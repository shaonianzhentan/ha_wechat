"""每台已配对手机一个标准 notify 实体。"""

from __future__ import annotations

from homeassistant.components.notify import NotifyEntity, NotifyEntityFeature
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device_registry import DeviceInfo

from .const import DOMAIN
from .push import PushManager
from .relay import WeChatRelay


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities
) -> None:
    data = hass.data[DOMAIN]
    relay: WeChatRelay = data["relay"]
    push: PushManager | None = data.get("push")
    notifiers: dict[str, "WeChatNotifyEntity"] = data.setdefault(
        "phone_notify_entities", {}
    )

    def add_phone(device_id: str) -> None:
        if device_id in notifiers:
            return
        device = relay.store.get(device_id)
        if device is None or not device.get("active"):
            return
        entity = WeChatNotifyEntity(entry, relay, push, device_id)
        notifiers[device_id] = entity
        async_add_entities([entity])

    def remove_phone(device_id: str) -> None:
        entity = notifiers.pop(device_id, None)
        if entity is not None:
            entity.async_remove()
        registry = er.async_get(hass)
        entity_id = registry.async_get_entity_id(
            "notify", DOMAIN, f"{DOMAIN}_phone_{device_id}_notify"
        )
        if entity_id:
            registry.async_remove(entity_id)

    data["add_phone_notify"] = add_phone
    data["remove_phone_notify"] = remove_phone

    for device_id, device in relay.store.devices.items():
        if device.get("active"):
            add_phone(device_id)


class WeChatNotifyEntity(NotifyEntity):
    """一台手机的标准通知目标，底层仍走现有微信订阅消息推送。"""

    _attr_should_poll = False
    _attr_has_entity_name = True
    _attr_name = "通知"
    _attr_icon = "mdi:wechat"
    _attr_supported_features = NotifyEntityFeature.TITLE

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
        self._attr_unique_id = f"{DOMAIN}_phone_{device_id}_notify"

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

    @property
    def available(self) -> bool:
        return bool(self.push and self.push.configured)

    @property
    def extra_state_attributes(self) -> dict:
        info = self.push.info(self._device_id) if self.push else {}
        return {
            "phone_id": self._device_id,
            "alias": info.get("alias") or "",
            "quota": self.push.quota(self._device_id) if self.push else None,
            "subscribed": info.get("subscribed", False),
            "push_configured": bool(self.push and self.push.configured),
            "last_sent_at": info.get("lastSentAt") or None,
            "last_error": info.get("lastError") or "",
        }

    async def async_send_message(
        self, message: str, title: str | None = None
    ) -> None:
        if self.push is None or not self.push.configured:
            raise HomeAssistantError(
                "推送服务未配置：请检查插件内置的推送地址"
            )
        await self.push.async_notify(
            devices=[self._device_id],
            title=(title or "").strip() or "家庭助理",
            message=str(message or "").strip(),
        )
