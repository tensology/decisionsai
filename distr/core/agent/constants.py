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

# --- TTS Provider Registry ---
# Single source of truth for all TTS providers. UI, backend, and service factory
# all read from this. To add a new provider, add an entry here and create the
# corresponding service class + /api/voices/<id> endpoint.
#   id:            internal key (used in settings DB, API, JS)
#   name:          human-readable label shown in UI dropdown
#   type:          "offline" or "online"
#   enabled:       False to hide from UI (e.g. missing Python support)
#   default_voice: voice id selected by default
#   settings_key:  DB key that stores the user's chosen voice for this provider
TTS_PROVIDERS = [
    {
        "id": "kokoro",
        "name": TTS_KOKORO,
        "type": "offline",
        "enabled": True,
        "default_voice": "af_heart",
        "settings_key": "kokoro_voice",
        "supports_custom_voices": True,
        "custom_voice_limit": 0,
    },
    {
        "id": "elevenlabs",
        "name": TTS_ELEVENLABS,
        "type": "online",
        "enabled": True,
        "default_voice": "default",
        "settings_key": "elevenlabs_voice",
        "supports_custom_voices": True,
        "custom_voice_limit": 5,
    },
    {
        "id": "openai",
        "name": TTS_OPENAI,
        "type": "online",
        "enabled": True,
        "default_voice": "alloy",
        "settings_key": "openai_voice",
        "supports_custom_voices": False,
    },
    {
        "id": "coqui",
        "name": TTS_COQUI,
        "type": "offline",
        "enabled": False,  # Coqui TTS package doesn't support Python 3.12+
        "default_voice": "p225",
        "settings_key": "coqui_voice",
        "supports_custom_voices": False,
    },
]

# Quick lookups derived from the registry
TTS_PROVIDER_BY_ID = {p["id"]: p for p in TTS_PROVIDERS}
TTS_ENABLED_IDS = [p["id"] for p in TTS_PROVIDERS if p["enabled"]]

DEFAULT_COQUI_VOICE = "p225"
DEFAULT_COQUI_AGENT = "Sarah"

# Coqui VCTK voices — loaded from playground/coqui-ai-voices.json
# Keyed by speaker ID (e.g. "p225") -> display name (e.g. "Sarah")
import json as _json, os as _os
_COQUI_JSON = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), '../../../../playground/coqui-ai-voices.json')
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
    _ollama_rec = {"conversational": "qwen3:8b", "coding": "qwen2.5-coder:7b", "vision": "qwen3-vl:2b"}

DEFAULT_MODELS = {
    "ollama": _ollama_rec["conversational"],
    "openai": "gpt-4o",
    "anthropic": "claude-3-5-sonnet-20241022",
    "openrouter": "openai/gpt-4o",
    "groq": "llama-3.3-70b-versatile",
    "kilocode": "anthropic/claude-opus-4.5",
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
}
ENGINE_TO_PROVIDER = {v: k for k, v in PROVIDER_TO_ENGINE.items()}

# --- API Key Names ---
API_KEY_NAMES = {
    "openai": "openai_key",
    "anthropic": "anthropic_key",
    "groq": "groq_key",
    "openrouter": "openrouter_key",
    "kilocode": "kilo_key",
}

# --- Kokoro TTS Files ---
KOKORO_MODEL_FILE = "kokoro-v1.0.onnx"
KOKORO_VOICES_FILE = "voices-v1.0.bin"

# --- Audio Sample Rates ---
SAMPLE_RATE_INPUT = 16000
SAMPLE_RATE_KOKORO = 24000
SAMPLE_RATE_OPENAI_TTS = 24000
SAMPLE_RATE_ELEVENLABS = 44100
SAMPLE_RATE_COQUI = 22050

# Engine -> output sample rate (used by _load_config and transport setup)
TTS_SAMPLE_RATES = {
    'kokoro': SAMPLE_RATE_KOKORO,
    'openai': SAMPLE_RATE_OPENAI_TTS,
    'elevenlabs': SAMPLE_RATE_ELEVENLABS,
    'coqui': SAMPLE_RATE_COQUI,
}

# --- Playback Speed Bounds ---
SPEED_BOUNDS = {
    "kokoro": (0.5, 2.0),
    "elevenlabs": (0.7, 1.2),
    "openai": (0.25, 4.0),
    "coqui": (0.5, 2.0),
}

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
    and partial matches. Returns lowercase id: kokoro, elevenlabs, openai, qwen3, coqui.
    """
    v = (raw or '').strip().lower()
    if 'kokoro' in v:
        return 'kokoro'
    if 'elevenlabs' in v:
        return 'elevenlabs'
    if 'openai' in v:
        return 'openai'
    if 'coqui' in v:
        return 'coqui'
    return v or 'kokoro'
