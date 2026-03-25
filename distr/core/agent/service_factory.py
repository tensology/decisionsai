"""
Service factory - creates LLM, TTS, and STT services from config dicts.

Extracted from session.py to reduce duplication and keep service creation
logic in one place.
"""

import logging
import os

from .constants import (
    DEFAULT_MODELS, API_KEY_NAMES,
    KOKORO_MODEL_FILE, KOKORO_VOICES_FILE,
    SPEED_BOUNDS, ELEVENLABS_DEFAULTS,
    DEFAULT_OPENAI_VOICE, DEFAULT_QWEN3_VOICE, DEFAULT_QWEN3_AGENT,
    DEFAULT_COQUI_VOICE, DEFAULT_COQUI_AGENT,
    KOKORO_VOICES, KOKORO_VOICE_BY_DISPLAY_NAME,
    DEFAULT_KOKORO_AGENT, DEFAULT_KOKORO_VOICE,
    DEFAULT_OPENAI_AGENT, DEFAULT_ELEVENLABS_AGENT,
    QWEN3_PRESETS, TTS_COQUI,
)

logger = logging.getLogger(__name__)

# Service class imports (lazy, same pattern as session.py)
from .services import (
    WhisperSTTService, OllamaLLMService, KokoroTTSService, ElevenLabsTTSService,
)
try:
    from .services import (
        OpenAITTSService, OpenAILLMService, OpenRouterLLMService,
        AnthropicLLMService, GroqLLMService, KiloCodeLLMService,
    )
except ImportError:
    OpenAITTSService = None
    OpenAILLMService = None
    OpenRouterLLMService = None
    AnthropicLLMService = None
    GroqLLMService = None
    KiloCodeLLMService = None
try:
    from .services import Qwen3TTSService
except ImportError:
    Qwen3TTSService = None

try:
    from .services import CoquiTTSService
except ImportError:
    CoquiTTSService = None

from .libs import ElevenLabs

# Maps engine name -> (ServiceClass, required_import_label)
_LLM_ENGINE_MAP = {
    'ollama':     (OllamaLLMService,     None),
    'openai':     (OpenAILLMService,      "OpenAILLMService"),
    'openrouter': (OpenRouterLLMService,  "OpenRouterLLMService"),
    'anthropic':  (AnthropicLLMService,   "AnthropicLLMService"),
    'groq':       (GroqLLMService,        "GroqLLMService"),
    'kilocode':   (KiloCodeLLMService,    "KiloCodeLLMService"),
}


def _normalize_api_key(raw_key: str) -> str:
    """Normalize API keys copied from docs/headers into plain token form."""
    key = (raw_key or "").strip()
    if key.lower().startswith("bearer "):
        # Users frequently paste "Bearer <token>" from curl examples.
        return key[7:].strip()
    return key


def create_llm_service(llm_config, *, role, agent_name, event_queue, is_listening,
                        chat_manager, command_queue, confirmation_results_dict,
                        is_hands_free, voice_enabled, tts_service=None):
    """Create an LLM service from a config dict.

    Returns the newly created service instance.
    """
    engine = llm_config['engine']
    system_prompt = role if role else llm_config.get('system_prompt')
    common = dict(
        system_prompt=system_prompt,
        event_queue=event_queue,
        is_listening=is_listening,
        agent_name=agent_name,
        chat_manager=chat_manager,
        command_queue=command_queue,
        confirmation_results_dict=confirmation_results_dict,
    )

    entry = _LLM_ENGINE_MAP.get(engine)
    if not entry:
        raise ValueError(f"Unsupported LLM engine: {engine}")

    cls, label = entry
    if not cls:
        raise ImportError(f"{label} is not available. Please ensure the required library is installed.")

    if engine == 'ollama':
        model_name = llm_config.get('model_name', DEFAULT_MODELS['ollama'])
        if not model_name or not model_name.strip():
            model_name = DEFAULT_MODELS['ollama']
        service = cls(model_name=model_name, **common)
    else:
        api_key = _normalize_api_key(llm_config.get('api_key', ''))
        if not api_key:
            raise ValueError(f"{label.replace('LLMService', '')} API key is required")
        model_name = llm_config.get('model_name', DEFAULT_MODELS.get(engine, ''))
        service = cls(api_key=api_key, model_name=model_name, **common)

    # Restore runtime state
    service.set_hands_free(is_hands_free)
    service.set_speaker_enabled(voice_enabled)
    if tts_service:
        service.set_tts_service(tts_service)

    return service


