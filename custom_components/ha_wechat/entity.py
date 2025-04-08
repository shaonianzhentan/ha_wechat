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
                # 获取区域注册表
                area_registry = await self.hass.helpers.area_registry.async_get_registry()
                # 获取实体的区域 ID
                area_id = attributes.get('area_id')
                # 根据区域 ID 获取区域名称
                area = area_registry.async_get_area(area_id).name if area_id else '未知区域'

                # 翻译状态值
                state_name = await self.translate_state(entity_id, state.state)

                return {
                    'id': entity_id,
                    'name': attributes.get('friendly_name', entity_id),
                    'area': area,
                    'state': state.state,
                    'stateName': state_name
                }
        return None

    async def translate_state(self, entity_id, state_value):
        ''' 翻译状态值 '''
        # 获取 Home Assistant 的翻译资源
        translations = await self.hass.helpers.translation.async_get_translations(
            self.hass.config.language, "state"
        )
        domain = entity_id.split('.')[0]  # 获取实体的域名，例如 light、sensor 等
        # 获取翻译的键，例如 "component.light.state.on"
        translation_key = f"component.{domain}.state.{state_value}"
        # 返回翻译后的值，如果没有翻译则返回原始值
        return translations.get(translation_key, state_value)