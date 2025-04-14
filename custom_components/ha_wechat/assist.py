from homeassistant.core import Context

try:
    from homeassistant.components.conversation.agent_manager import async_converse
    SUPPORT_AGENT_MANAGER = True
except ImportError:
    SUPPORT_AGENT_MANAGER = False

async def async_assistant(hass, text):
    # 判断是否支持 agent_manager
    if not SUPPORT_AGENT_MANAGER:
        return "当前版本的 Home Assistant 不支持所需的语音助手功能，请升级到最新版本。"

    # 获取 assist_pipeline 数据
    pipeline_data = hass.data.get('assist_pipeline')
    if not pipeline_data:
        return "Assist pipeline 数据未找到，请检查配置。"

    storage_collection = pipeline_data.pipeline_store
    pipelines = storage_collection.async_items()
    preferred_pipeline = storage_collection.async_get_preferred_item()

    # 遍历管道并调用 async_converse
    for pipeline in pipelines:
        if pipeline.id == preferred_pipeline:
            try:
                conversation_result = await async_converse(
                    hass=hass,
                    text=text,
                    context=Context(),
                    conversation_id=None,
                    device_id=None,
                    language=hass.config.language,
                    agent_id=pipeline.conversation_engine,
                )
                intent_response = conversation_result.response
                speech = intent_response.speech.get('plain')
                if speech is not None:
                    return speech.get('speech')
            except Exception as e:
                return f"语音助手处理失败: {e}"
    return "未找到匹配的语音管道。"