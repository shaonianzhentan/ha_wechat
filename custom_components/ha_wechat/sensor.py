from __future__ import annotations

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .manifest import manifest
from .relay import WeChatRelay

DEVICE_IDENTIFIERS = {(DOMAIN, "shaonianzhentan")}


def _last_seen_iso(value) -> str | None:
    """把 epoch 时间转成 HA 本地时区 ISO 字符串，便于前端/模板直接读。"""
    try:
        timestamp = float(value or 0)
    except (TypeError, ValueError):
        return None
    if timestamp <= 0:
        return None
    return dt_util.as_local(dt_util.utc_from_timestamp(timestamp)).isoformat()


async def async_setup_entry(hass, entry: ConfigEntry, async_add_entities) -> None:
    data = hass.data[DOMAIN]
    relay: WeChatRelay = data["relay"]
    registry = er.async_get(hass)

    def remove_phone(device_id: str) -> None:
        """设备解绑时清掉旧版额度实体；在线实体由 binary_sensor 平台删除。"""
        entity_id = registry.async_get_entity_id(
            "sensor", DOMAIN, f"{DOMAIN}_phone_{device_id}_quota"
        )
        if entity_id:
            registry.async_remove(entity_id)

    def refresh_names() -> None:
        refresh_online = data.get("refresh_phone_online_names")
        if refresh_online:
            refresh_online()

    aggregate = WeChatSensor(entry, relay)
    async_add_entities([aggregate])

    def refresh_aggregate() -> None:
        aggregate.async_write_ha_state()

    data["remove_phone_sensor"] = remove_phone
    data["refresh_phone_names"] = refresh_names
    data["refresh_phone_sensors"] = refresh_aggregate


class WeChatSensor(SensorEntity):
    _attr_name = "微信小程序"
    _attr_icon = "mdi:wechat"
    _attr_should_poll = False

    def __init__(self, entry: ConfigEntry, relay: WeChatRelay) -> None:
        self._attr_unique_id = entry.entry_id
        self._attr_device_info = DeviceInfo(
            name="家庭助理【微信小程序】",
            manufacturer="shaonianzhentan",
            model="ha_wechat",
            configuration_url=manifest.documentation,
            identifiers=DEVICE_IDENTIFIERS,
        )
        self.relay = relay

    @property
    def native_value(self) -> str:
        if not self.relay.connected:
            return "未连接"
        online = len(self.relay.online_ids())
        return f"已连接（{online} 台在线）" if online else "已连接（无设备在线）"

    @property
    def extra_state_attributes(self) -> dict:
        return {
            "relay": self.relay.relay,
            "devices": [
                {
                    "id": device_id,
                    "name": device.get("name"),
                    "active": device.get("active"),
                    "online": self.relay.is_online(device_id),
                    "last_seen": device.get("last_seen") or None,
                    "last_seen_iso": _last_seen_iso(device.get("last_seen")),
                }
                for device_id, device in self.relay.store.devices.items()
            ],
            "entities": len(self.relay.entities),
            "last_message": self.relay.last_message,
        }
