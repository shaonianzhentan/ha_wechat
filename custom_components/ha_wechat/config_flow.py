from __future__ import annotations

from typing import Any
import voluptuous as vol

from homeassistant.config_entries import ConfigFlow
from homeassistant.data_entry_flow import FlowResult
import urllib
from .const import DOMAIN, CONVERSATION_ASSISTANT

DATA_SCHEMA = vol.Schema({
    vol.Required("uid"): str,
    vol.Required("topic"): str
})

class SimpleConfigFlow(ConfigFlow, domain=DOMAIN):

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:

        if user_input is None:
            return self.async_show_form(step_id="user", data_schema=DATA_SCHEMA)

        # 检测是否配置语音小助手
        if self.hass.data.get(CONVERSATION_ASSISTANT) is None:
            return self.async_show_form(step_id="user", data_schema=DATA_SCHEMA, errors = {
                'base': 'conversation'
            })

        topic = user_input['topic'].replace('/wechat', '')
        user_input['topic'] = topic
        uid = user_input['uid']

        # 检测是否安装
        if self.hass.data.get(DOMAIN + uid) is not None:
            return self.async_abort(reason="single_instance_allowed")

        qrc = urllib.parse.quote(f'ha:{uid}#{topic}')
        await self.hass.services.async_call('persistent_notification', 'create', {
                    'title': '使用【HomeAssistant家庭助理】小程序扫码关联',
                    'message': f'[![qrcode](https://cdn.dotmaui.com/qrc/?t={qrc})](https://github.com/shaonianzhentan/ha_wechat) <font size="6">内含密钥和订阅主题<br/>请勿截图分享</font>'
                })
        return self.async_create_entry(title=uid[:10], data=user_input)