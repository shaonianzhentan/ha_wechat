"""每台已配对手机的唯一实体：状态是在线，属性里带推送额度。"""

from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device_registry import DeviceInfo

from .const import DOMAIN
from .push import PushManager
from .relay import WeChatRelay
from .sensor import _last_seen_iso


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities) -> None:
    data = hass.data[DOMAIN]
    relay: WeChatRelay = data["relay"]
    push: PushManager | None = data.get("push")
    sensors: dict[str, "WeChatPhoneEntity"] = data.setdefault(
        "phone_online_sensors", {}
    )

    def add_phone(device_id: str) -> None:
        if device_id in sensors:
            return
        device = relay.store.get(device_id)
        if device is None or not device.get("active"):
            return
        entity = WeChatPhoneEntity(entry, relay, push, device_id)
        sensors[device_id] = entity
        async_add_entities([entity])

    def remove_phone(device_id: str) -> None:
        entity = sensors.pop(device_id, None)
        if entity is not None:
            entity.async_remove()
        registry = er.async_get(hass)
        entity_id = registry.async_get_entity_id(
            "binary_sensor", DOMAIN, f"{DOMAIN}_phone_{device_id}_online"
        )
        if entity_id:
            registry.async_remove(entity_id)
        device_entry = dr.async_get(hass).async_get_device(
            identifiers={(DOMAIN, f"phone_{device_id}")}
        )
        if device_entry:
            dr.async_get(hass).async_remove_device(device_entry.id)

    def refresh_presence() -> None:
        for entity in sensors.values():
            entity.async_write_ha_state()

    def refresh_names() -> None:
        for entity in sensors.values():
            entity.update_name()

    data["add_phone_online"] = add_phone
    data["remove_phone_online"] = remove_phone
    data["refresh_phone_online"] = refresh_presence
    data["refresh_phone_online_names"] = refresh_names

    for device_id, device in relay.store.devices.items():
        if device.get("active"):
            add_phone(device_id)


class WeChatPhoneEntity(BinarySensorEntity):
    """一台已配对手机：状态在线，属性带推送剩余次数和订阅信息。"""

    _attr_should_poll = False
    _attr_has_entity_name = True
    _attr_name = None
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_icon = "mdi:wechat"

    def __init__(
        self,
        entry: ConfigEntry,
        relay: WeChatRelay,
        push: PushManager | None,
        device_id: str,
    ) -> None:
        self._device_id = device_id
        self.relay = relay
        self.push = push
        self._entry = entry
        self._attr_unique_id = f"{DOMAIN}_phone_{device_id}_online"

    def _identifiers(self) -> set[tuple[str, str]]:
        return {(DOMAIN, f"phone_{self._device_id}")}

    @property
    def device_info(self) -> DeviceInfo:
        device = self.relay.store.get(self._device_id) or {}
        return DeviceInfo(
            identifiers=self._identifiers(),
            name=device.get("name") or self._device_id,
            manufacturer="ha_wechat",
            model="微信小程序已配对手机",
        )

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()

        # 每台手机一个独立设备，实体名直接取设备名，避免“家庭助理【微信小程序】”前缀影响选择。
        info = self.device_info
        device = dr.async_get(self.hass).async_get_or_create(
            config_entry_id=self._entry.entry_id,
            identifiers=info["identifiers"],
            name=info["name"],
            manufacturer=info["manufacturer"],
            model=info["model"],
        )
        registry = er.async_get(self.hass)
        entity_id = registry.async_get_entity_id(
            "binary_sensor", DOMAIN, self._attr_unique_id
        )
        if entity_id:
            registry.async_update_entity(
                entity_id,
                device_id=device.id,
                has_entity_name=True,
                name=None,
                original_name=None,
            )

    def update_name(self) -> None:
        """手机改名后同步设备名，实体名会自动跟随设备名。"""
        hass = getattr(self, "hass", None)
        if hass is None:
            return
        device = dr.async_get(hass).async_get_device(identifiers=self._identifiers())
        if device is None:
            return
        name = (self.relay.store.get(self._device_id) or {}).get("name")
        if name and device.name != name:
            dr.async_get(hass).async_update_device(device.id, name=name)

    @property
    def is_on(self) -> bool:
        return self.relay.is_online(self._device_id)

    @property
    def extra_state_attributes(self) -> dict:
        info = self.push.info(self._device_id) if self.push else {}
        device = self.relay.store.get(self._device_id) or {}
        return {
            "phone_id": self._device_id,
            "alias": info.get("alias") or "",
            "quota": self.push.quota(self._device_id) if self.push else None,
            "subscribed": info.get("subscribed", False),
            "push_configured": bool(self.push and self.push.configured),
            "last_sent_at": info.get("lastSentAt") or None,
            "last_error": info.get("lastError") or "",
            "active": device.get("active", False),
            "last_seen": device.get("last_seen") or None,
            "last_seen_iso": _last_seen_iso(device.get("last_seen")),
        }
