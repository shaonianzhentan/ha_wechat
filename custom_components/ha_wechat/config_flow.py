from __future__ import annotations

from typing import Any
import voluptuous as vol

from homeassistant.config_entries import ConfigFlow
from homeassistant.data_entry_flow import FlowResult

from .const import DOMAIN

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
        if self.hass.data.get('conversation_voice') is None:
            return self.async_show_form(step_id="user", data_schema=DATA_SCHEMA, errors = {
                'base': 'conversation'
            })

        user_input['topic'] = user_input['topic'].replace('/wechat', '')
        uid = user_input['uid']
        return self.async_create_entry(title=uid[:10], data=user_input)