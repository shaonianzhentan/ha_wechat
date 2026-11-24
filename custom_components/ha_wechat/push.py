"""推送服务对接。

对 HA 只暴露两件事：
  - 每台已配对手机一个在线实体（状态、额度和订阅信息）
  - 每台手机一个标准 notify 实体（notify.send_message 可直接选目标）

openid、额度池、订阅消息下发都在推送服务里，HA 只发 deviceId 和文案。
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import timedelta

import aiohttp
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.event import async_track_time_interval

from .const import DEFAULT_PUSH_BASE, DOMAIN
from .store import DeviceStore

_LOGGER = logging.getLogger(__name__)

POLL_INTERVAL = timedelta(minutes=5)
RETRY_INTERVAL = timedelta(seconds=25)
RETRY_MAX_AGE = 600
TIMEOUT = aiohttp.ClientTimeout(total=8)


class PushManager:
    def __init__(self, hass: HomeAssistant, store: DeviceStore) -> None:
        self.hass = hass
        self.store = store
        self.url = ""
        self.status: dict[str, dict] = {}
        self._unsub = None
        self._retry_unsub = None
        # 瞬时网络错误/5xx 的失败任务保留 10 分钟，后台退避重试
        self._queue: list[dict] = []

    # ---- 配置 ----------------------------------------------------------
    def update_options(self) -> None:
        # 推送地址内置；公有服务不再使用全局密钥，room 就是每套安装的凭据
        self.url = DEFAULT_PUSH_BASE.rstrip("/")

    @property
    def configured(self) -> bool:
        return bool(self.url)

    def configure(self, entry: ConfigEntry | None = None) -> None:
        self.update_options()
        if self.configured and self._unsub is None:
            self._unsub = async_track_time_interval(
                self.hass, self._async_tick, POLL_INTERVAL
            )
            self._retry_unsub = async_track_time_interval(
                self.hass, self._async_tick_retry, RETRY_INTERVAL
            )
            self.hass.async_create_task(self.async_poll())
        elif not self.configured and self._unsub is not None:
            self._unsub()
            self._unsub = None

    async def async_stop(self) -> None:
        if self._unsub is not None:
            self._unsub()
            self._unsub = None
        if self._retry_unsub is not None:
            self._retry_unsub()
            self._retry_unsub = None

    # ---- 状态 ----------------------------------------------------------
    async def _async_tick(self, now=None) -> None:
        await self.async_poll()

    async def _async_tick_retry(self, now=None) -> None:
        if not self._queue:
            return

        alive = []
        for item in list(self._queue):
            if time.time() - item["at"] > RETRY_MAX_AGE:
                _LOGGER.warning("推送任务超过 %s 秒仍未送达，已放弃", RETRY_MAX_AGE)
                continue
            try:
                await self._async_post(item["body"])
            except Exception as err:  # noqa: BLE001
                _LOGGER.debug("推送重试失败: %s", err)
                alive.append(item)
            else:
                _LOGGER.info("推送重试成功")
        self._queue = alive
        if not self._queue:
            self.hass.async_create_task(self.async_poll())

    async def async_poll(self) -> None:
        if not self.configured:
            return
        try:
            session = async_get_clientsession(self.hass)
            async with session.get(
                f"{self.url}/ha/status",
                params={"room": self.store.room},
                timeout=TIMEOUT,
            ) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    raise HomeAssistantError(f"HTTP {resp.status} {text[:120]}")
                data = await resp.json()
        except Exception as err:  # noqa: BLE001 - 轮询失败不应该影响集成
            _LOGGER.debug("查询推送状态失败: %s", err)
            return

        self.status = {}
        for item in data.get("devices") or []:
            ids = item.get("devices")
            if not isinstance(ids, list) or not ids:
                ids = [item.get("device")] if item.get("device") else []
            # 同一微信账号可能同时绑了多台手机；每台手机都能看到同一份额度/状态。
            for device_id in ids:
                if device_id:
                    self.status[str(device_id)] = item
        self.async_refresh_sensors()

    def info(self, device_id: str) -> dict:
        return self.status.get(device_id) or {}

    def quota(self, device_id: str):
        if not self.configured:
            return None
        return self.info(device_id).get("quota")

    def async_refresh_sensors(self) -> None:
        data = self.hass.data.get(DOMAIN, {})
        # 在线实体和标准 notify 实体都带额度/订阅属性，轮询后一起刷新。
        for key in ("phone_sensors", "phone_online_sensors", "phone_notify_entities"):
            for entity in (data.get(key) or {}).values():
                entity.async_write_ha_state()

    # ---- 推送 ----------------------------------------------------------
    async def _async_post(self, body: dict) -> dict:
        """带一次瞬时错误重试的 POST；5xx/网络错误会重试。"""
        last: Exception | None = None
        for attempt in range(2):
            try:
                session = async_get_clientsession(self.hass)
                async with session.post(
                    f"{self.url}/ha/notify",
                    json=body,
                    timeout=TIMEOUT,
                ) as resp:
                    payload = await resp.json(content_type=None)
                    if resp.status != 200 or not payload.get("ok"):
                        raise HomeAssistantError(
                            f"推送失败 HTTP {resp.status}：{payload.get('error') or '未知错误'}"
                        )
                    return payload.get("event") or {}
            except (aiohttp.ClientError, asyncio.TimeoutError, HomeAssistantError) as err:
                last = err
                # 4xx 通常是参数/额度问题，重试没意义；只重试服务端 5xx 与网络
                if isinstance(err, HomeAssistantError) and "HTTP 4" in str(err):
                    break
                if attempt == 0:
                    await asyncio.sleep(0.5)
        raise last or HomeAssistantError("推送失败")

    async def async_notify(
        self,
        *,
        devices: list[str] | None,
        title: str,
        message: str = "",
        page: str | None = None,
        area: str | None = None,
        data: dict | None = None,
        actions: list[dict] | None = None,
    ) -> dict:
        if not self.configured:
            raise HomeAssistantError(
                "推送服务未配置：请检查插件内置的推送地址"
            )
        if not devices:
            raise HomeAssistantError("请选择至少一台手机")

        body: dict = {"room": self.store.room, "title": title, "body": message or ""}
        body["devices"] = devices
        if page:
            body["page"] = page
        if area:
            body["area"] = area
        if isinstance(data, dict) and data:
            body["data"] = data
        if actions:
            body["actions"] = actions

        try:
            event = await self._async_post(body)
        except Exception as err:  # noqa: BLE001 - 网络抖动先入队，不让自动化直接失败
            transient = (
                isinstance(err, (aiohttp.ClientError, asyncio.TimeoutError))
                or "HTTP 5" in str(err)
            )
            if transient:
                if not any(item["body"] == body for item in self._queue):
                    self._queue.append({"body": body, "at": time.time()})
                    _LOGGER.warning("推送暂时失败，已加入重试队列: %s", err)
                return {"queued": True, "error": str(err)}
            raise

        self.hass.async_create_task(self.async_poll())
        return event

    async def async_forget(self, devices: list[str]) -> None:
        """设备解绑时清掉服务端的订阅，失败只记日志。"""
        if not self.configured or not devices:
            return
        try:
            session = async_get_clientsession(self.hass)
            async with session.post(
                f"{self.url}/ha/forget",
                json={"room": self.store.room, "devices": devices},
                timeout=TIMEOUT,
            ) as resp:
                if resp.status != 200:
                    _LOGGER.debug("清理推送订阅返回 %s", resp.status)
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("清理推送订阅失败: %s", err)
