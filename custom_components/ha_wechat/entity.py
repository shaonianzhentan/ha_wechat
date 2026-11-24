"""实体 → 卡片 / 详情。

只做 Home Assistant 侧的原始数据读取：区域解析、实体状态/属性原样下发、
把 state 交给 `controls` 生成控件树。中文展示名全部由小程序内置词表处理，
这里不再调用 Home Assistant 的翻译资源。
"""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from . import controls


def _domain(entity_id: str) -> str:
    return controls.domain_of(entity_id)


def area_of(hass: HomeAssistant, entity_id: str) -> str:
    """实体所属区域名：优先设备所在区域，其次实体自己的区域。"""
    entity_registry = er.async_get(hass)
    device_registry = dr.async_get(hass)
    area_registry = ar.async_get(hass)

    entry = entity_registry.async_get(entity_id)
    area_id = None
    if entry is not None:
        if entry.device_id:
            device = device_registry.async_get(entry.device_id)
            if device is not None:
                area_id = device.area_id
        if not area_id:
            area_id = entry.area_id

    if area_id and area_id in area_registry.areas:
        return area_registry.areas[area_id].name
    return ""


class EntityHelper:
    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass

    def _area(self, entity_id: str) -> str:
        return area_of(self.hass, entity_id)

    def _icon(self, entity_id: str, attrs: dict) -> str | None:
        """和 HA 前端一致：实体属性 icon 优先，其次实体注册表里的自定义图标。"""
        icon = attrs.get("icon")
        if icon:
            return str(icon)
        entry = er.async_get(self.hass).async_get(entity_id)
        return entry.icon if entry is not None else None

    def _available(self, state_obj) -> bool:
        # unknown 只是暂时读不到值，仍然是一个正常实体；
        # 只有 unavailable 才代表设备/实体不可用，卡片才需要变淡。
        return state_obj.state != "unavailable"

    async def async_card(self, entity_id: str) -> dict | None:
        """列表用的卡片：字段少，开销小。"""
        state_obj = self.hass.states.get(entity_id)
        if state_obj is None:
            return None

        state = state_obj.state
        attrs = state_obj.attributes
        flags = controls.card_flags(
            entity_id,
            state,
            attrs,
            time_zone=self.hass.config.time_zone,
        )

        card = {
            "id": entity_id,
            "name": attrs.get("friendly_name", entity_id),
            "area": self._area(entity_id),
            "domain": _domain(entity_id),
            "device_class": attrs.get("device_class"),
            "icon": self._icon(entity_id, attrs),
            "state": state,
            "available": self._available(state_obj),
            "on": state in controls.ON_STATES,
            "toggle": flags["toggle"],
            "action": flags["action"],
            "value": flags["value"],
            "unit": flags["unit"],
            "stateName": "",
        }

        # 有数值并且没有开关时，用数值当主标题，避免卡片上出现三行字
        if flags["value"] is not None and flags["toggle"] is None:
            card["stateName"] = ""
        elif flags["action"] is not None and flags["toggle"] is None:
            # 场景/脚本/按钮的 state 是“上次触发时间”这类噪音，卡片上不显示
            card["stateName"] = ""
        else:
            # 原样返回 state，中文状态名由小程序翻译
            card["stateName"] = state

        return card

    async def async_detail(self, entity_id: str) -> dict | None:
        """详情：卡片 + 控件树 + 属性摘要。"""
        state_obj = self.hass.states.get(entity_id)
        if state_obj is None:
            return None

        card = await self.async_card(entity_id)
        if card is None:
            return None

        attrs = state_obj.attributes
        detail = dict(card)
        detail["controls"] = controls.build_controls(entity_id, state_obj.state, attrs)
        # 已经有控件展示的属性就不再重复列在「设备信息」里
        control_keys = {item.get("key") for item in detail["controls"] if item.get("key")}

        detail["attrs"] = controls.summarize(
            attrs, exclude=control_keys, localize=False
        )
        detail["editable"] = _domain(entity_id) not in controls.READ_ONLY_DOMAINS
        latitude = attrs.get("latitude")
        longitude = attrs.get("longitude")
        detail["geo"] = (
            {
                "latitude": latitude,
                "longitude": longitude,
                "radius": attrs.get("radius"),
                # zone 属性自带 editable；YAML 定义的区域为 False
                "editable": attrs.get("editable") is not False,
                "home": entity_id == "zone.home",
            }
            if latitude is not None and longitude is not None
            else None
        )
        return detail

    # 兼容旧调用（relay 早期版本用的名字）
    async def async_get(self, entity_id: str) -> dict | None:
        return await self.async_detail(entity_id)
