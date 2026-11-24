"""把文字交给 Home Assistant 的语音助手处理。

文字只走 conversation agent，只返回文字，不生成 TTS 音频；录音接口保留给
支持音频回传的客户端，走 Assist Pipeline 的 STT/INTENT/TTS 全链路。
"""

from __future__ import annotations

import logging

from homeassistant.core import callback
from homeassistant.helpers.network import get_url

_LOGGER = logging.getLogger(__name__)

try:
    from homeassistant.components.conversation import (
        HOME_ASSISTANT_AGENT,
        async_converse,
        async_get_agent_info,
        get_agent_manager,
    )

    _HAS_CONVERSATION = True
except ImportError:  # pragma: no cover - 兼容旧版本
    try:
        from homeassistant.components.conversation.agent_manager import (
            async_converse,
            get_agent_manager,
        )
        from homeassistant.components.conversation.const import HOME_ASSISTANT_AGENT

        async_get_agent_info = None
        _HAS_CONVERSATION = True
    except ImportError:
        async_converse = None
        get_agent_manager = None
        async_get_agent_info = None
        HOME_ASSISTANT_AGENT = None
        _HAS_CONVERSATION = False

try:
    from homeassistant.components.assist_pipeline import (
        PipelineEventType,
        PipelineStage,
        async_get_pipeline,
        async_get_pipelines,
        async_pipeline_from_audio_stream,
    )

    _HAS_PIPELINE = True
except ImportError:  # pragma: no cover - 兼容旧版本
    PipelineEventType = None
    PipelineStage = None
    async_get_pipeline = None
    async_get_pipelines = None
    async_pipeline_from_audio_stream = None
    _HAS_PIPELINE = False

try:
    from homeassistant.components import tts

    _HAS_TTS = True
except ImportError:  # pragma: no cover
    tts = None
    _HAS_TTS = False

FALLBACK = "未检测到可用的语音助手"
DEFAULT_NAME = "助理"


def _preferred_pipeline(hass):
    """取 Home Assistant 里设为首选的语音助手流水线。"""
    if not _HAS_PIPELINE:
        return None
    try:
        return async_get_pipeline(hass)
    except Exception:  # noqa: BLE001 - 还没加载语音流水线时退回默认助手
        return None


def _pipeline_by_id(hass, pipeline_id: str | None):
    if not _HAS_PIPELINE:
        return None
    try:
        return async_get_pipeline(hass, pipeline_id) if pipeline_id else async_get_pipeline(hass)
    except Exception:  # noqa: BLE001
        return None


def _default_agent_id(hass) -> str:
    """取系统里设置的默认助手，用户改动后自动跟随。"""
    if not _HAS_CONVERSATION:
        return HOME_ASSISTANT_AGENT or ""

    pipeline = _preferred_pipeline(hass)
    engine = getattr(pipeline, "conversation_engine", None) if pipeline else None
    if engine:
        return engine

    agent = get_agent_manager(hass).default_agent
    if agent is None:
        return HOME_ASSISTANT_AGENT
    return getattr(agent, "entity_id", None) or HOME_ASSISTANT_AGENT


async def async_assistant_name(hass) -> str:
    """返回默认语音助手名称，优先用 HA 里给流水线起的名字。"""
    pipeline = _preferred_pipeline(hass)
    name = str(getattr(pipeline, "name", "") or "").strip() if pipeline else ""
    if name:
        return name

    agent_id = _default_agent_id(hass)
    if async_get_agent_info is not None:
        try:
            info = async_get_agent_info(hass, agent_id)
            if info and getattr(info, "name", None):
                return str(info.name)
        except Exception:  # noqa: BLE001
            pass

    if _HAS_CONVERSATION:
        agent = get_agent_manager(hass).default_agent
        name = str(getattr(agent, "name", "") or "").strip() if agent else ""
        if name:
            return name

    return DEFAULT_NAME


