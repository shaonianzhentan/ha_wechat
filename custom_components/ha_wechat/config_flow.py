from __future__ import annotations

from typing import Any
import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, OptionsFlow
from homeassistant.data_entry_flow import FlowResult
import uuid
from .util import async_generate_qrcode
from .manifest import manifest
from homeassistant.helpers import selector

DOMAIN = manifest.domain
DATA_SCHEMA = vol.Schema({})


class SimpleConfigFlow(ConfigFlow, domain=DOMAIN):

    VERSION = 3

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> FlowResult:

        if self._async_current_entries():
            return self.async_abort(reason="single_instance_allowed")

        if user_input is None:
            return self.async_show_form(step_id="user", data_schema=DATA_SCHEMA)

        key = str(uuid.uuid4()).replace('-', '')
        topic = str(uuid.uuid1()).replace('-', '')

        await async_generate_qrcode(self.hass, topic, key)

        return self.async_create_entry(title=DOMAIN, data={'topic': topic, 'key': key})

    @staticmethod
    def async_get_options_flow(config_entry):
        """返回选项配置处理程序"""
        return SimpleOptionsFlowHandler(config_entry)


class SimpleOptionsFlowHandler(OptionsFlow):
    """处理选项配置的类"""

    def __init__(self, config_entry):
        """初始化选项配置"""
        self.config_entry = config_entry

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """初始选项配置步骤"""
        if user_input is not None:
            # 保存用户输入的选项
            return self.async_create_entry(title="", data=user_input)

        # 定义选项表单的 schema，支持选择多个实体
        options_schema = vol.Schema({
            vol.Optional(
                "entities",
                default=self.config_entry.options.get("entities", [])
            ): selector.EntitySelector(
                selector.EntitySelectorConfig(multiple=True)
            )
        })

        return self.async_show_form(step_id="init", data_schema=options_schema)
