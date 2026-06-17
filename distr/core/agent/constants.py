"""
Agent session constants - voice, model, provider, and service defaults.

Lightweight file that can be imported without pulling in the heavy session module.
"""

# --- Voice Constants ---
KOKORO_VOICES = {
    "af_heart": "Heart",
    "af_alloy": "Alloy",
    "af_aoede": "Aoede",
    "af_bella": "Bella",
    "af_jessica": "Jessica",
    "af_kore": "Kore",
    "af_nicole": "Nicole",
    "af_nova": "Nova",
    "af_river": "River",
    "af_sarah": "Sarah",
    "af_sky": "Sky",
    "am_adam": "Adam",
    "am_echo": "Echo",
    "am_eric": "Eric",
    "am_fenrir": "Fenrir",
    "am_liam": "Liam",
    "am_michael": "Michael",
    "am_onyx": "Onyx",
    "am_puck": "Puck",
    "am_santa": "Santa",
}

DEFAULT_KOKORO_VOICE = "af_heart"
DEFAULT_KOKORO_AGENT = "Heart"

# Reverse mapping: display name -> Kokoro internal id (for chat.voice_model that may store "Heart" not "af_heart")
KOKORO_VOICE_BY_DISPLAY_NAME = {v: k for k, v in KOKORO_VOICES.items()}
DEFAULT_OPENAI_VOICE = "alloy"
DEFAULT_OPENAI_AGENT = "Alloy"
DEFAULT_ELEVENLABS_AGENT = "Heart"

# --- TTS Provider Display Names ---
TTS_KOKORO = "Kokoro (Offline)"
TTS_ELEVENLABS = "ElevenLabs (Online)"
TTS_OPENAI = "OpenAI (Online)"
TTS_COQUI = "Coqui TTS (Offline)"
TTS_SYSTEM = "System Default"

# --- TTS Provider Registry (derived from descriptors) ---
# TTS_PROVIDERS is now built dynamically from the TTSProviderRegistry.
# To add a new provider, create a descriptor in distr/core/agent/services/tts/
# and export DESCRIPTOR at module level — it will be auto-discovered.


def _build_tts_providers() -> list[dict]:
    """Build the TTS_PROVIDERS list from the registry for backward compatibility."""
    from distr.core.agent.services.tts.registry import (
        DISABLED_TTS_PROVIDER_IDS,
        tts_registry,
    )
    result = []
    for d in tts_registry.all_providers():
        entry = {
            "id": d.id,
            "name": d.name,
            "type": d.type,
            "enabled": d.enabled and d.id not in DISABLED_TTS_PROVIDER_IDS,
            "default_voice": d.default_voice,
            "settings_key": d.settings_key,
            "supports_custom_voices": d.supports_custom_voices,
        }
        if d.custom_voice_limit:
            entry["custom_voice_limit"] = d.custom_voice_limit
        result.append(entry)
    return result


# Lazy-initialised module-level list — built on first access via __getattr__
_TTS_PROVIDERS_CACHE: list[dict] | None = None


def _get_tts_providers() -> list[dict]:
    global _TTS_PROVIDERS_CACHE
    if _TTS_PROVIDERS_CACHE is None:
        _TTS_PROVIDERS_CACHE = _build_tts_providers()
    return _TTS_PROVIDERS_CACHE


# Expose TTS_PROVIDERS as a module-level name for backward compatibility.
# Callers that do `from constants import TTS_PROVIDERS` will get the list.
TTS_PROVIDERS: list[dict] = []  # placeholder — populated by _init_registry_derived()