async def async_assistant_pipelines(hass) -> list[dict]:
    """给小程序的选择器用：只暴露 id/名称/语言，不泄露引擎内部配置。"""
    if not _HAS_PIPELINE:
        return []
    try:
        pipelines = async_get_pipelines(hass) or []
    except Exception as err:  # noqa: BLE001
        _LOGGER.debug("读取语音流水线失败: %s", err)
        return []
    preferred = _preferred_pipeline(hass)
    preferred_id = getattr(preferred, "id", None) if preferred else None
    rows = []
    for pipeline in pipelines:
        pid = str(getattr(pipeline, "id", "") or "")
        if not pid:
            continue
        rows.append(
            {
                "id": pid,
                "name": str(getattr(pipeline, "name", "") or "默认助手"),
                "language": str(getattr(pipeline, "language", "") or ""),
                "stt": bool(getattr(pipeline, "stt_engine", None)),
                "tts": bool(getattr(pipeline, "tts_engine", None)),
                "preferred": pid == preferred_id,
            }
        )
    return rows


async def _async_tts_url(hass, pipeline, text: str) -> str | None:
    """用流水线配置的 TTS 生成一段可播放音频；没有 TTS 就静默返回 None。"""
    if not _HAS_TTS or not text or pipeline is None:
        return None
    engine = getattr(pipeline, "tts_engine", None)
    if not engine:
        return None
    language = getattr(pipeline, "tts_language", None) or getattr(pipeline, "language", None)
    voice = getattr(pipeline, "tts_voice", None)
    options = {"voice": voice} if voice else {}
    try:
        stream = tts.async_create_stream(hass, engine, language=language, options=options)
        stream.async_set_message(text)
        return f"{get_url(hass)}{stream.url}"
    except Exception as err:  # noqa: BLE001 - 某些引擎不认 voice 选项
        _LOGGER.debug("TTS 生成失败（带 voice）: %s", err)
        if options:
            try:
                stream = tts.async_create_stream(
                    hass=hass, engine=engine, language=language, options={}
                )
                stream.async_set_message(text)
                return f"{get_url(hass)}{stream.url}"
            except Exception as inner:  # noqa: BLE001 - 播放失败不影响文字回复
                _LOGGER.debug("TTS 生成失败: %s", inner)
        return None


async def _async_text_conversation(hass, text: str, pipeline_id: str | None) -> dict:
    pipeline = _pipeline_by_id(hass, pipeline_id)
    speech = ""
    if _HAS_CONVERSATION:
        try:
            from homeassistant.core import Context

            agent_id = (
                getattr(pipeline, "conversation_engine", None)
                if pipeline is not None
                else None
            ) or _default_agent_id(hass)
            result = await async_converse(
                hass=hass,
                text=text,
                conversation_id=None,
                context=Context(),
                language=getattr(pipeline, "conversation_language", None)
                or getattr(pipeline, "language", None)
                or hass.config.language,
                agent_id=agent_id,
            )
            plain = result.response.speech.get("plain") or {}
            speech = str(plain.get("speech") or "").strip()
        except Exception as err:  # noqa: BLE001 - 助手未配置时退回服务调用
            _LOGGER.debug("async_converse 失败: %s", err)

    if not speech:
        try:
            response = await hass.services.async_call(
                "conversation",
                "process",
                {
                    "text": text,
                    "language": getattr(pipeline, "language", None) or hass.config.language,
                },
                blocking=True,
                return_response=True,
            )
            plain = (((response or {}).get("response") or {}).get("speech") or {}).get(
                "plain"
            ) or {}
            speech = str(plain.get("speech") or "").strip()
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("conversation.process 失败: %s", err)

    speech = speech or FALLBACK
    return {
        "speech": speech,
        "text": speech,
        "audio": None,
        "pipeline": str(getattr(pipeline, "id", "") or pipeline_id or ""),
        "language": str(getattr(pipeline, "language", "") or hass.config.language),
        "voice": False,
    }


async def async_assistant_result(hass, text: str, pipeline_id: str | None = None) -> dict:
    if not text:
        return {"speech": "请输入你的指令", "text": "请输入你的指令", "audio": None}
    return await _async_text_conversation(hass, text, pipeline_id)


