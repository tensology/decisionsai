import os
import sys
from pathlib import Path

CORE_DIR = os.path.join(os.path.dirname(__file__), "..", "..")
DISTR_DIR = os.path.join(os.path.dirname(__file__), "..")


def user_data_root(*, platform: str | None = None, home: Path | None = None) -> Path:
    """Return the writable per-user root used by installed application builds."""
    current_platform = platform or sys.platform
    base_home = home or Path.home()
    explicit = (os.environ.get("DECISIONS_DATA_DIR") or "").strip()
    if explicit:
        return Path(explicit).expanduser()
    if current_platform == "darwin":
        return base_home / "Library" / "Application Support" / "DecisionsAI"
    if current_platform == "win32":
        local_app_data = os.environ.get("LOCALAPPDATA")
        return Path(local_app_data) / "DecisionsAI" if local_app_data else base_home / "AppData" / "Local" / "DecisionsAI"
    xdg_data_home = os.environ.get("XDG_DATA_HOME")
    return Path(xdg_data_home) / "decisionsai" if xdg_data_home else base_home / ".local" / "share" / "decisionsai"


IS_FROZEN = bool(getattr(sys, "frozen", False))
DATA_DIR = str(user_data_root()) if IS_FROZEN or os.environ.get("DECISIONS_DATA_DIR") else CORE_DIR

MODELS_DIR = os.environ.get("DECISIONS_MODELS_DIR") or os.path.join(DATA_DIR, "models")

# Cross-chat durable memory (AGENT.md, USER.md, MEMORY.md, EVENTS.md) — R8
MEMORY_FILES_DIR = os.path.join(MODELS_DIR, "memory")

# MCP server definitions (R5) — atomic JSON alongside models / DB
MCP_CONFIG_PATH = os.path.join(MODELS_DIR, "mcp_config.json")

# Integration message bus — (platform, thread_id) → chat_id mapping (R15)
MESSAGE_BUS_MAPPING_PATH = os.path.join(MODELS_DIR, "integration_message_bus_mapping.json")

ASSETS_DIR = os.path.join(CORE_DIR, "assets")

DB_DIR = os.environ.get("DECISIONS_DB_DIR") or os.path.join(DATA_DIR, "db")

SECRETS_DIR = os.environ.get("DECISIONS_SECRETS_DIR") or os.path.join(DATA_DIR, "secrets")
GOOGLE_OAUTH_SECRET_PATH = os.path.join(SECRETS_DIR, "google_oauth_client_secret.json")

IMAGES_DIR = os.path.join(ASSETS_DIR, "img")
ICONS_DIR = os.path.join(IMAGES_DIR, "icons")
TMP_DIR = os.environ.get("DECISIONS_TMP_DIR") or os.path.join(DATA_DIR, "tmp")

AVATARS_DIR = os.path.join(ASSETS_DIR, "avatars")

ORACLE_DIR = os.path.join(AVATARS_DIR, "oracle")

RECORDINGS_DIR = os.environ.get("DECISIONS_RECORDINGS_DIR") or os.path.join(DATA_DIR, "recordings")

DEFAULT_SILENCE_TIMER = 2

WHISPER_MODEL_SIZE = "base.en"
WHISPER_MODEL_PATH = os.path.join(MODELS_DIR, WHISPER_MODEL_SIZE)

