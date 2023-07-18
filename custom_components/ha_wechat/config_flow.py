from __future__ import annotations

from typing import Any
import voluptuous as vol

from homeassistant.config_entries import ConfigFlow
from homeassistant.data_entry_flow import FlowResult
import uuid
from .const import CONVERSATION_ASSISTANT
from .util import async_generate_qrcode
from .manifest import manifest

DOMAIN = manifest.domain
DATA_SCHEMA = vol.Schema({})

class SimpleConfigFlow(ConfigFlow, domain=DOMAIN):

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        
        if self._async_current_entries():
            return self.async_abort(reason="single_instance_allowed")

        if user_input is None:
            return self.async_show_form(step_id="user", data_schema=DATA_SCHEMA)

        # 检测是否配置语音小助手
        if self.hass.data.get(CONVERSATION_ASSISTANT) is None:
            return self.async_show_form(step_id="user", data_schema=DATA_SCHEMA, errors = {
                'base': 'conversation'
            })
  
        key = str(uuid.uuid4()).replace('-', '')
        topic = str(uuid.uuid1()).replace('-', '')

        await async_generate_qrcode(self.hass, topic, key)

        return self.async_create_entry(title=DOMAIN, data={ 'topic': topic, 'key': key })