def _init_registry_derived() -> None:
    """Populate module-level dicts/lists derived from the registry.

    Called once at the bottom of this module (after all static constants are defined)
    so that any import of constants.py gets the fully-populated values.

    Uses in-place mutation (.extend / .update / .clear) so that modules which
    already hold a reference via ``from constants import TTS_PROVIDERS`` see
    the updated data.
    """
    providers = _build_tts_providers()

    TTS_PROVIDERS.clear()
    TTS_PROVIDERS.extend(providers)

    TTS_PROVIDER_BY_ID.clear()
    TTS_PROVIDER_BY_ID.update({p["id"]: p for p in TTS_PROVIDERS})

    TTS_ENABLED_IDS.clear()
    TTS_ENABLED_IDS.extend(p["id"] for p in TTS_PROVIDERS if p["enabled"])

    from distr.core.agent.services.tts.registry import tts_registry
    SPEED_BOUNDS.clear()
    SPEED_BOUNDS.update({d.id: d.speed_bounds for d in tts_registry.all_providers()})

    TTS_SAMPLE_RATES.clear()
    TTS_SAMPLE_RATES.update({d.id: d.sample_rate for d in tts_registry.all_providers()})


# Quick lookups derived from the registry (populated by _init_registry_derived)
TTS_PROVIDER_BY_ID: dict[str, dict] = {}
TTS_ENABLED_IDS: list[str] = []

DEFAULT_COQUI_VOICE = "p225"
DEFAULT_COQUI_AGENT = "Sarah"

# Coqui VCTK voices — loaded from playground/coqui-ai-voices.json
# Keyed by speaker ID (e.g. "p225") -> display name (e.g. "Sarah")
import json as _json, os as _os
_COQUI_JSON = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), 'data/coqui-voices.json')
try:
    with open(_COQUI_JSON, 'r') as _f:
        _coqui_raw = _json.load(_f)
    COQUI_VOICES = {v["id"]: v["name"] for v in _coqui_raw if v.get("id") and v.get("name")}
except Exception:
    COQUI_VOICES = {"p225": "Sarah", "p226": "Tim", "p227": "Isabelle"}
del _json, _os, _COQUI_JSON

# --- Default Models Per Provider (memory-aware for Ollama) ---
try:
    from distr.core.system_resources import recommend_ollama_defaults as _rec
    _ollama_rec = _rec()
except Exception:
    _ollama_rec = {"conversational": "deepseek-v4-pro:cloud", "coding": "glm-5.1:cloud", "vision": "qwen3-vl:235b-cloud"}

DEFAULT_MODELS = {
    "ollama": _ollama_rec["conversational"],
    "openai": "gpt-4o",
    "anthropic": "claude-3-5-sonnet-20241022",
    "openrouter": "openai/gpt-4o",
    "groq": "llama-3.3-70b-versatile",
    "kilocode": "anthropic/claude-opus-4.5",
    "gemini": "gemini-2.5-flash",
    "nvidia": "meta/llama-3.3-70b-instruct",
}

# --- Default Models Per LLM Type (Ollama, memory-aware) ---
# Used when no model is configured yet for a specific LLM type.
DEFAULT_OLLAMA_MODELS_BY_TYPE = {
    "conversational": _ollama_rec["conversational"],
    "coding": _ollama_rec["coding"],
    "vision": _ollama_rec["vision"],
    "image": "x/flux2-klein:latest",
}

# --- Provider Engine Map ---
PROVIDER_TO_ENGINE = {
    "Ollama": "ollama",
    "OpenAI": "openai",
    "Anthropic": "anthropic",
    "Groq": "groq",
    "OpenRouter": "openrouter",
    "KiloCode": "kilocode",
    "Google Gemini": "gemini",
    "NVIDIA": "nvidia",
}
ENGINE_TO_PROVIDER = {v: k for k, v in PROVIDER_TO_ENGINE.items()}

# --- API Key Names ---
API_KEY_NAMES = {
    "openai": "openai_key",
    "anthropic": "anthropic_key",
    "groq": "groq_key",
    "openrouter": "openrouter_key",
    "kilocode": "kilo_key",
    "gemini": "gemini_key",
    "nvidia": "nvidia_key",
}

# --- Kokoro TTS Files ---
KOKORO_MODEL_FILE = "kokoro-v1.0.onnx"
KOKORO_VOICES_FILE = "voices-v1.0.bin"

