"""
Service factory - creates LLM, TTS, and STT services from config dicts.

Extracted from session.py to reduce duplication and keep service creation
logic in one place.
"""

import logging

from .constants import DEFAULT_MODELS, API_KEY_NAMES

logger = logging.getLogger(__name__)

# LLM service class imports
from .services import WhisperSTTService, OllamaLLMService
try:
    from .services import (
        OpenAILLMService, OpenRouterLLMService,
        AnthropicLLMService, GroqLLMService, KiloCodeLLMService,
        GeminiLLMService,
    )
except ImportError:
    OpenAILLMService = None
    OpenRouterLLMService = None
    AnthropicLLMService = None
    GroqLLMService = None
    KiloCodeLLMService = None
    GeminiLLMService = None

from .libs import ElevenLabs

# TTS provider registry — replaces per-provider if/elif chains
from distr.core.agent.services.tts.registry import tts_registry

# Maps engine name -> (ServiceClass, required_import_label)
_LLM_ENGINE_MAP = {
    'ollama':     (OllamaLLMService,     None),
    'openai':     (OpenAILLMService,      "OpenAILLMService"),
    'openrouter': (OpenRouterLLMService,  "OpenRouterLLMService"),
    'anthropic':  (AnthropicLLMService,   "AnthropicLLMService"),
    'groq':       (GroqLLMService,        "GroqLLMService"),
    'kilocode':   (KiloCodeLLMService,    "KiloCodeLLMService"),
    'gemini':     (GeminiLLMService,      "GeminiLLMService"),
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

    Dispatches to the appropriate provider descriptor via the TTS registry.
    Returns the newly created service instance.
    """
    engine = tts_config['engine']

    try:
        descriptor = tts_registry.get(engine)
    except KeyError:
        raise ValueError(f"Unsupported TTS engine: {engine}")

    # Each descriptor's create_service() returns a fully initialised service
    # (including set_hands_free).
    return descriptor.create_service(
        tts_config,
        settings=settings,
        stt_service=stt_service,
        is_hands_free=is_hands_free,
        models_dir=models_dir,
    )


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
    """Resolve voice ID to human-readable display name for any TTS provider.

    Dispatches to the appropriate provider descriptor via the TTS registry.
    Returns the display name for UI and agent identity.
    """
    from .constants import normalize_voice_provider as _nvp
    vp = _nvp(voice_provider)
    vm = (voice_model or '').strip()

    try:
        descriptor = tts_registry.get(vp)
        return descriptor.resolve_display_name(vm, settings)
    except KeyError:
        # Unknown provider — fall back to Kokoro default
        try:
            kokoro = tts_registry.get('kokoro')
            return kokoro.resolve_display_name('', settings)
        except KeyError:
            return vm or "Heart"


def resolve_agent_name_from_tts_config(tts_config: dict, settings: dict) -> str:
    """Derive agent display name from a TTS config dict.

    Uses the TTS registry to determine the correct voice model key for each
    provider, then delegates to resolve_voice_to_display_name().
    """
    engine = (tts_config.get('engine') or '').strip().lower()

    try:
        descriptor = tts_registry.get(engine)
    except KeyError:
        # Unknown engine — fall back to kokoro from settings
        return resolve_voice_to_display_name('kokoro', '', settings)

    # Determine the voice model value from the config.
    # Providers use either 'voice_name' or 'voice_id' as their primary key.
    # ElevenLabs also falls back to settings.
    if engine == 'elevenlabs':
        vm = tts_config.get('voice_id', '') or (settings or {}).get('elevenlabs_voice', '')
    elif engine in ('kokoro', 'f5tts', 'voxcpm'):
        vm = tts_config.get('voice_name', descriptor.default_voice)
    else:
        vm = tts_config.get('voice_id', descriptor.default_voice)

    return resolve_voice_to_display_name(engine, vm, settings)


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
