from homeassistant.components.conversation.agent_manager import async_converse
from homeassistant.core import Context

async def async_assistant(hass, text):
    pipeline_data = hass.data['assist_pipeline']
    storage_collection = pipeline_data.pipeline_store
    pipelines = storage_collection.async_items()
    preferred_pipeline = storage_collection.async_get_preferred_item()

    for pipeline in pipelines:
        if pipeline.id == preferred_pipeline:
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