def create_tts_service(tts_config, *, settings, stt_service, is_hands_free, models_dir):
    """Create a TTS service from a config dict.

    Returns the newly created service instance.
    """
    engine = tts_config['engine']

    if engine == 'kokoro':
        kokoro_model = os.path.join(models_dir, KOKORO_MODEL_FILE)
        kokoro_voices = os.path.join(models_dir, KOKORO_VOICES_FILE)
        if not os.path.exists(kokoro_model):
            raise FileNotFoundError(f"Kokoro model not found at {kokoro_model}")
        lo, hi = SPEED_BOUNDS['kokoro']
        playback_speed = max(lo, min(hi, settings.get('playback_speed', 1.0)))

        # Resolve custom voice reference clip for Kanade voice cloning
        _ref_path = None
        _voice_name = tts_config['voice_name']
        if _voice_name and _voice_name.startswith('custom_'):
            try:
                from distr.core.db import get_session as _gs, CustomVoice as _CV
                _db_id = int(_voice_name.split('_', 1)[1])
                _sess = _gs()
                try:
                    _cv = _sess.query(_CV).filter(
                        _CV.id == _db_id, _CV.provider == 'kokoro', _CV.status == 'ready'
                    ).first()
                    if _cv and _cv.audio_dir:
                        for _fn in os.listdir(_cv.audio_dir):
                            if _fn.lower().endswith(('.wav', '.mp3', '.m4a', '.ogg', '.flac', '.webm')):
                                _ref_path = os.path.join(_cv.audio_dir, _fn)
                                break
                finally:
                    _sess.close()
            except Exception:
                pass
            _voice_name = 'af_heart'  # good base voice for cloning

        service = KokoroTTSService(
            model_path=kokoro_model,
            voices_path=kokoro_voices,
            voice_name=_voice_name,
            stt_service=stt_service,
            playback_speed=playback_speed,
            event_queue=settings.get('_event_queue'),
            speech_volume=100,
            reference_voice_path=_ref_path,
        )
    elif engine == 'elevenlabs':
        api_key = tts_config.get('api_key', '')
        voice_id_or_name = tts_config.get('voice_id', '')
        if not api_key:
            raise ValueError("ElevenLabs API key is required")
        if not voice_id_or_name:
            raise ValueError("ElevenLabs voice ID is required")
        voice_id, voice_name = resolve_elevenlabs_voice(api_key, voice_id_or_name)
        playback_speed = settings.get('playback_speed', 1.0)
        service = ElevenLabsTTSService(
            api_key=api_key,
            voice_id=voice_id,
            voice_name=voice_name,
            stt_service=stt_service,
            playback_speed=playback_speed,
            event_queue=settings.get('_event_queue'),
            speech_volume=100,
            stability=float(settings.get('elevenlabs_stability', ELEVENLABS_DEFAULTS['stability'])),
            similarity_boost=float(settings.get('elevenlabs_similarity_boost', ELEVENLABS_DEFAULTS['similarity_boost'])),
            style=float(settings.get('elevenlabs_style', ELEVENLABS_DEFAULTS['style'])),
            use_speaker_boost=bool(settings.get('elevenlabs_use_speaker_boost', ELEVENLABS_DEFAULTS['use_speaker_boost'])),
            on_quota_exceeded=settings.get('_on_quota_exceeded'),
        )
        # Stash resolved voice name on the service so caller can read it
        service._resolved_voice_name = voice_name
    elif engine == 'openai':
        api_key = tts_config.get('api_key', '')
        voice_id = tts_config.get('voice_id', DEFAULT_OPENAI_VOICE)
        if not api_key:
            raise ValueError("OpenAI API key is required for TTS")
        if not OpenAITTSService:
            raise ImportError("OpenAITTSService is not available")
        lo, hi = SPEED_BOUNDS['openai']
        playback_speed = max(lo, min(hi, settings.get('playback_speed', 1.0)))
        service = OpenAITTSService(
            api_key=api_key,
            voice_id=voice_id,
            voice_name=voice_id,
            stt_service=stt_service,
            playback_speed=playback_speed,
            event_queue=settings.get('_event_queue'),
            speech_volume=100,
        )
    elif engine == 'qwen3':
        if not Qwen3TTSService:
            raise ImportError("Qwen3TTSService is not available. Install with: pip install qwen-tts")
        voice_id = tts_config.get('voice_id', DEFAULT_QWEN3_VOICE)
        voice_name = tts_config.get('voice_name') or voice_id
        model_name = tts_config.get('model_name') or settings.get('qwen3_model_name') or None
        device = tts_config.get('device') or settings.get('qwen3_device') or None
        lo, hi = SPEED_BOUNDS['qwen3']
        playback_speed = max(lo, min(hi, settings.get('playback_speed', 1.0)))
        service = Qwen3TTSService(
            voice_id=voice_id,
            voice_name=voice_name,
            model_name=model_name,
            device=device,
            stt_service=stt_service,
            playback_speed=playback_speed,
            event_queue=settings.get('_event_queue'),
            speech_volume=100,
        )
    elif engine == 'coqui':
        if not CoquiTTSService:
            raise ImportError("CoquiTTSService is not available. Install with: pip install TTS")
        voice_id = tts_config.get('voice_id', DEFAULT_COQUI_VOICE)
        voice_name = tts_config.get('voice_name') or voice_id
        device = tts_config.get('device') or settings.get('coqui_device') or None
        lo, hi = SPEED_BOUNDS['coqui']
        playback_speed = max(lo, min(hi, settings.get('playback_speed', 1.0)))
        service = CoquiTTSService(
            voice_id=voice_id,
            voice_name=voice_name,
            device=device,
            stt_service=stt_service,
            playback_speed=playback_speed,
            event_queue=settings.get('_event_queue'),
            speech_volume=100,
        )
    else:
        raise ValueError(f"Unsupported TTS engine: {engine}")

    service.set_hands_free(is_hands_free)
    return service