WHISPER_LANGUAGES = {
    "afrikaans": "af",
    "albanian": "sq",
    "amharic": "am",
    "arabic": "ar",
    "armenian": "hy",
    "azerbaijani": "az",
    "basque": "eu",
    "belarusian": "be",
    "bengali": "bn",
    "bosnian": "bs",
    "bulgarian": "bg",
    "catalan": "ca",
    "cebuano": "ceb",
    "chichewa": "ny",
    "chinese (simplified)": "zh-CN",
    "chinese (traditional)": "zh-TW",
    "corsican": "co",
    "croatian": "hr",
    "czech": "cs",
    "danish": "da",
    "dutch": "nl",
    "english": "en",
    "esperanto": "eo",
    "estonian": "et",
    "filipino": "tl",
    "finnish": "fi",
    "french": "fr",
    "frisian": "fy",
    "galician": "gl",
    "georgian": "ka",
    "german": "de",
    "greek": "el",
    "gujarati": "gu",
    "haitian creole": "ht",
    "hausa": "ha",
    "hawaiian": "haw",
    "hebrew": "he",
    "hindi": "hi",
    "hmong": "hmn",
    "hungarian": "hu",
    "icelandic": "is",
    "igbo": "ig",
    "indonesian": "id",
    "irish": "ga",
    "italian": "it",
    "japanese": "ja",
    "javanese": "jw",
    "kannada": "kn",
    "kazakh": "kk",
    "khmer": "km",
    "korean": "ko",
    "kurdish (kurmanji)": "ku",
    "kyrgyz": "ky",
    "lao": "lo",
    "latin": "la",
    "latvian": "lv",
    "lithuanian": "lt",
    "luxembourgish": "lb",
    "macedonian": "mk",
    "malagasy": "mg",
    "malay": "ms",
    "malayalam": "ml",
    "maltese": "mt",
    "maori": "mi",
    "marathi": "mr",
    "mongolian": "mn",
    "myanmar (burmese)": "my",
    "nepali": "ne",
    "norwegian": "no",
    "odia": "or",
    "pashto": "ps",
    "persian": "fa",
    "polish": "pl",
    "portuguese": "pt",
    "punjabi": "pa",
    "romanian": "ro",
    "russian": "ru",
    "samoan": "sm",
    "scots gaelic": "gd",
    "serbian": "sr",
    "sesotho": "st",
    "shona": "sn",
    "sindhi": "sd",
    "sinhala": "si",
    "slovak": "sk",
    "slovenian": "sl",
    "somali": "so",
    "spanish": "es",
    "sundanese": "su",
    "swahili": "sw",
    "swedish": "sv",
    "tajik": "tg",
    "tamil": "ta",
    "telugu": "te",
    "thai": "th",
    "turkish": "tr",
    "ukrainian": "uk",
    "urdu": "ur",
    "uzbek": "uz",
    "vietnamese": "vi",
    "welsh": "cy",
    "xhosa": "xh",
    "yiddish": "yi",
    "yoruba": "yo",
    "zulu": "zu",
}

# NOTE: The CORRECTIONS dict below is intentionally commented out. It was originally
# intended as a phoneme correction map for Kokoro TTS mispronunciations (e.g.
# "screen"→"scream", "pause"→"paws") but was never wired to any TTS call site.
# If phoneme correction is needed in the future, add a per-voice config with
# context-aware gating rather than a global substitution map.
#
# CORRECTIONS = {
#     "moss": "mouse",
#     "moose": "mouse",
#     "curse": "cursor",
#     "curser": "cursor",
#     "right": "write",
#     "rite": "right",
#     "left": "lift",
#     "center": "sender",
#     "middle": "medal",
#     "scroll": "stroll",
#     "move": "mood",
#     "press": "dress",
#     "enter": "inter",
#     "delete": "dilute",
#     "space": "pace",
#     "tab": "tap",
#     "escape": "cape",
#     "copy": "coffee",
#     "paste": "taste",
#     "cut": "cup",
#     "undo": "unto",
#     "redo": "read",
#     "select": "collect",
#     "find": "fine",
#     "replace": "replay",
#     "save": "safe",
#     "open": "hoping",
#     "close": "clothes",
#     "quit": "quick",
#     "exit": "exist",
#     "minimize": "minimized",
#     "maximize": "maximized",
#     "restore": "restorer",
#     "refresh": "fresh",
#     "reload": "reloaded",
#     "zoom": "boom",
#     "screen": "scream",
#     "print": "sprint",
#     "mute": "moot",
#     "unmute": "unmoot",
#     "volume": "volumes",
#     "play": "lay",
#     "pause": "paws",
#     "stop": "top",
#     "next": "nest",
#     "previous": "previews",
#     "forward": "foreword",
#     "backward": "backwards",
#     "rewind": "remind",
#     "fast": "last",
# }
#
# --- end of commented CORRECTIONS block ---
