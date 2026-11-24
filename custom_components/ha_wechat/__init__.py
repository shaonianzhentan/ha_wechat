from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from .const import DOMAIN, PLATFORMS
from .push import PushManager
from .relay import async_ensure_relay
from .store import async_get_store
from .util import async_create_pairing, cancel_pairing

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    store = await async_get_store(hass)

    # 重启后清掉未完成配对的设备，避免占用名额
    removed = False
    for device_id, device in list(store.devices.items()):
        if not device.get("active"):
            store.remove(device_id)
            removed = True
    if removed:
        await store.async_save()

    # 添加集成时已经连上中转的，这里直接接管，不重建连接
    relay = await async_ensure_relay(hass, entry)
    hass.data[DOMAIN]["relay"] = relay

    push = hass.data[DOMAIN].get("push")
    if push is None:
        push = PushManager(hass, store)
        hass.data[DOMAIN]["push"] = push
    push.configure(entry)

    entry.async_on_unload(entry.add_update_listener(_async_update_entry))

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    _async_remove_legacy_quota_entities(hass)

    # 首次安装或没有可用设备时，直接出一张配对二维码
    if not store.devices:
        await async_create_pairing(hass, store)

    return True


def _async_remove_legacy_quota_entities(hass: HomeAssistant) -> None:
    """删除旧版每台手机的“微信提醒剩余次数” sensor 登记。"""
    registry = er.async_get(hass)
    removed = 0
    for entity in list(registry.entities.values()):
        unique_id = entity.unique_id or ""
        if (
            entity.platform == DOMAIN
            and unique_id.startswith(f"{DOMAIN}_phone_")
            and unique_id.endswith("_quota")
        ):
            registry.async_remove(entity.entity_id)
            removed += 1
    if removed:
        _LOGGER.info("已清理 %s 个旧版推送额度实体", removed)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    if not await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        return False

    data = hass.data.get(DOMAIN, {})
    relay = data.pop("relay", None)
    if relay is not None:
        await relay.async_stop()

    push = data.get("push")
    if push is not None:
        await push.async_stop()

    cancel_pairing(hass)
    # add_device 已移除；这里顺手清理旧版本可能残留的服务注册
    for service in ("add_device", "notify"):
        if hass.services.has_service(DOMAIN, service):
            hass.services.async_remove(DOMAIN, service)
    hass.data.pop(DOMAIN, None)
    return True


async def _async_update_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    relay = hass.data.get(DOMAIN, {}).get("relay")
    if relay is not None:
        await relay.async_update_entities()
        await relay.async_broadcast_catalog()


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """v4 的 token/relay 已作废，迁移后需要重新扫码。"""
    if entry.version >= 5:
        return True

    hass.config_entries.async_update_entry(entry, data={}, version=5)
    _LOGGER.info("ha_wechat 已升级到端到端加密，请重新扫码配对")
    return True
