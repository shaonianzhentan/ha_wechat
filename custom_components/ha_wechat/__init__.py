from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .util import async_generate_qrcode
from .manifest import manifest
from .const import PLATFORMS
from .http import HttpApi
from .ha_mqtt import HaMqtt

DOMAIN = manifest.domain

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    hass.http.register_view(HttpApi)
    config = entry.data
    topic = config['topic']
    key = config['key']
    hass.data[DOMAIN] = await hass.async_add_executor_job(HaMqtt, hass, {
        'topic': topic,
        'key': key
    })

    async def qrcode_service(service):
        await async_generate_qrcode(hass, topic, key)

    hass.services.async_register(DOMAIN, 'qrcode', qrcode_service)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True

async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    hass.data[DOMAIN].unload()
    del hass.data[DOMAIN]
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
