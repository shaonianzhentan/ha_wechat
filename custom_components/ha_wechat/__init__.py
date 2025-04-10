from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_registry import async_get

from .util import async_generate_qrcode
from .manifest import manifest
from .const import PLATFORMS
from .ha_mqtt import HaMqtt

DOMAIN = manifest.domain

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    config = entry.data
    # 
    topic = config['topic']
    key = config['key']
    entities = entry.options.get("entities", [])

    # 初始化 HaMqtt 实例
    hass.data[DOMAIN] = await hass.async_add_executor_job(HaMqtt, hass, {
        'topic': topic,
        'key': key,
        'entities': entities
    })

    async def qrcode_service(service):
        await async_generate_qrcode(hass, topic, key)

    hass.services.async_register(DOMAIN, 'qrcode', qrcode_service)

    # 监听配置更新
    entry.async_on_unload(entry.add_update_listener(async_update_entry))

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True

async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    hass.data[DOMAIN].unload()
    del hass.data[DOMAIN]
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

@callback
async def async_update_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """处理配置更新"""
    # 获取更新后的选项
    options = entry.options
    selected_entities = options.get("entities", [])

    # 示例：打印选中的实体
    #_LOGGER.info(f"配置更新，选中的实体: {selected_entities}")

    # 如果需要动态更新某些逻辑，可以在这里实现
    # 比如重新订阅 MQTT 主题或更新实体状态
    mqtt_instance: HaMqtt = hass.data[DOMAIN]
    mqtt_instance.update_entities(selected_entities)
