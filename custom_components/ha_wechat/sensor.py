from homeassistant.components.sensor import SensorEntity

class WechatSensor(SensorEntity):

    def __init__(self, entry):
        self._attr_unique_id = entry.entry_id
        self._attr_name = "HomeAssistant家庭助理"
        self._attr_icon = 'mdi:wechat'

    async def async_update(self):
      self._attr_native_value = ''
      self._attr_extra_state_attributes = {
          '连接数': 1
      }