# --- Audio Sample Rates ---
SAMPLE_RATE_INPUT = 16000
SAMPLE_RATE_KOKORO = 24000
SAMPLE_RATE_OPENAI_TTS = 24000
SAMPLE_RATE_ELEVENLABS = 44100
# Stable PortAudio output rate for local playback (Bluetooth/macOS native).
# TTS engines emit at their own rates; transport resamples to this rate.
SAMPLE_RATE_PLAYBACK = SAMPLE_RATE_ELEVENLABS
SAMPLE_RATE_COQUI = 22050

# VoxCPM sample rate: 16kHz for 0.5B (CPU/macOS), 48kHz for VoxCPM2 (CUDA).
# Default to 16kHz — the service's get_sample_rate() returns the actual rate.
SAMPLE_RATE_VOXCPM = 16000

# Engine -> output sample rate (derived from registry by _init_registry_derived)
TTS_SAMPLE_RATES: dict[str, int] = {}

# --- Playback Speed Bounds (derived from registry by _init_registry_derived) ---
SPEED_BOUNDS: dict[str, tuple[float, float]] = {}

# --- ElevenLabs Default Voice Settings ---
ELEVENLABS_DEFAULTS = {
    "stability": 0.5,
    "similarity_boost": 0.6,
    "style": 0.25,
    "use_speaker_boost": True,
}

# --- VAD Defaults ---
VAD_DEFAULT_THRESHOLD = 50
VAD_CONFIDENCE_MIN = 0.01
VAD_CONFIDENCE_MAX = 0.99
VAD_DEFAULT_CONFIDENCE = 0.5
VAD_START_SECS = 0.1

# --- STT Defaults ---
DEFAULT_OPENAI_WHISPER_MODEL = "whisper-1"
DEFAULT_ASSEMBLYAI_MODEL = "universal-2"
VALID_ASSEMBLYAI_MODELS = ["universal-2", "nano", "best"]
DEFAULT_VOSK_MODEL_DIR = "vosk-model-en-us-0.22"

# --- Misc ---
WELCOME_DELAY_SECS = 1.5
COMMAND_POLL_TIMEOUT = 0.02
DEFAULT_SPEECH_VOLUME = 100

# --- Default Agent Persona ---
# Fallback personality used when no custom voice personality is set.
DEFAULT_PERSONA = (
    "You are a voice assistant created by Tensology.\n\n"
    "About Tensology:\n"
    "- A company based in Cape Town, South Africa\n"
    "- Focuses on AI automation and building software solutions for small and medium businesses\n\n"
    "About DecisionsAI:\n"
    "- You are part of an application called DecisionsAI\n"
    "- Your primary focus is to help users with automation and getting tasks done\n"
    "- You can run system commands and help control the user's computer\n"
    "- DecisionsAI is still in early development, so you're learning and growing\n\n"
    "Your personality:\n"
    "- Friendly and warm, like chatting with a helpful colleague\n"
    "- Keep responses short and to the point unless asked for more detail\n"
    "- Honest about what you can and cannot do\n"
    "- When you're not sure about something, just say so\n\n"
    "When asked who built you, mention Tensology and that they're based in Cape Town."
)


# --- Voice Provider Normalization ---
def normalize_voice_provider(raw: str) -> str:
    """Normalize voice provider strings to canonical lowercase id.

    Handles display names like 'Kokoro (Offline)', DB values like 'kokoro',
    and partial matches. Returns lowercase id: kokoro, elevenlabs, openai, coqui, f5tts,
    voxcpm, etc. (all registered descriptor ids).

    Delegates to each registered descriptor's ``normalize_provider_name()`` method.
    Falls back to the raw value (lowered/stripped) or 'kokoro' for empty input.
    """
    from distr.core.agent.services.tts.registry import tts_registry

    v = (raw or '').strip()
    if not v:
        return 'kokoro'

    for descriptor in tts_registry.all_providers():
        result = descriptor.normalize_provider_name(v)
        if result is not None:
            return result

    # No descriptor matched — return the raw value lowered
    return v.lower() or 'kokoro'


# --- Initialise registry-derived module-level constants ---
# This must be called after all static constants are defined above.
try:
    _init_registry_derived()
except Exception:
    # If registry auto-discovery fails (e.g. during early import or testing),
    # the module-level dicts remain empty and will be populated on first use.
    pass
