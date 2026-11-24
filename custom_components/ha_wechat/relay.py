"""与中转服务的长连接：收发密文帧、推送状态。"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import time
from collections import deque
from collections.abc import Mapping
from contextlib import suppress
from datetime import timedelta

import aiohttp
from homeassistant.components.calendar import (
    DATA_COMPONENT as CALENDAR_COMPONENT,
    CalendarEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED, MATCH_ALL
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.event import (
    async_track_state_added_domain,
    async_track_state_change_event,
    async_track_time_interval,
)
from homeassistant.helpers.network import get_url
from homeassistant.util import dt as dt_util

from . import controls, extras
from .assist import (
    async_assistant_name,
    async_assistant_pipelines,
    async_assistant_result,
    async_assistant_voice,
)
from .const import (
    CONF_EXCLUDE_DOMAINS,
    CONF_EXCLUDE_ENTITIES,
    CONF_INCLUDE_DOMAINS,
    CONF_INCLUDE_ENTITIES,
    DEFAULT_RELAY,
    DOMAIN,
    FAIL_LOG,
)

from .crypto import DIR_HA, FrameError, decode, encode, parse_header
from .entity import EntityHelper, area_of
from .guard import validate_service
from .manifest import manifest
from .store import DeviceStore, async_get_store
from .util import async_on_paired

_LOGGER = logging.getLogger(__name__)

BATCH_WINDOW = 0.3
MAX_BACKOFF = 60
SAVE_DELAY = 1.0
PAGE_SIZE = 40
MAX_ENTITIES = 500

# 小程序每 45 秒发一次 heartbeat；90 秒收不到就视为离线
PRESENCE_TICK = 30
REGISTRY_DEBOUNCE = 1.5

# 详情页的历史曲线只保留本次连接内的采样，不依赖 recorder。
HISTORY_LIMIT = 144
HISTORY_MIN_INTERVAL = 5
HISTORY_SAME_INTERVAL = 60
HISTORY_DOMAINS = frozenset(
    {"sensor", "number", "input_number", "counter", "binary_sensor", "weather"}
)

# 注册表事件名用字符串，避免不同 HA 版本里常量导入位置变化
REGISTRY_EVENTS = (
    "entity_registry_updated",
    "device_registry_updated",
    "area_registry_updated",
)


def _option_set(options: Mapping[str, object], key: str) -> set[str]:
    """选项里的多选值统一成 set；兼容字符串、列表和旧版单值。"""
    value = options.get(key)
    if isinstance(value, str):
        return {value} if value else set()
    if isinstance(value, (list, tuple, set, frozenset)):
        return {str(item) for item in value if item}
    return set()


def entity_selected(
    entity_id: str, options: Mapping[str, object] | None = None
) -> bool:
    """按集成选项判断实体是否进入同步列表。

    - include_entities 最具体，可覆盖 exclude_domains；
    - exclude_entities 优先级最高；
    - include_domains 非空时，只同步这些域，include_entities 可额外追加；
    - 两个 include 都为空时同步全部。
    """
    if not options:
        return True

    include_domains = _option_set(options, CONF_INCLUDE_DOMAINS)
    exclude_domains = _option_set(options, CONF_EXCLUDE_DOMAINS)
    include_entities = _option_set(options, CONF_INCLUDE_ENTITIES)
    # 兼容旧版选项：mode=selected + entities 等价于 include_entities
    if (
        CONF_INCLUDE_ENTITIES not in options
        and options.get("mode") == "selected"
    ):
        include_entities = _option_set(options, "entities")
    exclude_entities = _option_set(options, CONF_EXCLUDE_ENTITIES)
    domain = entity_id.split(".", 1)[0]

    if entity_id in exclude_entities:
        return False
    if entity_id in include_entities:
        return True
    if domain in exclude_domains:
        return False
    if include_domains:
        return domain in include_domains
    if include_entities:
        return False
    return True


def resolve_entities(
    hass: HomeAssistant, options: Mapping[str, object] | None = None
) -> list[str]:
    """按集成选项同步实体。

    跳过隐藏和禁用实体；按「区域 + 名称」排序，手机端分组顺序稳定。
    先筛选再限量，上限 500 条，避免一次推太多。
    """
    registry = er.async_get(hass)
    states = []
    for state in hass.states.async_all():
        entity_id = state.entity_id
        if not entity_selected(entity_id, options):
            continue
        entry = registry.async_get(entity_id)
        if entry is not None and (entry.disabled_by or entry.hidden_by):
            continue
        states.append(state)

    # 按「区域 + 名称」排序，手机端分组顺序才稳定（没区域的排最后）
    states.sort(
        key=lambda s: (
            area_of(hass, s.entity_id) or "\uffff",
            s.attributes.get("friendly_name") or s.entity_id,
        )
    )
    ids = [state.entity_id for state in states]
    if len(ids) > MAX_ENTITIES:
        _LOGGER.warning(
            "实体数量 %s 超过上限 %s，多余的不会同步", len(ids), MAX_ENTITIES
        )
        ids = ids[:MAX_ENTITIES]
    return ids


class WeChatRelay:
    def __init__(
        self, hass: HomeAssistant, entry: ConfigEntry | None, store: DeviceStore
    ) -> None:
        self.hass = hass
        self.entry = entry
        self.store = store
        self.relay = DEFAULT_RELAY
        self.entities: list[str] = resolve_entities(
            hass, entry.options if entry is not None else None
        )
        self.connected = False
        # 中转会下发当前在线的 deviceId 列表；HA 只给列表里的设备发密文帧。
        # 未收到列表时按没有小程序在线处理，不向空房间发送数据。
        self.online_devices: set[str] = set()
        self.last_message: float | None = None

        self._helper = EntityHelper(hass)
        self._task: asyncio.Task | None = None
        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._unsub = None
        self._presence_timer = None
        self._startup_unsub = None
        self._startup_timer: asyncio.TimerHandle | None = None
        self._state_added_unsub = None
        self._state_added_timer: asyncio.TimerHandle | None = None
        self._registry_unsubs: list = []
        self._registry_debounce: asyncio.TimerHandle | None = None
        self._catalog_timer: asyncio.TimerHandle | None = None
        self._flush: asyncio.TimerHandle | None = None
        self._save: asyncio.TimerHandle | None = None
        self._dirty: set[str] = set()
        # 每台设备订阅了哪些实体的详情（只推订阅过的控件树）
        self._watch: dict[str, set[str]] = {}
        # P1 详情缓存与本次连接内的状态采样
        self._extras_cache: dict[str, tuple[float, dict]] = {}
        self._history: dict[str, deque] = {}
        self._history_last: dict[str, tuple[float, object]] = {}

    @property
    def url(self) -> str:
        return self.relay

    @property
    def headers(self) -> dict[str, str]:
        # 房间号走请求头，不进 URL，避免被代理访问日志记录
        return {"X-HA-Token": self.store.room, "X-HA-Role": "ha"}

    async def async_start(self) -> None:
        if self.entities:
            self._unsub = async_track_state_change_event(
                self.hass, self.entities, self._state_changed
            )
        # 启动时 zone/person 等平台可能还没建立状态，等 HA 启动完成后再补一次
        if self.hass.is_running:
            self._startup_timer = self.hass.loop.call_later(
                1.0, self._startup_refresh
            )
        else:
            self._startup_unsub = self.hass.bus.async_listen_once(
                EVENT_HOMEASSISTANT_STARTED, self._startup_refresh
            )
        # zone 等不写入实体注册表的平台，启动后新增状态也要纳入同步
        self._state_added_unsub = async_track_state_added_domain(
            self.hass, MATCH_ALL, self._state_added
        )
        self._presence_timer = async_track_time_interval(
            self.hass, self._presence_tick, timedelta(seconds=PRESENCE_TICK)
        )
        # 实体、设备、区域注册表变化后自动重建同步列表，不需要手动重载集成
        self._registry_unsubs = [
            self.hass.bus.async_listen(event, self._registry_changed)
            for event in REGISTRY_EVENTS
        ]
        self._task = self.hass.async_create_background_task(
            self._async_run(), "ha_wechat relay"
        )

    async def async_wait_connected(self, timeout: float = 5.0) -> bool:
        """等待中转连接建立，避免二维码先于 relay 就绪。"""
        if self.connected:
            return True
        deadline = self.hass.loop.time() + timeout
        while not self.connected and self.hass.loop.time() < deadline:
            await asyncio.sleep(0.2)
        return self.connected

    async def async_stop(self) -> None:
        if self._unsub:
            self._unsub()
            self._unsub = None
        if self._startup_unsub:
            self._startup_unsub()
            self._startup_unsub = None
        if self._startup_timer:
            self._startup_timer.cancel()
            self._startup_timer = None
        if self._state_added_unsub:
            self._state_added_unsub()
            self._state_added_unsub = None
        if self._state_added_timer:
            self._state_added_timer.cancel()
            self._state_added_timer = None
        if self._presence_timer:
            self._presence_timer()
            self._presence_timer = None
        for unsub in self._registry_unsubs:
            unsub()
        self._registry_unsubs = []
        if self._registry_debounce:
            self._registry_debounce.cancel()
            self._registry_debounce = None
        if self._catalog_timer:
            self._catalog_timer.cancel()
            self._catalog_timer = None
        if self._flush:
            self._flush.cancel()
            self._flush = None
        if self._save:
            self._save.cancel()
            self._save = None
        if self._task:
            self._task.cancel()
            with suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        if self._ws and not self._ws.closed:
            await self._ws.close()
        self.connected = False
        if self.store.devices:
            await self.store.async_save()

    def is_online(self, device_id: str) -> bool:
        """已配对、HA 中转已连接，且中转报告这台设备在线。"""
        return self.connected and device_id in self.online_devices

    def online_ids(self) -> list[str]:
        return [
            device_id
            for device_id in self.store.devices
            if self.is_online(device_id)
        ]

    @callback
    def _presence_tick(self, _now=None) -> None:
        self._refresh_presence()

    @callback
    def _startup_refresh(self, _event=None) -> None:
        self._startup_unsub = None
        if self._startup_timer is not None:
            self._startup_timer.cancel()
            self._startup_timer = None
        self.hass.async_create_task(self._async_startup_refresh())

    async def _async_startup_refresh(self) -> None:
        await self.async_update_entities()
        # 启动阶段实体是一个个恢复的，等列表稳定后再推 catalog，避免只推出去前几个。
        self._schedule_catalog(2.0)

    @callback
    def _schedule_catalog(self, delay: float = 1.5) -> None:
        if self._catalog_timer is not None:
            self._catalog_timer.cancel()
        self._catalog_timer = self.hass.loop.call_later(delay, self._catalog_refresh)

    @callback
    def _catalog_refresh(self) -> None:
        self._catalog_timer = None
        if self.connected and self.online_devices:
            self.hass.async_create_task(self.async_broadcast_catalog())

    @callback
    def _state_added(self, event) -> None:
        state = event.data.get("new_state")
        if state is None:
            return
        options = self.entry.options if self.entry is not None else None
        if not entity_selected(state.entity_id, options):
            return
        if self._state_added_timer is not None:
            return
        self._state_added_timer = self.hass.loop.call_later(
            0.5, self._state_added_refresh
        )

    @callback
    def _state_added_refresh(self) -> None:
        self._state_added_timer = None
        self.hass.async_create_task(self._async_startup_refresh())

    @callback
    def _refresh_presence(self) -> None:
        data = self.hass.data.get(DOMAIN, {})
        for key in ("refresh_phone_sensors", "refresh_phone_online"):
            refresh = data.get(key)
            if refresh:
                refresh()

    @callback
    def _registry_changed(self, _event) -> None:
        # 一次改名/换区域可能连续触发多个事件，合并成一次重建
        if self._registry_debounce:
            self._registry_debounce.cancel()
        self._registry_debounce = self.hass.loop.call_later(
            REGISTRY_DEBOUNCE, self._registry_refresh
        )

    @callback
    def _registry_refresh(self) -> None:
        self._registry_debounce = None
        self.hass.async_create_task(self._async_registry_refresh())

    async def _async_registry_refresh(self) -> None:
        await self.async_update_entities()
        _LOGGER.debug("注册表变化，已重建同步实体: %s", len(self.entities))
        self._schedule_catalog(1.5)

    async def async_update_entities(self, entities: list[str] | None = None) -> None:
        if entities is None:
            options = self.entry.options if self.entry is not None else None
            entities = resolve_entities(self.hass, options)
        self.entities = list(entities)
        allowed = set(self.entities)
        self._watch = {
            device_id: watched & allowed
            for device_id, watched in self._watch.items()
        }
        if self._unsub:
            self._unsub()
            self._unsub = None
        if self.entities:
            self._unsub = async_track_state_change_event(
                self.hass, self.entities, self._state_changed
            )
        # 同步列表变化后刷新聚合传感器上的 entities 属性
        refresh = self.hass.data.get(DOMAIN, {}).get("refresh_phone_sensors")
        if refresh:
            refresh()

    async def async_bind(self, entry: ConfigEntry) -> None:
        """配对期间已经建立的连接，在创建 config entry 后接管。"""
        self.entry = entry
        await self.async_update_entities()

    # ---- 连接 ----------------------------------------------------------
    async def _async_run(self) -> None:
        backoff = 1
        session = async_get_clientsession(self.hass)

        while True:
            try:
                async with session.ws_connect(
                    self.url, headers=self.headers, heartbeat=25, max_msg_size=0
                ) as ws:
                    self._ws = ws
                    self.connected = True
                    # 中转连接后会立即下发当前在线设备列表；在此之前按无小程序处理。
                    self.online_devices = set()
                    self._refresh_presence()
                    backoff = 1
                    _LOGGER.info("已连接中转服务")

                    async for msg in ws:
                        if msg.type == aiohttp.WSMsgType.BINARY:
                            self.last_message = time.time()
                            self._on_frame(msg.data)
                        elif msg.type == aiohttp.WSMsgType.TEXT:
                            self._on_text(msg.data)
                        elif msg.type == aiohttp.WSMsgType.ERROR:
                            break
            except asyncio.CancelledError:
                raise
            except Exception as err:  # noqa: BLE001 - 任何异常都应重试
                _LOGGER.warning("中转连接异常: %s", err)
            finally:
                self.connected = False
                self.online_devices = set()
                self._ws = None
                self._refresh_presence()

            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, MAX_BACKOFF)

    # ---- 收帧 ----------------------------------------------------------
    @callback
    def _on_text(self, raw: str) -> None:
        """处理中转的房间在线状态，不把文本帧当成设备密文。"""
        try:
            payload = json.loads(raw)
        except (TypeError, ValueError):
            return
        if not isinstance(payload, dict) or payload.get("type") != "sys":
            return
        data = payload.get("data") or {}
        raw_devices = data.get("devices")
        devices = {
            str(item).lower()
            for item in raw_devices
            if isinstance(item, str) and item
        } if isinstance(raw_devices, list) else set()
        if devices == self.online_devices:
            return
        self.online_devices = devices
        if devices:
            # 新设备上线或重连后，等实体数量稳定再补一帧完整 catalog；
            # 无论是否首次连接都要补，否则新加入的小程序会一直停留在 join 时的旧列表。
            self._schedule_catalog(2.0)
        self._refresh_presence()
        _LOGGER.info("在线设备: %s", ", ".join(sorted(devices)) or "-")

    @callback
    def _on_frame(self, raw: bytes) -> None:
        try:
            device_id, counter = parse_header(raw)
        except FrameError:
            return

        device = self.store.get(device_id)
        if device is None:
            return
        # 计数器必须严格递增，重放帧直接丢弃
        if counter <= device["recv"]:
            return

        try:
            payload, counter = decode(self.store.room, device_id, device["key"], raw)
        except FrameError:
            device["fail"] = device.get("fail", 0) + 1
            self._schedule_save()
            if device["fail"] == FAIL_LOG:
                _LOGGER.warning("设备 %s 连续解密失败，请检查是否为伪造流量", device_id)
            return

        device["recv"] = counter
        device["fail"] = 0
        device["last_seen"] = time.time()
        self._schedule_save()
        self._refresh_presence()

        first = not device.get("active")
        if first:
            device["active"] = True
            self.hass.async_create_task(self._async_paired(device_id))

        self.hass.async_create_task(self._async_handle(device_id, payload))

    async def _async_paired(self, device_id: str) -> None:
        device = self.store.get(device_id)
        _LOGGER.info("设备已配对: %s", device.get("name") if device else device_id)
        await async_on_paired(self.hass, device or {})
        await self.store.async_save()
        data = self.hass.data.get(DOMAIN, {})
        for key in ("add_phone_online", "add_phone_notify"):
            add_entity = data.get(key)
            if add_entity:
                add_entity(device_id)
        self._refresh_presence()

    # ---- 指令 ----------------------------------------------------------
    async def _async_handle(self, device_id: str, payload: dict) -> None:
        request_id = payload.get("id")
        msg_type = payload.get("type")
        data = payload.get("data") or {}

        try:
            result = await self._async_dispatch(device_id, msg_type, data)
        except Exception as err:  # noqa: BLE001 - 回传给设备而不是静默失败
            _LOGGER.exception("处理指令失败: %s", msg_type)
            if request_id:
                await self._async_reply(device_id, request_id, {"type": "error", "data": {"message": str(err)}})
            return

        if request_id and result is not None:
            await self._async_reply(device_id, request_id, {"type": msg_type, "data": result})

    async def _async_dispatch(self, device_id: str, msg_type: str, data: dict) -> dict | str | None:
        if msg_type == "join":
            return await self._async_join(device_id, data)
        if msg_type == "sync":
            return await self._async_cards(0)
        if msg_type == "page":
            offset = int(data.get("offset") or 0)
            return await self._async_cards(max(0, offset))
        if msg_type == "watch":
            ids = data.get("ids") or []
            self._watch[device_id] = {
                str(i) for i in ids if str(i) in set(self.entities)
            }
            return {"ok": True}
        if msg_type == "entity":
            entity_id = str(data.get("id") or "")
            if entity_id not in self.entities:
                raise ValueError("实体不在同步列表里")
            detail = await self._async_detail(entity_id)
            if detail is None:
                raise ValueError("实体不存在")
            return detail
        if msg_type == "map":
            return await self._async_map()
        if msg_type == "map_zone_update":
            return await self._async_zone_update(data)
        if msg_type == "call_service":
            return await self._async_call_service(data)
        if msg_type == "calendar_event_update":
            return await self._async_calendar_event_update(data)
        if msg_type == "calendar_event_delete":
            return await self._async_calendar_event_delete(data)
        if msg_type == "conversation":
            result = await async_assistant_result(self.hass, data.get("text", ""), data.get("pipeline"))
            return result
        if msg_type == "voice":
            raw = data.get("audio")
            if isinstance(raw, str):
                try:
                    audio = base64.b64decode(raw)
                except (ValueError, TypeError) as err:
                    raise ValueError("录音格式无效") from err
            elif isinstance(raw, bytes):
                audio = raw
            else:
                audio = b""
            if not audio:
                raise ValueError("录音为空")
            return await async_assistant_voice(self.hass, audio, data.get("pipeline"))
        if msg_type == "assistant":
            return {
                "name": await async_assistant_name(self.hass),
                "pipelines": await async_assistant_pipelines(self.hass),
            }
        if msg_type == "heartbeat":
            # 只为刷新 last_seen；无 id 时不会回帧
            return {"ok": True}
        raise ValueError(f"未知指令: {msg_type}")

    async def _async_detail(self, entity_id: str) -> dict | None:
        """实体详情统一入口：控件树 + P1 扩展 + 历史采样。"""
        detail = await self._helper.async_detail(entity_id)
        if detail is None:
            return None
        try:
            detail = await extras.async_enrich(
                self.hass, self._helper, entity_id, detail, self._extras_cache
            )
        except Exception as err:  # noqa: BLE001 - 扩展失败不能影响基础详情
            _LOGGER.debug("详情扩展失败 %s: %s", entity_id, err)
        history = self._history_for(entity_id)
        if history:
            detail["history"] = history
        return detail

    async def _async_cards(self, offset: int = 0) -> dict:
        """分页下发卡片，实体很多时也不会撑爆单帧。

        `next` 是下一页该传的 offset：客户端照着传，不要自己数条数
        （某些实体可能取不到 state 被跳过，条数和下标不是一回事）。
        """
        offset = max(0, offset)
        slice_ids = self.entities[offset : offset + PAGE_SIZE]
        entities = []
        for entity_id in slice_ids:
            card = await self._helper.async_card(entity_id)
            if card is not None:
                entities.append(card)
        next_offset = offset + len(slice_ids)
        return {
            "entities": entities,
            "offset": offset,
            "next": next_offset,
            "total": len(self.entities),
            "more": next_offset < len(self.entities),
        }

    async def async_broadcast_catalog(self) -> None:
        """注册表或集成选项变化后，让小程序重置实体列表。"""
        if not self.connected or not self.online_devices:
            return
        cards = await self._async_cards(0)
        await self._async_broadcast(
            {"id": "", "type": "catalog", "data": cards}
        )

    async def _async_join(self, device_id: str, data: dict) -> dict:
        name = str(data.get("device") or "").strip()
        if name:
            device = self.store.get(device_id)
            if device is not None:
                device["name"] = name[:32]
                self._schedule_save()
                refresh_names = self.hass.data.get(DOMAIN, {}).get("refresh_phone_names")
                if refresh_names:
                    refresh_names()

        # 刚启动时实体状态可能还在恢复；join 前重建一次，避免把少量实体当成完整目录。
        await self.async_update_entities()
        cards = await self._async_cards(0)
        # join 可能发生在 HA 启动早期，先给客户端一页；实体稳定后再补一帧完整 catalog。
        self._schedule_catalog(2.0)

        return {
            "schema": 6,
            "name": self.hass.config.location_name or "我的家",
            "assistant": await async_assistant_name(self.hass),
            "pipelines": await async_assistant_pipelines(self.hass),
            "host": get_url(self.hass),
            "version": manifest.version,
            **cards,
        }

    async def _async_map(self) -> dict:
        """地图数据只取同步范围内的实体，避免绕过白名单泄露其它位置实体。"""
        entities = []
        for entity_id in self.entities:
            state = self.hass.states.get(entity_id)
            if state is None or "latitude" not in state.attributes:
                continue
            card = await self._helper.async_card(entity_id)
            if card is None:
                continue
            detail = await self._async_detail(entity_id)
            if detail is not None:
                card["geo"] = detail.get("geo")
            if entity_id.startswith("person."):
                card["trackers"] = self._person_trackers(state)
            entities.append(card)
        return {"entities": entities}

    def _person_trackers(self, state) -> list[dict]:
        """人员关联的 device_tracker，用于在人员卡片上快速识别设备。"""
        raw_trackers = state.attributes.get("device_trackers") or []
        if isinstance(raw_trackers, str):
            raw_trackers = [raw_trackers]
        trackers = []
        for tracker_id in raw_trackers:
            tracker_id = str(tracker_id)
            if not tracker_id.startswith("device_tracker."):
                continue
            tracker = self.hass.states.get(tracker_id)
            if tracker is None:
                continue
            trackers.append(
                {
                    "id": tracker_id,
                    "name": tracker.attributes.get("friendly_name") or tracker_id,
                }
            )
        return trackers

    async def _async_zone_update(self, payload: dict) -> dict:
        """把小程序里拖动后的区域中心/半径写回 HA。

        - zone.home 改的是 HA 核心家庭位置，走 config.async_update；
        - 其它 UI 创建的 zone 走 zone 的 storage collection；
        - YAML 定义的 zone 在 HA 中本身不可 UI 编辑，直接拒绝。
        """
        entity_id = str(payload.get("id") or "")
        if not entity_id.startswith("zone."):
            raise ValueError("只能编辑 zone 实体")
        if entity_id not in self.entities:
            raise ValueError("区域不在同步列表里")

        state = self.hass.states.get(entity_id)
        if state is None:
            raise ValueError("区域不存在")
        if state.attributes.get("editable") is False:
            raise ValueError("该区域由 YAML 定义，不能在 App 内编辑")

        try:
            latitude = float(payload.get("latitude"))
            longitude = float(payload.get("longitude"))
            radius = float(payload.get("radius"))
        except (TypeError, ValueError) as err:
            raise ValueError("区域坐标或半径无效") from err
        if not -90 <= latitude <= 90:
            raise ValueError("纬度超出范围")
        if not -180 <= longitude <= 180:
            raise ValueError("经度超出范围")
        if not 1 <= radius <= 100000:
            raise ValueError("半径需要在 1 到 100000 米之间")

        # 小程序地图中心是 GCJ-02，前端已反算成 WGS-84 再发过来，这里只做范围校验。
        update = {
            "latitude": latitude,
            "longitude": longitude,
            "radius": radius,
        }

        if entity_id == "zone.home":
            await self.hass.config.async_update(**update)
        else:
            from homeassistant.components.zone import DATA_ZONE  # noqa: PLC0415

            zone_data = self.hass.data.get(DATA_ZONE)
            if zone_data is None:
                raise ValueError("zone 集成没有加载")
            zone_entity = zone_data.component.get_entity(entity_id)
            if zone_entity is None:
                raise ValueError("区域实体不存在")
            if not getattr(zone_entity, "editable", False):
                raise ValueError("该区域不可编辑")
            zone_id = zone_entity.unique_id
            if not zone_id:
                raise ValueError("区域缺少存储 ID")
            # 只更新需要变化的字段，避免覆盖 HA 里其它 zone 属性。
            await zone_data.storage_collection.async_update_item(zone_id, update)

        return {"ok": True, "geo": update}

    async def _async_call_service(self, payload: dict) -> dict:
        domain, name, body = validate_service(
            self.entities, payload, self.hass.services.has_service
        )
        if domain == "calendar" and name == "create_event":
            targets = body.get("entity_id")
            if isinstance(targets, str):
                targets = [targets]
            for entity_id in targets or []:
                entity = self._calendar_entity(entity_id)
                self._require_calendar_feature(
                    entity, CalendarEntityFeature.CREATE_EVENT, "新建"
                )
        # blocking=True 让服务内部抛出的异常回到这里，由 _async_handle 回 error；
        # 否则小程序会在 HA 执行失败时仍然显示成功。
        await self.hass.services.async_call(domain, name, body, blocking=True)
        if domain in ("todo", "calendar"):
            # 待办条目和日历事件不在实体状态里，详情是单独缓存；操作后必须立刻失效，
            # 否则勾选/删除后 180 秒内还会推旧清单。
            target = body.get("entity_id")
            targets = [target] if isinstance(target, str) else target or []
            for entity_id in targets:
                self._extras_cache.pop(f"{domain}:{entity_id}", None)
        return {"ok": True}

    def _calendar_entity(self, entity_id: str):
        component = self.hass.data.get(CALENDAR_COMPONENT)
        entity = component.get_entity(entity_id) if component is not None else None
        if entity is None:
            raise ValueError("日历实体不可用")
        return entity

    def _require_calendar_feature(self, entity, feature, action: str) -> None:
        features = int(getattr(entity, "supported_features", 0) or 0)
        if not features & int(feature):
            raise ValueError(f"这个日历不支持{action}日程")

    def _calendar_event_from_payload(self, payload: dict) -> dict:
        summary = str(payload.get("summary") or "").strip()
        if not summary:
            raise ValueError("日程标题不能为空")

        all_day = bool(payload.get("start_date")) and not bool(
            payload.get("start_date_time")
        )
        if all_day:
            start = dt_util.parse_date(payload.get("start_date"))
            end = dt_util.parse_date(payload.get("end_date"))
        else:
            start = dt_util.parse_datetime(
                str(payload.get("start_date_time") or "").strip().replace(" ", "T", 1)
            )
            end = dt_util.parse_datetime(
                str(payload.get("end_date_time") or "").strip().replace(" ", "T", 1)
            )
        if start is None or end is None:
            raise ValueError("日程开始或结束时间无效")
        if isinstance(start, datetime):
            # 小程序发的是本地墙上时间（无时区），不能交给 as_local 按 UTC 解释；
            # 先补 HA 本地时区，再统一转成 HA 本地时区。
            local_tz = dt_util.get_default_timezone()
            start = (
                start.replace(tzinfo=local_tz)
                if start.tzinfo is None
                else dt_util.as_local(start)
            )
            end = (
                end.replace(tzinfo=local_tz)
                if end.tzinfo is None
                else dt_util.as_local(end)
            )
        if end <= start:
            raise ValueError("结束时间必须晚于开始时间")

        return {
            "summary": summary,
            "description": str(payload.get("description") or "").strip(),
            "location": str(payload.get("location") or "").strip(),
            "dtstart": start,
            "dtend": end,
        }

    async def _async_calendar_event_update(self, payload: dict) -> dict:
        entity_id = str(payload.get("entity_id") or "")
        if entity_id not in self.entities:
            raise ValueError("实体不在同步列表里")
        uid = str(payload.get("uid") or "").strip()
        if not uid:
            raise ValueError("日程缺少 uid")
        entity = self._calendar_entity(entity_id)
        self._require_calendar_feature(
            entity, CalendarEntityFeature.UPDATE_EVENT, "修改"
        )
        event = self._calendar_event_from_payload(payload)
        try:
            await entity.async_update_event(uid, event)
        except NotImplementedError as err:
            raise ValueError("这个日历不支持修改日程") from err
        self._extras_cache.pop(f"calendar:{entity_id}", None)
        return {"ok": True}

    async def _async_calendar_event_delete(self, payload: dict) -> dict:
        entity_id = str(payload.get("entity_id") or "")
        if entity_id not in self.entities:
            raise ValueError("实体不在同步列表里")
        uid = str(payload.get("uid") or "").strip()
        if not uid:
            raise ValueError("日程缺少 uid")
        entity = self._calendar_entity(entity_id)
        self._require_calendar_feature(
            entity, CalendarEntityFeature.DELETE_EVENT, "删除"
        )
        try:
            await entity.async_delete_event(uid)
        except NotImplementedError as err:
            raise ValueError("这个日历不支持删除日程") from err
        self._extras_cache.pop(f"calendar:{entity_id}", None)
        return {"ok": True}


    # ---- 发帧 ----------------------------------------------------------
    async def _async_reply(self, device_id: str, request_id: str, obj: dict) -> None:
        obj["id"] = request_id
        await self._async_send(device_id, obj)

    async def _async_send(self, device_id: str, obj: dict) -> None:
        ws = self._ws
        device = self.store.get(device_id)
        if (
            ws is None
            or ws.closed
            or device is None
            or not device.get("active")
            or device_id not in self.online_devices
        ):
            return

        sequence = device.get("sent", 0) + 1
        frame = encode(DIR_HA, self.store.room, device_id, device["key"], sequence, obj)
        try:
            await ws.send_bytes(frame)
        except ConnectionResetError:
            return

        device["sent"] = sequence
        self._schedule_save()

    async def _async_broadcast(self, obj: dict) -> None:
        if not self.online_devices:
            return
        for device_id in list(self.store.devices):
            if device_id in self.online_devices:
                await self._async_send(device_id, obj)

    # ---- 状态 ----------------------------------------------------------
    @callback
    def _state_changed(self, event) -> None:
        entity_id = event.data.get("entity_id")
        if not entity_id:
            return
        self._record_history(entity_id, event.data.get("new_state"))
        self._dirty.add(entity_id)
        if self._flush is None:
            self._flush = self.hass.loop.call_later(BATCH_WINDOW, self._flush_states)

    def _record_history(self, entity_id: str, state_obj) -> None:
        """记一条内存采样供详情页画曲线；不依赖 recorder，重启即清空。"""
        if entity_id not in self.entities or controls.domain_of(entity_id) not in HISTORY_DOMAINS:
            return
        if state_obj is None or state_obj.state in ("unknown", "unavailable"):
            return
        value = controls.num(state_obj.state)
        domain = controls.domain_of(entity_id)
        if value is None and domain == "binary_sensor":
            value = 1 if state_obj.state in ("on", "home", "open", "detected") else 0
        if value is None and domain == "weather":
            value = controls.num(state_obj.attributes.get("temperature"))
        if value is None:
            return
        if isinstance(value, bool):
            value = 1 if value else 0

        now = time.time()
        last = self._history_last.get(entity_id)
        if last and now - last[0] < HISTORY_MIN_INTERVAL:
            return

        store = self._history.get(entity_id)
        if store is None:
            store = deque(maxlen=HISTORY_LIMIT)
            self._history[entity_id] = store
        store.append({"at": round(now, 1), "value": float(value)})
        self._history_last[entity_id] = (now, value)

    def _history_for(self, entity_id: str) -> dict | None:
        store = self._history.get(entity_id)
        if not store:
            state_obj = self.hass.states.get(entity_id)
            if state_obj is not None:
                self._record_history(entity_id, state_obj)
            store = self._history.get(entity_id)
        if not store:
            return None

        points = list(store)
        values = [p["value"] for p in points]
        state_obj = self.hass.states.get(entity_id)
        unit = ""
        if state_obj is not None:
            unit = state_obj.attributes.get("unit_of_measurement") or ""
            if controls.domain_of(entity_id) == "weather":
                unit = state_obj.attributes.get("temperature_unit") or "°C"
        return {
            "points": points,
            "min": min(values),
            "max": max(values),
            "avg": round(sum(values) / len(values), 2),
            "last": values[-1],
            "first": values[0],
            "unit": unit,
            "count": len(values),
            "window": int(time.time() - points[0]["at"]) if points else 0,
        }

    def _flush_states(self) -> None:
        self._flush = None
        if not self._dirty:
            return
        entity_ids = list(self._dirty)
        self._dirty.clear()
        self.hass.async_create_task(self._async_push_states(entity_ids))

    async def _async_push_states(self, entity_ids: list[str]) -> None:
        if not self.connected or not self.online_devices:
            return

        cards = []
        allowed = set(self.entities)
        for entity_id in entity_ids:
            if entity_id not in allowed:
                continue
            card = await self._helper.async_card(entity_id)
            if card is not None:
                cards.append(card)
        if not cards:
            return

        await self._async_broadcast(
            {"id": "", "type": "state_changed", "data": {"entities": cards}}
        )

        # 只给订阅了详情的设备推控件树，避免把全部属性广播给所有人
        for device_id, watched in list(self._watch.items()):
            hit = watched & set(entity_ids)
            if not hit:
                continue
            details = []
            for entity_id in hit:
                detail = await self._async_detail(entity_id)
                if detail is not None:
                    details.append(detail)
            if details:
                await self._async_send(
                    device_id, {"id": "", "type": "detail", "data": {"entities": details}}
                )

    def _schedule_save(self) -> None:
        if self._save is not None:
            return
        self._save = self.hass.loop.call_later(SAVE_DELAY, self._save_now)

    def _save_now(self) -> None:
        self._save = None
        self.hass.async_create_task(self.store.async_save())


async def async_ensure_relay(
    hass: HomeAssistant, entry: ConfigEntry | None = None
) -> WeChatRelay:
    """拿到（必要时启动）到中转的连接。

    添加集成时就要连上，否则小程序首帧密文没人解，配对会一直等不到。
    """
    data = hass.data.setdefault(DOMAIN, {})
    relay = data.get("relay")
    store = await async_get_store(hass)

    if relay is None:
        relay = WeChatRelay(hass, entry, store)
        data["relay"] = relay
        await relay.async_start()
    elif entry is not None:
        await relay.async_bind(entry)

    return relay
