"""添加集成 = 出二维码 + 等扫码，扫完自动完成。"""

from __future__ import annotations

import asyncio
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.helpers import selector

from .const import (
    CONF_EXCLUDE_DOMAINS,
    CONF_EXCLUDE_ENTITIES,
    CONF_INCLUDE_DOMAINS,
    CONF_INCLUDE_ENTITIES,
    DOMAIN,
    PAIR_WINDOW,
)
from .relay import async_ensure_relay
from .store import async_get_store
from .util import async_create_pairing


class HaWechatConfigFlow(ConfigFlow, domain=DOMAIN):
    """扫码配对。

    用 Home Assistant 的 progress 步骤实现“等待”：任务未完成时继续 show_progress，
    完成后 show_progress_done，前端会自动刷新并进入下一步，不需要用户点按钮。
    """

    VERSION = 5

    def __init__(self) -> None:
        self._device_id: str | None = None
        self._image: str | None = None
        self._task: asyncio.Task | None = None
        # 已存在集成时，这次流程只加设备，不再创建第二个配置条目
        self._existing_entry = False

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        self._existing_entry = bool(self._async_current_entries())

        # 用户在等待页点了提交 = 换一张新码
        if user_input is not None:
            self._device_id = None
            self._image = None
            self._task = None

        store = await async_get_store(self.hass)
        relay = await async_ensure_relay(self.hass)
        await relay.async_wait_connected(5.0)

        if self._device_id is None:
            try:
                self._device_id, self._image = await async_create_pairing(
                    self.hass, store
                )
            except ValueError:
                # 设备数到上限：先删旧设备再加
                return self.async_abort(reason="device_limit")

        if self._task is None:
            self._task = self.hass.async_create_task(
                self._async_wait_paired(store, self._device_id)
            )

        if not self._task.done():
            return self.async_show_progress(
                step_id="user",
                progress_action="wait_for_qr",
                description_placeholders={
                    "qr": f"![qrcode]({self._image})",
                    "window": str(PAIR_WINDOW),
                },
                progress_task=self._task,
            )

        return self.async_show_progress_done(next_step_id="finish")

    async def _async_wait_paired(self, store, device_id: str) -> None:
        """最多等 PAIR_WINDOW 秒，设备首帧解密成功即返回。"""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + PAIR_WINDOW
        while loop.time() < deadline:
            device = store.get(device_id)
            if device and device.get("active"):
                return
            await asyncio.sleep(0.5)

    async def async_step_finish(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        store = await async_get_store(self.hass)
        device = store.get(self._device_id) if self._device_id else None

        if device and device.get("active"):
            if self._existing_entry:
                return self.async_abort(reason="device_added")
            return self.async_create_entry(title="家庭助理", data={})

        return self.async_show_form(
            step_id="expired",
            data_schema=vol.Schema({}),
            description_placeholders={"window": str(PAIR_WINDOW)},
        )

    async def async_step_expired(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is None:
            return self.async_show_form(
                step_id="expired",
                data_schema=vol.Schema({}),
                description_placeholders={"window": str(PAIR_WINDOW)},
            )

        self._device_id = None
        self._image = None
        self._task = None
        return await self.async_step_user()

    @staticmethod
    def async_get_options_flow(entry: ConfigEntry) -> OptionsFlow:
        return HaWechatOptionsFlow()


class HaWechatOptionsFlow(OptionsFlow):
    def _current_options(self) -> dict[str, Any]:
        entry = getattr(self, "config_entry", None)
        if entry is not None:
            options = dict(entry.options)
        else:
            relay = self.hass.data.get(DOMAIN, {}).get("relay")
            entry = getattr(relay, "entry", None)
            options = dict(entry.options) if entry is not None else {}

        # 兼容旧版：mode=selected + entities 迁移到 include_entities
        if CONF_INCLUDE_ENTITIES not in options:
            legacy_entities = _string_list(options.get("entities"))
            if legacy_entities and options.get("mode") == "selected":
                options[CONF_INCLUDE_ENTITIES] = legacy_entities
        options.pop("mode", None)
        options.pop("entities", None)
        return options

    def _domain_options(self) -> list[selector.SelectOptionDict]:
        domains = sorted(
            {
                state.entity_id.split(".", 1)[0]
                for state in self.hass.states.async_all()
            }
        )
        return [
            selector.SelectOptionDict(value=domain, label=domain)
            for domain in domains
        ]

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        store = self.hass.data[DOMAIN]["store"]
        options = self._current_options()

        if user_input is not None:
            removed_devices = []
            for device_id in user_input.get("remove") or []:
                store.remove(device_id)
                removed_devices.append(device_id)
                for key in (
                    "remove_phone_notify",
                    "remove_phone_sensor",
                    "remove_phone_online",
                ):
                    remove_entity = self.hass.data[DOMAIN].get(key)
                    if remove_entity:
                        remove_entity(device_id)
            await store.async_save()

            push = self.hass.data[DOMAIN].get("push")
            if push is not None and removed_devices:
                self.hass.async_create_task(push.async_forget(removed_devices))

            # 保留设备删除以外的所有选项，避免保存时把筛选条件清空
            new_options = {
                **options,
                CONF_INCLUDE_DOMAINS: _string_list(
                    user_input.get(CONF_INCLUDE_DOMAINS)
                ),
                CONF_EXCLUDE_DOMAINS: _string_list(
                    user_input.get(CONF_EXCLUDE_DOMAINS)
                ),
                CONF_INCLUDE_ENTITIES: _string_list(
                    user_input.get(CONF_INCLUDE_ENTITIES)
                ),
                CONF_EXCLUDE_ENTITIES: _string_list(
                    user_input.get(CONF_EXCLUDE_ENTITIES)
                ),
            }
            return self.async_create_entry(data=new_options)

        schema: dict[Any, Any] = {}
        domain_options = self._domain_options()
        if store.devices:
            schema[vol.Optional("remove", default=[])] = selector.SelectSelector(
                selector.SelectSelectorConfig(
                    multiple=True,
                    options=[
                        selector.SelectOptionDict(
                            value=device_id, label=device.get("name", device_id)
                        )
                        for device_id, device in store.devices.items()
                    ],
                )
            )

        schema[
            vol.Optional(
                CONF_INCLUDE_DOMAINS,
                default=_string_list(options.get(CONF_INCLUDE_DOMAINS)),
            )
        ] = selector.SelectSelector(
            selector.SelectSelectorConfig(
                multiple=True,
                options=domain_options,
            )
        )
        schema[
            vol.Optional(
                CONF_EXCLUDE_DOMAINS,
                default=_string_list(options.get(CONF_EXCLUDE_DOMAINS)),
            )
        ] = selector.SelectSelector(
            selector.SelectSelectorConfig(
                multiple=True,
                options=domain_options,
            )
        )
        schema[
            vol.Optional(
                CONF_INCLUDE_ENTITIES,
                default=_string_list(options.get(CONF_INCLUDE_ENTITIES)),
            )
        ] = selector.EntitySelector(
            selector.EntitySelectorConfig(multiple=True)
        )
        schema[
            vol.Optional(
                CONF_EXCLUDE_ENTITIES,
                default=_string_list(options.get(CONF_EXCLUDE_ENTITIES)),
            )
        ] = selector.EntitySelector(
            selector.EntitySelectorConfig(multiple=True)
        )

        return self.async_show_form(step_id="init", data_schema=vol.Schema(schema))


def _string_list(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value] if value else []
    if isinstance(value, (list, tuple, set, frozenset)):
        return [str(item) for item in value if item]
    return []
