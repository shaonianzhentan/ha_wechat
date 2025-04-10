from homeassistant.components.sensor import SensorEntity
from homeassistant.helpers.entity import DeviceInfo, Entity

from .manifest import manifest

async def async_setup_entry(hass, config_entry, async_add_entities):
    ha_mqtt = hass.data[manifest.domain]
    async_add_entities([WeChatSensor(config_entry, ha_mqtt)])

class WeChatSensor(SensorEntity):

    def __init__(self, entry, ha_mqtt):
        self._attr_unique_id = entry.entry_id
        self._attr_name = "微信小程序"
        self._attr_icon = 'mdi:wechat'
        self._attr_device_info = DeviceInfo(
            name="家庭助理【微信小程序】",
            manufacturer='shaonianzhentan',
            model='ha_wechat',
            configuration_url=manifest.documentation,
            identifiers={(manifest.domain, 'shaonianzhentan')},
        )
        self.ha_mqtt = ha_mqtt
        self._attr_extra_state_attributes = {
          'receive_time': None
        }

    @property
    def state(self):
      return '连接成功' if self.ha_mqtt.is_connected else '断开连接'

    async def async_update(self):
      self._attr_extra_state_attributes = {
        'receive_time': self.ha_mqtt.msg_time
      }