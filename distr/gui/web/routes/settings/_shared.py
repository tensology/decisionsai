"""
Shared Pydantic models, constants, and helpers used across settings route modules.
"""
from functools import wraps
from pydantic import BaseModel, Field
from typing import Optional, Dict, Any, List
from pathlib import Path
import json
import logging
import os
from distr.core.hotkeys import DEFAULTS as HOTKEY_DEFAULTS

from fastapi import HTTPException
from fastapi.responses import JSONResponse

from distr.core.paths import DB_DIR
from distr.gui.web.security import (
    mask_secret,
    redact_connected_account,
    redact_thirdparty_settings,
    validate_safe_outbound_url,
    rate_limiter,
)

PROJECT_UPLOADS_DIR = os.path.join(DB_DIR, "project_uploads")

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def route_handler(label: str, *, status_code: int = 500, fallback=None):
    """Decorator that wraps an async route handler with try/except/log/HTTPException.

    Usage::

        @router.get("/foo")
        @route_handler("load foo")
        async def get_foo():
            ...

    On exception it logs ``f"Failed to {label}: {e}"`` and either returns
    *fallback* (if given) as a JSONResponse, or raises HTTPException(status_code).
    """
    def decorator(fn):
        @wraps(fn)
        async def wrapper(*args, **kwargs):
            try:
                return await fn(*args, **kwargs)
            except HTTPException:
                raise  # let FastAPI handle these directly
            except Exception as e:
                logger.error("Failed to %s: %s", label, e, exc_info=True)
                if fallback is not None:
                    return JSONResponse(fallback)
                raise HTTPException(status_code=status_code, detail=str(e))
        return wrapper
    return decorator


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def resolve_secret_update(existing_value: str, incoming_value: str) -> str:
    """Keep existing secret when client submits a blank or displayed masked value."""
    existing = (existing_value or "").strip()
    incoming = (incoming_value or "").strip()
    if not incoming:
        return existing
    if existing and incoming == mask_secret(existing):
        return existing
    return incoming


def parse_connected_accounts(settings: Dict[str, Any]) -> list:
    """Parse connected_accounts from settings (may be JSON string or list)."""
    raw = settings.get("connected_accounts") or "[]"
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except Exception:
            return []
    if isinstance(raw, list):
        return raw
    return []


# ---------------------------------------------------------------------------
# Pydantic models — Third-party / validation
# ---------------------------------------------------------------------------

class ProviderSettings(BaseModel):
    enabled: bool
    key: str = ""


class ThirdPartySettings(BaseModel):
    ollama_url: str = "http://localhost:11434/"
    assemblyai_enabled: bool = False
    assemblyai_key: str = ""
    openai_enabled: bool = False
    openai_key: str = ""
    anthropic_enabled: bool = False
    anthropic_key: str = ""
    cursor_enabled: bool = False
    cursor_key: str = ""
    elevenlabs_enabled: bool = False
    elevenlabs_key: str = ""
    openrouter_enabled: bool = False
    openrouter_key: str = ""
    groq_enabled: bool = False
    groq_key: str = ""
    kilo_enabled: bool = False
    kilo_key: str = ""
    gemini_enabled: bool = False
    gemini_key: str = ""
    masko_enabled: bool = False
    masko_key: str = ""


class ValidateRequest(BaseModel):
    provider: str
    key: str


class OllamaPullRequest(BaseModel):
    model: str
    size: Optional[str] = None


# ---------------------------------------------------------------------------
# Pydantic models — General / Audio / Oracle / Voice
# ---------------------------------------------------------------------------

class VoiceSelectionUpdate(BaseModel):
    """Persist only TTS provider + voice from chat configure UI (mirrors General voice fields)."""

    voice_provider: str = Field(min_length=1)
    voice_model: str = Field(min_length=1)


class GeneralSettings(BaseModel):
    load_splash_sound: bool = False
    show_about: bool = False
    welcome_greet_me: bool = False
    telegram_send_online_notice: bool = True
    load_on_startup: bool = True
    listening_state: str = "remember"
    voice_provider: str = "kokoro"
    kokoro_voice: str = "af_heart"
    elevenlabs_voice: str = "default"
    openai_voice: str = "alloy"
    coqui_voice: str = "p225"
    qwen3_voice: str = "aiden"
    f5tts_voice: str = "default"
    voxcpm_voice: str = "default"
    supertonic_voice: str = "M1"
    chatterbox_voice: str = "default"
    playback_speed: float = 1.0
    speech_volume: int = 100
    vad_threshold: int = 50
    elevenlabs_stability: float = 0.5
    elevenlabs_similarity_boost: float = 0.6
    elevenlabs_style: float = 0.25
    elevenlabs_use_speaker_boost: bool = True
    restore_position: bool = True
    oracle_position: str = "custom"
    global_ptt_hotkey_enabled: bool = True
    global_ptt_hotkey_combo: str = "option_command"


