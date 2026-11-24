"""二维码图片端点：给配置流程与通知里的 markdown 提供图片。

放在 /api/ 下但 requires_auth=False：URL 里的一次性 token 就是凭据，
120 秒后自动失效，配对成功立即删除，不落盘。
"""

from __future__ import annotations

import time

from aiohttp import web

from homeassistant.components.http import HomeAssistantView
from homeassistant.core import HomeAssistant

from .const import DOMAIN

DATA_QR_IMAGES = "qr_images"

# 视图一旦注册就无法注销，用模块级标记避免重新添加集成时重复注册同一路由
_REGISTERED = False


class QrImageView(HomeAssistantView):
    url = "/api/ha_wechat/qr.png"
    name = "api:ha_wechat:qr"
    requires_auth = False

    async def get(self, request: web.Request) -> web.Response:
        hass: HomeAssistant = request.app["hass"]
        token = request.query.get("token", "")
        images = hass.data.get(DOMAIN, {}).get(DATA_QR_IMAGES, {})
        entry = images.get(token)
        if entry is None:
            return web.Response(status=404)

        png, expires = entry
        if expires < time.time():
            images.pop(token, None)
            return web.Response(status=410)

        return web.Response(
            body=png,
            content_type="image/png",
            headers={"Cache-Control": "no-store"},
        )


def async_register_qr_view(hass: HomeAssistant) -> None:
    global _REGISTERED

    if _REGISTERED or hass.http is None:
        return
    hass.http.register_view(QrImageView())
    _REGISTERED = True