async def async_assistant_voice(hass, audio: bytes, pipeline_id: str | None = None) -> dict:
    """音频走 Assist Pipeline：STT → intent → TTS。"""
    if not _HAS_PIPELINE:
        raise ValueError("Home Assistant 未加载语音流水线")
    pipeline = _pipeline_by_id(hass, pipeline_id)
    if pipeline is None:
        raise ValueError("没有可用的语音助手流水线")

    try:
        from homeassistant.components.stt import (
            AudioBitRates,
            AudioChannels,
            AudioCodecs,
            AudioFormats,
            AudioSampleRates,
            SpeechMetadata,
        )
    except ImportError as err:  # pragma: no cover
        raise ValueError("Home Assistant 未加载语音识别组件") from err

    from homeassistant.core import Context

    stt_text = ""
    speech = ""
    audio_url = ""
    error = ""
    done = {"stt": False, "intent": False, "tts": False}

    metadata = SpeechMetadata(
        language=getattr(pipeline, "stt_language", None)
        or getattr(pipeline, "language", None)
        or hass.config.language,
        format=AudioFormats.WAV,
        codec=AudioCodecs.PCM,
        bit_rate=AudioBitRates.BITRATE_16,
        sample_rate=AudioSampleRates.SAMPLERATE_16000,
        channel=AudioChannels.CHANNEL_MONO,
    )

    async def stream():
        # 分块喂给流水线，避免一次性把整段录音压进事件循环
        chunk_size = 4096
        for index in range(0, len(audio), chunk_size):
            yield audio[index : index + chunk_size]

    @callback
    def event_callback(event) -> None:
        nonlocal stt_text, speech, audio_url, error
        try:
            kind = getattr(event, "type", None)
            data = getattr(event, "data", None) or {}
            if kind == PipelineEventType.STT_END:
                done["stt"] = True
                output = data.get("stt_output") or {}
                stt_text = str(output.get("text") or "").strip()
            elif kind == PipelineEventType.INTENT_END:
                done["intent"] = True
                output = data.get("intent_output") or {}
                response = (output.get("response") or {}).get("speech") or {}
                plain = response.get("plain") or {}
                speech = str(plain.get("speech") or "").strip()
            elif kind == PipelineEventType.TTS_END:
                done["tts"] = True
                output = data.get("tts_output") or {}
                relative = str(output.get("url") or "").strip()
                if relative:
                    audio_url = relative if relative.startswith("http") else f"{get_url(hass)}{relative}"
            elif kind == PipelineEventType.ERROR:
                error = str(data.get("message") or data.get("code") or "语音处理失败")
        except Exception:  # noqa: BLE001 - 回调里不能把流水线打断
            _LOGGER.exception("处理语音流水线事件失败")

    # callback 是普通函数，需要手动包成 HA 的 callback 才能被事件循环识别
    wrapped = callback(event_callback)

    try:
        await async_pipeline_from_audio_stream(
            hass=hass,
            context=Context(),
            event_callback=wrapped,
            stt_metadata=metadata,
            stt_stream=stream(),
            pipeline_id=str(getattr(pipeline, "id", "") or "") or None,
            start_stage=PipelineStage.STT,
            end_stage=PipelineStage.TTS,
            tts_audio_output=None,
        )
    except Exception as err:  # noqa: BLE001
        raise ValueError(f"语音处理失败: {err}") from err

    if error:
        raise ValueError(error)
    if not speech:
        speech = stt_text or FALLBACK
    if not audio_url and speech:
        audio_url = await _async_tts_url(hass, pipeline, speech) or ""
    return {
        "speech": speech,
        "text": stt_text,
        "audio": audio_url or None,
        "pipeline": str(getattr(pipeline, "id", "") or ""),
        "language": str(getattr(pipeline, "language", "") or hass.config.language),
        "voice": True,
    }


async def async_assistant(hass, text: str) -> str:
    """兼容旧调用：只返回文字。"""
    result = await async_assistant_result(hass, text)
    return str(result.get("speech") or FALLBACK)