def resolve_elevenlabs_voice(api_key, voice_id_or_name):
    """Resolve an ElevenLabs voice ID or name to (voice_id, voice_name).

    Tries: exact ID match, then case-insensitive name match, then first available voice.
    """
    if not ElevenLabs:
        raise ImportError("ElevenLabs library not installed")

    client = ElevenLabs(api_key=api_key)
    voices = client.voices.get_all().voices
    if not voices:
        raise ValueError("No voices available in ElevenLabs account")

    voice_id = None
    voice_name = voice_id_or_name

    is_likely_id = (
        len(voice_id_or_name) >= 15
        and ' ' not in voice_id_or_name
        and voice_id_or_name.replace('_', '').replace('-', '').isalnum()
    )

    # Match by ID
    if is_likely_id:
        for v in voices:
            if v.voice_id == voice_id_or_name:
                voice_id, voice_name = v.voice_id, v.name
                break

    # Match by name (case-insensitive)
    if not voice_id:
        needle = voice_id_or_name.lower().strip()
        for v in voices:
            if v.name.lower().strip() == needle:
                voice_id, voice_name = v.voice_id, v.name
                break

    # Fallback to first available
    if not voice_id:
        logger.warning(
            "Could not find voice '%s' in available voices: %s. Using first available.",
            voice_id_or_name, [v.name for v in voices],
        )
        voice_id, voice_name = voices[0].voice_id, voices[0].name

    return voice_id, voice_name