class ShortcutSettings(BaseModel):
    global_ptt_hotkey_enabled: bool = True
    global_ptt_hotkey_combo: str = HOTKEY_DEFAULTS["global_ptt_hotkey_combo"]
    oracle_size_hotkey_decrease_modifier: str = HOTKEY_DEFAULTS["oracle_size_hotkey_decrease_modifier"]
    oracle_size_hotkey_decrease_key: str = HOTKEY_DEFAULTS["oracle_size_hotkey_decrease_key"]
    oracle_size_hotkey_increase_modifier: str = HOTKEY_DEFAULTS["oracle_size_hotkey_increase_modifier"]
    oracle_size_hotkey_increase_key: str = HOTKEY_DEFAULTS["oracle_size_hotkey_increase_key"]
    recording_hotkey_enabled: bool = True
    recording_hotkey_modifier: str = HOTKEY_DEFAULTS["recording_hotkey_modifier"]
    recording_hotkey_key: str = HOTKEY_DEFAULTS["recording_hotkey_key"]
    skin_nav_hotkey_previous_modifier: str = HOTKEY_DEFAULTS["skin_nav_hotkey_previous_modifier"]
    skin_nav_hotkey_previous_key: str = HOTKEY_DEFAULTS["skin_nav_hotkey_previous_key"]
    skin_nav_hotkey_next_modifier: str = HOTKEY_DEFAULTS["skin_nav_hotkey_next_modifier"]
    skin_nav_hotkey_next_key: str = HOTKEY_DEFAULTS["skin_nav_hotkey_next_key"]
    skin_select_hotkey_modifier: str = HOTKEY_DEFAULTS["skin_select_hotkey_modifier"]
    web_hotkey_chat_modifier: str = HOTKEY_DEFAULTS["web_hotkey_chat_modifier"]
    web_hotkey_chat_key: str = HOTKEY_DEFAULTS["web_hotkey_chat_key"]
    web_hotkey_projects_modifier: str = HOTKEY_DEFAULTS["web_hotkey_projects_modifier"]
    web_hotkey_projects_key: str = HOTKEY_DEFAULTS["web_hotkey_projects_key"]
    web_hotkey_actions_modifier: str = HOTKEY_DEFAULTS["web_hotkey_actions_modifier"]
    web_hotkey_actions_key: str = HOTKEY_DEFAULTS["web_hotkey_actions_key"]
    web_hotkey_snippets_modifier: str = HOTKEY_DEFAULTS["web_hotkey_snippets_modifier"]
    web_hotkey_snippets_key: str = HOTKEY_DEFAULTS["web_hotkey_snippets_key"]
    web_hotkey_workflows_modifier: str = HOTKEY_DEFAULTS["web_hotkey_workflows_modifier"]
    web_hotkey_workflows_key: str = HOTKEY_DEFAULTS["web_hotkey_workflows_key"]
    web_hotkey_preferences_modifier: str = HOTKEY_DEFAULTS["web_hotkey_preferences_modifier"]
    web_hotkey_preferences_key: str = HOTKEY_DEFAULTS["web_hotkey_preferences_key"]
    dictation_hotkey_enabled: bool = HOTKEY_DEFAULTS["dictation_hotkey_enabled"]
    dictation_hotkey_modifier: str = HOTKEY_DEFAULTS["dictation_hotkey_modifier"]
    dictation_hotkey_key: str = HOTKEY_DEFAULTS["dictation_hotkey_key"]
    ticket_dictation_hotkey_enabled: bool = HOTKEY_DEFAULTS["ticket_dictation_hotkey_enabled"]
    ticket_dictation_hotkey_modifier: str = HOTKEY_DEFAULTS["ticket_dictation_hotkey_modifier"]
    ticket_dictation_hotkey_key: str = HOTKEY_DEFAULTS["ticket_dictation_hotkey_key"]
    dictation_ticket_use_llm: bool = True
    dictation_ticket_model: str = "qwen2.5:0.5b"
    dictation_ticket_timeout: str = "1.2"
    dictation_ticket_prompt: str = ""


class AudioSettings(BaseModel):
    input_device: str = "System Default"
    output_device: str = "System Default"
    remember_audio_settings: bool = False
    locked_output: Optional[str] = None
    locked_input: Optional[str] = None


class PlayVoiceRequest(BaseModel):
    provider: str
    voice: str
    speed: float = 1.0
    voice_name: Optional[str] = None


