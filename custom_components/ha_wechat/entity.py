from homeassistant.helpers import area_registry as ar, device_registry as dr, entity_registry as er
from homeassistant.helpers.translation import async_get_translations  # 导入新的翻译方法

class EntityHelper:
    def __init__(self, hass):
        self.hass = hass

    async def get_entity_info(self, entity_id):
        ''' 获取实体的详细信息 '''
        if entity_id is not None:
            state = self.hass.states.get(entity_id)
            if state is not None:
                # 获取实体属性
                attributes = state.attributes

                # 获取实体注册表和设备注册表
                entity_registry = er.async_get(self.hass)
                device_registry = dr.async_get(self.hass)

                # 从实体注册表中获取实体信息
                entity_entry = entity_registry.async_get(entity_id)
                area_id = None

                if entity_entry:
                    # 如果实体关联了设备，则从设备注册表中获取区域 ID
                    if entity_entry.device_id:
                        device_entry = device_registry.async_get(entity_entry.device_id)
                        if device_entry:
                            area_id = device_entry.area_id
                    # 如果实体直接关联了区域，则获取区域 ID
                    if not area_id:
                        area_id = entity_entry.area_id

                # 获取区域注册表
                area_registry = ar.async_get(self.hass)
                # 根据区域 ID 获取区域名称
                area = area_registry.areas.get(area_id).name if area_id and area_id in area_registry.areas else ''

                # 翻译状态值
                state_name = await self.translate_state(entity_id, state.state)

                # 根据实体类型返回附加信息
                attrs = self.get_attributes(entity_id, attributes)

                return {
                    'id': entity_id,
                    'name': attributes.get('friendly_name', entity_id),
                    'area': area,
                    'state': state.state,
                    'stateName': state_name,
                    'attrs': attrs
                }
        return None

    async def translate_state(self, entity_id, state_value):
        ''' 翻译状态值 '''
        # 获取 Home Assistant 的翻译资源
        translations = await async_get_translations(
            self.hass, self.hass.config.language, "entity_component"
        )
        domain = entity_id.split('.')[0]  # 获取实体的域名，例如 light、sensor 等
        # 获取翻译的键，例如 "component.automation.entity_component._.state.off"
        translation_key = f"component.{domain}.entity_component._.state.{state_value}"
        # 返回翻译后的值，如果没有翻译则返回原始值
        return translations.get(translation_key, state_value)

    def get_attributes(self, entity_id, attributes):
        ''' 根据实体类型返回附加信息 '''
        domain = entity_id.split('.')[0]  # 获取实体的域名
        if domain in ['device_tracker', 'person']:
            # 定位设备及人员，返回经纬度
            return {
                'latitude': attributes.get('latitude'),
                'longitude': attributes.get('longitude')
            }
        elif domain == 'media_player':
            # 媒体播放器，返回歌手、歌名、音量等信息
            return {
                'title': attributes.get('media_title'),
                'artist': attributes.get('media_artist'),
                'volume': attributes.get('volume_level')
            }
        else:
            # 其他实体返回空
            return {}