def resolve_voice_to_display_name(voice_provider: str, voice_model: str, settings: dict) -> str:
    """Resolve voice ID to human-readable display name for Kokoro, OpenAI, ElevenLabs.

    ElevenLabs stores voice IDs (e.g. EOWOXNvpbg3D1ZJDPJCF); we resolve via API to name.
    Returns the display name for UI and agent identity.
    """
    vp = (voice_provider or '').strip().lower()
    vm = (voice_model or '').strip()
    if not vm:
        if 'kokoro' in vp:
            v = (settings or {}).get('kokoro_voice', DEFAULT_KOKORO_VOICE)
            if v and v.startswith('custom_'):
                try:
                    from distr.core.db import get_session, CustomVoice
                    db_id = int(v.split('_', 1)[1])
                    session = get_session()
                    try:
                        cv = session.query(CustomVoice).filter(CustomVoice.id == db_id).first()
                        if cv:
                            return cv.name
                    finally:
                        session.close()
                except Exception:
                    pass
                return DEFAULT_KOKORO_AGENT
            return KOKORO_VOICES.get(v, v) if v else DEFAULT_KOKORO_AGENT
        if 'openai' in vp:
            v = (settings or {}).get('openai_voice', DEFAULT_OPENAI_VOICE)
            return (v or DEFAULT_OPENAI_AGENT).capitalize()
        if 'elevenlabs' in vp:
            return (settings or {}).get('elevenlabs_voice', '') or DEFAULT_ELEVENLABS_AGENT
        if 'qwen3' in vp:
            v = (settings or {}).get('qwen3_voice', DEFAULT_QWEN3_VOICE)
            if v and v.startswith('custom_'):
                try:
                    from distr.core.db import get_session, CustomVoice
                    db_id = int(v.split('_', 1)[1])
                    session = get_session()
                    try:
                        cv = session.query(CustomVoice).filter(CustomVoice.id == db_id).first()
                        if cv:
                            return cv.name
                    finally:
                        session.close()
                except Exception:
                    pass
                return DEFAULT_QWEN3_AGENT
            for pr in QWEN3_PRESETS:
                if pr.get('id') == v:
                    return (pr.get('name') or v).split(' ')[0] if pr.get('name') else v.capitalize()
            return (v or DEFAULT_QWEN3_AGENT).capitalize()
        if 'coqui' in vp:
            v = (settings or {}).get('coqui_voice', DEFAULT_COQUI_VOICE)
            try:
                from distr.core.agent.constants import COQUI_VOICES
                return COQUI_VOICES.get(v, v)
            except Exception:
                return v or DEFAULT_COQUI_AGENT
        return DEFAULT_KOKORO_AGENT
    if 'kokoro' in vp:
        if vm.startswith('custom_'):
            try:
                from distr.core.db import get_session, CustomVoice
                db_id = int(vm.split('_', 1)[1])
                session = get_session()
                try:
                    cv = session.query(CustomVoice).filter(CustomVoice.id == db_id).first()
                    if cv:
                        return cv.name
                finally:
                    session.close()
            except Exception:
                pass
            return DEFAULT_KOKORO_AGENT
        if vm in KOKORO_VOICE_BY_DISPLAY_NAME:
            vm = KOKORO_VOICE_BY_DISPLAY_NAME[vm]
        return KOKORO_VOICES.get(vm, vm)
    if 'openai' in vp:
        return vm.capitalize()
    if 'qwen3' in vp:
        if vm.startswith('custom_'):
            try:
                from distr.core.db import get_session, CustomVoice
                db_id = int(vm.split('_', 1)[1])
                session = get_session()
                try:
                    cv = session.query(CustomVoice).filter(CustomVoice.id == db_id).first()
                    if cv:
                        return cv.name
                finally:
                    session.close()
            except Exception:
                pass
            return DEFAULT_QWEN3_AGENT
        vm_lower = vm.lower()
        for pr in QWEN3_PRESETS:
            if pr.get('id') == vm_lower:
                name = pr.get('name') or vm
                return name.split(' ')[0] if ' ' in name else name
        return vm.capitalize()
    if 'coqui' in vp:
        try:
            from distr.core.agent.constants import COQUI_VOICES
            return COQUI_VOICES.get(vm, vm)
        except Exception:
            return vm or DEFAULT_COQUI_AGENT
    if 'elevenlabs' in vp:
        # Check custom voices DB first (fast, no API call)
        try:
            from distr.core.db import get_session
            from sqlalchemy import text as sa_text
            session = get_session()
            try:
                row = session.execute(sa_text(
                    "SELECT name FROM custom_voices "
                    "WHERE provider = 'elevenlabs' AND provider_voice_id = :vid AND status = 'ready' LIMIT 1"
                ), {"vid": vm}).fetchone()
                if row:
                    return row[0]
            finally:
                session.close()
        except Exception:
            pass
        api_key = ((settings or {}).get('elevenlabs_key') or '').strip()
        if not api_key:
            return vm
        try:
            _, voice_name = resolve_elevenlabs_voice(api_key, vm)
            return voice_name
        except Exception as e:
            logger.debug("Could not resolve ElevenLabs voice %s: %s", vm[:20], e)
            return vm
    return vm


def resolve_agent_name_from_tts_config(tts_config: dict, settings: dict) -> str:
    """Derive agent display name from a TTS config dict.

    Single entry point that replaces the duplicated per-engine if/elif blocks
    in __init__, _create_services, and _determine_agent_name.
    """
    engine = (tts_config.get('engine') or '').strip().lower()
    if engine == 'kokoro':
        vm = tts_config.get('voice_name', DEFAULT_KOKORO_VOICE)
        return resolve_voice_to_display_name('kokoro', vm, settings)
    if engine == 'elevenlabs':
        vm = tts_config.get('voice_id', '') or (settings or {}).get('elevenlabs_voice', '')
        return resolve_voice_to_display_name('elevenlabs', vm, settings)
    if engine == 'openai':
        vm = tts_config.get('voice_id', DEFAULT_OPENAI_VOICE)
        return resolve_voice_to_display_name('openai', vm, settings)
    if engine == 'qwen3':
        vm = tts_config.get('voice_id', '') or (settings or {}).get('qwen3_voice', '')
        return resolve_voice_to_display_name('qwen3', vm, settings)
    if engine == 'coqui':
        vm = tts_config.get('voice_id', DEFAULT_COQUI_VOICE)
        return resolve_voice_to_display_name('coqui', vm, settings)
    # Unknown engine — fall back to kokoro from settings
    return resolve_voice_to_display_name('kokoro', '', settings)