# ---------------------------------------------------------------------------
# Pydantic models — LLMs / Advanced / Initiative
# ---------------------------------------------------------------------------

class LLMSettings(BaseModel):
    stt_model: str = "whisper"
    conversational_provider: str = "ollama"
    conversational_model: str = ""
    coding_provider: str = "ollama"
    coding_model: str = ""
    vision_provider: str = "ollama"
    vision_model: str = ""
    image_provider: str = "ollama"
    image_model: str = ""
    workflow_provider: str = ""
    workflow_model: str = ""
    computer_use_provider: str = ""
    computer_use_model: str = ""
    project_cli_low_backend: str = "cursor"
    project_cli_low_model: str = "auto"
    project_cli_medium_backend: str = "codex"
    project_cli_medium_model: str = "auto"
    project_cli_high_backend: str = "codex"
    project_cli_high_model: str = "gpt-5.3-codex"
    project_cli_low_codex_intelligence: str = ""
    project_cli_low_codex_speed: str = ""
    project_cli_medium_codex_intelligence: str = ""
    project_cli_medium_codex_speed: str = ""
    project_cli_high_codex_intelligence: str = ""
    project_cli_high_codex_speed: str = ""
    instant_dictation: bool = True


class AdvancedSettings(BaseModel):
    exclude_types: str = ""
    indexed_folders: List[str] = Field(default_factory=list)
    hermes_enabled: bool = True
    hermes_memory_export_enabled: bool = False
    hermes_orchestrator_provider: str = ""
    hermes_orchestrator_model: str = ""
    hermes_validator_provider: str = ""
    hermes_validator_model: str = ""
    hermes_correction_provider: str = ""
    hermes_correction_model: str = ""


class InitiativeSettings(BaseModel):
    initiative_level: str = "assist"
    initiative_allow_telegram: bool = False
    initiative_allow_routine_tasks: bool = False
    initiative_scan_boards: bool = True
    initiative_scan_external_boards: bool = False
    initiative_scan_email: bool = False
    initiative_scan_whatsapp: bool = True
    initiative_scan_telegram: bool = True
    initiative_suggest_backlog_promotion: bool = True
    initiative_allow_ticket_lane_moves: bool = False
    initiative_allow_workflow_start: bool = False
    initiative_allow_project_cli: bool = False
    initiative_ask_external_comms: bool = True
    initiative_ask_file_changes: bool = True
    initiative_ask_sensitive: bool = True


# ---------------------------------------------------------------------------
# Pydantic models — Skins
# ---------------------------------------------------------------------------

class SkinSelectRequest(BaseModel):
    skin_name: str


class SkinConfigResponse(BaseModel):
    type: str
    name: str
    rendering: dict
    events: dict
    transitions: dict


class SkinFilesResponse(BaseModel):
    files: list[str]


class OraclePositionUpdate(BaseModel):
    oracle_position: str = "custom"


class PlaybackSpeedUpdate(BaseModel):
    playback_speed: float = Field(ge=0.5, le=2.0)


class SpeechVolumeUpdate(BaseModel):
    speech_volume: int = Field(ge=0, le=100)


class VadThresholdUpdate(BaseModel):
    vad_threshold: int = Field(ge=0, le=100)


class ElevenLabsVoiceSettingsUpdate(BaseModel):
    stability: float = Field(ge=0.0, le=1.0)
    similarity_boost: float = Field(ge=0.0, le=1.0)
    style: float = Field(ge=0.0, le=1.0)
    use_speaker_boost: bool = True


# ---------------------------------------------------------------------------
# Pydantic models — Actions / Skills / Projects
# ---------------------------------------------------------------------------

class ActionUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    additional_trigger_words: Optional[str] = "[]"
    is_instruction: Optional[bool] = None
    instruction_text: Optional[str] = None


class ActionCreate(BaseModel):
    title: str = "New Action"
    description: Optional[str] = ""
    additional_trigger_words: Optional[str] = "[]"
    is_instruction: bool = False
    instruction_text: Optional[str] = None


class SnippetUpdate(BaseModel):
    title: Optional[str] = None
    text: Optional[str] = None
    description: Optional[str] = None
    additional_trigger_words: Optional[str] = None
    remote_hotkey: Optional[str] = None


class ProjectUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    folder_location: Optional[str] = None
    additional_trigger_words: Optional[str] = None
    startup_instructions: Optional[str] = None
    coding_backend: Optional[str] = None
    coding_backend_model: Optional[str] = None
    provider: Optional[str] = None
    board_id: Optional[str] = None
    board_name: Optional[str] = None


class ContextItemCreate(BaseModel):
    title: str
    content: str


class ContextItemUpdate(BaseModel):
    title: Optional[str] = None
    content: Optional[str] = None
