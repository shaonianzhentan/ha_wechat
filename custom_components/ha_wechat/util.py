"""配对：生成二维码、通知展示、超时作废。"""

from __future__ import annotations

import secrets
import time
from io import BytesIO

import qrcode
from homeassistant.core import HomeAssistant

from .const import DOMAIN, PAIR_WINDOW
from .qr_view import DATA_QR_IMAGES, async_register_qr_view
from .store import DEVICE_LIMIT, DeviceStore

QR_NOTIFICATION_ID = "ha_wechat_qrcode"
PAIRED_NOTIFICATION_ID = "ha_wechat_paired"
EXPIRED_NOTIFICATION_ID = "ha_wechat_expired"
_TIMER_KEY = "pair_timer"
_QR_TOKEN_KEY = "qr_token"


def _qr_png(content: str) -> bytes:
    qr = qrcode.QRCode(
        error_correction=qrcode.constants.ERROR_CORRECT_M, box_size=8, border=2
    )
    qr.add_data(content)
    qr.make(fit=True)
    image = qr.make_image(fill_color="black", back_color="white")
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def async_write_qrcode(hass: HomeAssistant, content: str) -> str:
    """二维码只放内存，用一次性 token 取图，返回前端可直接用的地址。

    前端会过滤 data URI，所以图片必须走 URL；这个 URL 由集成自己注册，
    不依赖 config/www，也不需要重启。
    """
    async_register_qr_view(hass)
    data = hass.data.setdefault(DOMAIN, {})
    images: dict[str, tuple[bytes, float]] = data.setdefault(DATA_QR_IMAGES, {})

    now = time.time()
    for token, (_, expires) in list(images.items()):
        if expires < now:
            images.pop(token, None)

    token = secrets.token_hex(16)
    images[token] = (_qr_png(content), now + PAIR_WINDOW + 10)
    data[_QR_TOKEN_KEY] = token
    return f"/api/ha_wechat/qr.png?token={token}"


def _async_cleanup_qrcode(hass: HomeAssistant) -> None:
    data = hass.data.get(DOMAIN, {})
    token = data.pop(_QR_TOKEN_KEY, None)
    if token is not None:
        data.get(DATA_QR_IMAGES, {}).pop(token, None)


async def async_create_pairing(
    hass: HomeAssistant, store: DeviceStore, *, notify: bool = True
) -> tuple[str, str]:
    """生成一次配对，返回 (deviceId, 二维码图片地址)。"""
    if len(store.devices) >= DEVICE_LIMIT:
        raise ValueError(f"最多 {DEVICE_LIMIT} 台设备，请先在集成里删除旧设备")

    # 同一时间只保留一个待配对设备，避免多张有效二维码并存
    for device_id, device in list(store.devices.items()):
        if not device.get("active"):
            store.remove(device_id)

    device_id = secrets.token_hex(4)
    key = secrets.token_hex(32)
    store.add(device_id, key)
    await store.async_save()

    content = f"ha#{store.room}#{device_id}#{key}"
    image = async_write_qrcode(hass, content)
    if notify:
        await _async_notify_qrcode(hass, image)
    _async_schedule_expire(hass, store, device_id)
    return device_id, image


async def async_on_paired(hass: HomeAssistant, device: dict) -> None:
    _cancel_timer(hass)
    _async_cleanup_qrcode(hass)
    await _async_dismiss(hass, QR_NOTIFICATION_ID)
    await hass.services.async_call(
        "persistent_notification",
        "create",
        {
            "title": "设备已配对",
            "message": (
                f"「{device.get('name', '新设备')}」已关联，现在可以在小程序里控制设备。"
                "如非本人操作，请在集成配置里删除该设备。"
            ),
            "notification_id": PAIRED_NOTIFICATION_ID,
        },
        blocking=False,
    )


async def _async_notify_qrcode(hass: HomeAssistant, image: str) -> None:
    await hass.services.async_call(
        "persistent_notification",
        "create",
        {
            "title": "扫码添加设备",
            "message": (
                f"![qrcode]({image})\n\n"
                f"用微信小程序扫码，{PAIR_WINDOW} 秒内有效。"
                "二维码含本设备密钥，请勿截图或转发。"
            ),
            "notification_id": QR_NOTIFICATION_ID,
        },
        blocking=False,
    )


def _async_schedule_expire(
    hass: HomeAssistant, store: DeviceStore, device_id: str
) -> None:
    data = hass.data.setdefault(DOMAIN, {})
    _cancel_timer(hass)

    def _expire() -> None:
        data.pop(_TIMER_KEY, None)
        hass.async_create_task(_async_expire(hass, store, device_id))

    data[_TIMER_KEY] = hass.loop.call_later(PAIR_WINDOW, _expire)


def _cancel_timer(hass: HomeAssistant) -> None:
    handle = hass.data.get(DOMAIN, {}).pop(_TIMER_KEY, None)
    if handle is not None:
        handle.cancel()


def cancel_pairing(hass: HomeAssistant) -> None:
    """卸载集成时取消待配对的超时任务。"""
    _cancel_timer(hass)


async def _async_expire(
    hass: HomeAssistant, store: DeviceStore, device_id: str
) -> None:
    device = store.get(device_id)
    if device is None or device.get("active"):
        return

    store.remove(device_id)
    await store.async_save()
    _async_cleanup_qrcode(hass)
    await _async_dismiss(hass, QR_NOTIFICATION_ID)

    # 集成还没创建（在“添加集成”里等扫码）时，配对失败要把临时连接收掉
    data = hass.data.get(DOMAIN, {})
    relay = data.get("relay")
    if relay is not None and relay.entry is None:
        await relay.async_stop()
        data.pop("relay", None)

    await hass.services.async_call(
        "persistent_notification",
        "create",
        {
            "title": "二维码已失效",
            "message": f"超过 {PAIR_WINDOW} 秒未扫码，请在集成里重新添加设备。",
            "notification_id": EXPIRED_NOTIFICATION_ID,
        },
        blocking=False,
    )


async def _async_dismiss(hass: HomeAssistant, notification_id: str) -> None:
    await hass.services.async_call(
        "persistent_notification",
        "dismiss",
        {"notification_id": notification_id},
        blocking=False,
    )