def update_agent_name_on_llm(llm_service, agent_name, role):
    """Update agent name, persona/role, and system prompt on an LLM service.

    Consolidates the 3 identical ~28-line blocks from _create_services.
    """
    if not llm_service:
        return
    if hasattr(llm_service, 'set_agent_name'):
        llm_service.set_agent_name(agent_name)
    else:
        llm_service._agent_name = agent_name

    # Update persona (role) in LLM service and rebuild system prompt
    if hasattr(llm_service, '_persona'):
        llm_service._persona = role
        if hasattr(llm_service, 'default_template'):
            final_prompt = f"{role}\n\n{llm_service.default_template}"
            # Update _system_prompt so services that use it directly (e.g. Anthropic) also get the persona
            llm_service._system_prompt = final_prompt
            if hasattr(llm_service, '_messages') and llm_service._messages:
                llm_service._messages[0] = {"role": "system", "content": final_prompt}
                logger.info("Updated LLM service system prompt with new role for '%s'", agent_name)
    elif hasattr(llm_service, '_setup_system_prompt'):
        llm_service._setup_system_prompt(role)


def swap_processor_in_pipeline(pipeline, old_service, new_service):
    """Replace a processor in a Pipecat pipeline and re-link the frame chain.

    Searches _processors, _services, and common attribute names.
    After replacing in the list:
      1. Re-links _next/_prev pointers so frames flow through the new service.
      2. Copies _task_manager and _clock from the old service so the new one
         can create async tasks without a StartFrame.
      3. Spins up the input and process drain tasks on the new service.
    Returns True if replaced, False otherwise.
    """
    if pipeline is None or old_service is None:
        return False

    for attr_name in ('_processors', '_services', 'processors', 'services'):
        proc_list = getattr(pipeline, attr_name, None)
        if not isinstance(proc_list, list):
            continue
        for i, proc in enumerate(proc_list):
            if proc is old_service:
                proc_list[i] = new_service
                logger.info("Replaced processor at index %d in pipeline.%s", i, attr_name)

                # --- 1. Re-link _next/_prev so frames route through the new service ---
                prev_proc = getattr(old_service, '_prev', None)
                next_proc = getattr(old_service, '_next', None)

                if prev_proc is not None:
                    prev_proc._next = new_service
                    new_service._prev = prev_proc
                    logger.info("Re-linked _next on %s -> %s", type(prev_proc).__name__, type(new_service).__name__)

                if next_proc is not None:
                    new_service._next = next_proc
                    next_proc._prev = new_service
                    logger.info("Re-linked _prev on %s <- %s", type(next_proc).__name__, type(new_service).__name__)

                # Detach old service so it no longer receives frames
                old_service._prev = None
                old_service._next = None

                # --- 2. Copy task infrastructure from old service ---
                # _task_manager and _clock are set by setup() which requires a StartFrame.
                # We copy them directly so create_task() works on the new service.
                for attr in ('_task_manager', '_clock', '_observer'):
                    val = getattr(old_service, attr, None)
                    if val is not None:
                        setattr(new_service, attr, val)

                # --- 3. Start the frame drain tasks on the new service ---
                # These are normally started by setup() → __create_input_task() and
                # __start() → __create_process_task(). We call the private methods
                # directly using name-mangled access.
                try:
                    create_input = getattr(new_service, '_FrameProcessor__create_input_task', None)
                    if create_input:
                        create_input()
                        logger.info("HOT-SWAP: started input task on %s", type(new_service).__name__)
                except Exception as e:
                    logger.warning("HOT-SWAP: could not start input task on %s: %s", type(new_service).__name__, e)

                try:
                    create_process = getattr(new_service, '_FrameProcessor__create_process_task', None)
                    if create_process:
                        create_process()
                        logger.info("HOT-SWAP: started process task on %s", type(new_service).__name__)
                except Exception as e:
                    logger.warning("HOT-SWAP: could not start process task on %s: %s", type(new_service).__name__, e)

                return True
    return False
