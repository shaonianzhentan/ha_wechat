"""设备表：roomId 与每台设备的密钥、计数器、状态。"""

from __future__ import annotations

import secrets
import time

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import DOMAIN

STORAGE_KEY = "ha_wechat.devices"
STORAGE_VERSION = 1
DEVICE_LIMIT = 8


class DeviceStore:
    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self._store = Store(hass, STORAGE_VERSION, STORAGE_KEY)
        self.room: str = ""
        self.devices: dict[str, dict] = {}
        self._dirty = False

    async def async_load(self) -> None:
        data = await self._store.async_load() or {}
        self.room = data.get("room", "")
        self.devices = data.get("devices", {})

    async def async_save(self) -> None:
        self._dirty = False
        await self._store.async_save({"room": self.room, "devices": self.devices})

    def mark(self) -> None:
        self._dirty = True

    def get(self, device_id: str) -> dict | None:
        return self.devices.get(device_id)

    def add(self, device_id: str, key_hex: str, name: str = "") -> dict:
        device = {
            "key": key_hex,
            "name": name or f"设备 {device_id[:4]}",
            "created": int(time.time()),
            "active": False,
            "recv": 0,       # 已接受的小程序帧计数
            "sent": 0,       # 已发出的 HA 帧计数
            "fail": 0,       # 连续解密失败次数
            "last_seen": 0,  # 最近一次有效帧时间；在线状态由它推导
        }
        self.devices[device_id] = device
        self.mark()
        return device

    def remove(self, device_id: str) -> bool:
        if device_id not in self.devices:
            return False
        self.devices.pop(device_id)
        self.mark()
        return True

    def active(self) -> list[str]:
        return [device_id for device_id, device in self.devices.items() if device.get("active")]


async def async_get_store(hass: HomeAssistant) -> DeviceStore:
    """进程内单例：配置流程与集成 setup 共用同一份设备表。"""
    data = hass.data.setdefault(DOMAIN, {})
    store = data.get("store")
    if store is None:
        store = DeviceStore(hass)
        await store.async_load()
        if not store.room:
            store.room = secrets.token_hex(16)
            await store.async_save()
        data["store"] = store
    return store
