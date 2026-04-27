"""
Centralized settings service — single place for load→mutate→save→emit patterns.

Route handlers should call these functions instead of inlining the same
load/save/signal boilerplate everywhere.
"""
import logging
from typing import Any, Dict, Optional, Tuple

from distr.core.settings import load_settings_from_db, save_settings_to_db
from distr.core.signals import signal_manager

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Generic helpers
# ---------------------------------------------------------------------------

def _safe_emit(signal, *args, label: str = "signal"):
    """Emit a signal, swallowing errors so callers don't crash."""
    try:
        signal.emit(*args)
        logger.info("Emitted %s", label)
    except Exception as e:
        logger.warning("Failed to emit %s: %s", label, e)


def update_setting(key: str, value: Any, *, signal=None, signal_args: tuple = (), signal_label: str = "") -> dict:
    """Load settings, set *key* to *value*, save, optionally emit *signal*."""
    settings = load_settings_from_db()
    previous_settings = dict(settings)
    settings[key] = value
    save_settings_to_db(settings)
    if signal is not None:
        _safe_emit(signal, *signal_args, label=signal_label or key)
    return settings


# ---------------------------------------------------------------------------
# General settings (big save from the General tab)
# ---------------------------------------------------------------------------

def save_general_settings(data) -> None:
    """Persist all general-tab fields and emit every relevant signal."""
    valid_modifiers = {"option", "option_command", "command", "control", "shift"}
    primary = str(getattr(data, "global_ptt_hotkey_primary", "option")).strip().lower()
    secondary = str(getattr(data, "global_ptt_hotkey_secondary", "command")).strip().lower()
    if primary not in valid_modifiers:
        primary = "option"
    if secondary not in valid_modifiers:
        secondary = "command"
    if primary == secondary:
        secondary = "command" if primary != "command" else "option"
    data.global_ptt_hotkey_primary = primary
    data.global_ptt_hotkey_secondary = secondary

    settings = load_settings_from_db()

    # Bulk-copy every field from the Pydantic model
    for field in data.__fields__:
        settings[field] = getattr(data, field)
    save_settings_to_db(settings)

    # --- Emit signals ---
    _safe_emit(signal_manager.playback_speed_changed, data.playback_speed,
               label="playback_speed_changed")
    _safe_emit(signal_manager.speech_volume_changed, data.speech_volume,
               label="speech_volume_changed")
    _safe_emit(signal_manager.vad_threshold_changed, data.vad_threshold,
               label="vad_threshold_changed")

    if data.voice_provider == "elevenlabs":
        try:
            from distr.core.audio.tts_handler import clear_elevenlabs_voice_cache
            clear_elevenlabs_voice_cache()
        except Exception as e:
            logger.warning("Failed to clear ElevenLabs voice cache: %s", e)
        _safe_emit(signal_manager.elevenlabs_voice_settings_changed,
                    data.elevenlabs_stability, data.elevenlabs_similarity_boost,
                    data.elevenlabs_style, data.elevenlabs_use_speaker_boost,
                    label="elevenlabs_voice_settings_changed")

    # Reload agent only when core voice provider/voice selections changed.
    # Avoid reloading for UI-only settings (e.g. global PTT hotkey toggle),
    # which can destabilize macOS during active desktop listeners.
    reload_sensitive_fields = (
        "voice_provider",
        "kokoro_voice",
        "elevenlabs_voice",
        "openai_voice",
        "coqui_voice",
        "qwen3_voice",
        "f5tts_voice",
        "voxcpm_voice",
    )
    should_reload_agent = any(
        previous_settings.get(field) != settings.get(field)
        for field in reload_sensitive_fields
    )
    if should_reload_agent:
        _safe_emit(signal_manager.reload_agent, label="reload_agent (general save)")
    # Oracle skin/size signals removed — those now go through Skins routes
    # (Requirements: 8.8)
    _safe_emit(signal_manager.oracle_position_changed, data.oracle_position,
               label="oracle_position_changed")

    # --- Autostart (load on startup) ---
    try:
        from distr.core.autostart import set_autostart
        set_autostart(data.load_on_startup)
    except Exception as e:
        logger.warning("Failed to update autostart setting: %s", e)


def save_shortcut_settings(data) -> None:
    """Persist shortcut-related settings with validation."""
    valid_ptt_modifiers = {"option", "command", "control", "shift"}
    valid_chord_modifiers = {"option", "command", "control", "shift", "option_command"}
    valid_keys = {
        "left_bracket", "right_bracket", "minus", "equal",
        "left_arrow", "right_arrow",
        "a", "c", "j", "n", "s", "w",
        "grave",
    }

    def _norm_modifier(value: str, default: str = "option") -> str:
        v = str(value or default).strip().lower()
        return v if v in valid_chord_modifiers else default

    def _norm_key(value: str, default: str = "left_bracket") -> str:
        v = str(value or default).strip().lower()
        return v if v in valid_keys else default

    primary = _norm_modifier(getattr(data, "global_ptt_hotkey_primary", "option"), "option")
    secondary = _norm_modifier(getattr(data, "global_ptt_hotkey_secondary", "command"), "command")
    if primary not in valid_ptt_modifiers:
        primary = "option"
    if secondary not in valid_ptt_modifiers:
        secondary = "command"
    if primary == secondary:
        secondary = "command" if primary != "command" else "option"

    down_modifier = _norm_modifier(getattr(data, "oracle_size_hotkey_decrease_modifier", "option_command"), "option_command")
    up_modifier = _norm_modifier(getattr(data, "oracle_size_hotkey_increase_modifier", "option_command"), "option_command")
    if down_modifier not in valid_chord_modifiers:
        down_modifier = "option_command"
    if up_modifier not in valid_chord_modifiers:
        up_modifier = "option_command"
    down_key = _norm_key(getattr(data, "oracle_size_hotkey_decrease_key", "left_bracket"), "left_bracket")
    up_key = _norm_key(getattr(data, "oracle_size_hotkey_increase_key", "right_bracket"), "right_bracket")
    record_modifier = _norm_modifier(getattr(data, "recording_hotkey_modifier", "option_command"), "option_command")
    record_key = _norm_key(getattr(data, "recording_hotkey_key", "s"), "s")
    if record_modifier not in valid_chord_modifiers:
        record_modifier = "option_command"
    skin_prev_modifier = _norm_modifier(getattr(data, "skin_nav_hotkey_previous_modifier", "option_command"), "option_command")
    skin_prev_key = _norm_key(getattr(data, "skin_nav_hotkey_previous_key", "left_arrow"), "left_arrow")
    skin_next_modifier = _norm_modifier(getattr(data, "skin_nav_hotkey_next_modifier", "option_command"), "option_command")
    skin_next_key = _norm_key(getattr(data, "skin_nav_hotkey_next_key", "right_arrow"), "right_arrow")
    skin_select_modifier = _norm_modifier(getattr(data, "skin_select_hotkey_modifier", "option_command"), "option_command")
    web_chat_modifier = _norm_modifier(getattr(data, "web_hotkey_chat_modifier", "option_command"), "option_command")
    web_chat_key = _norm_key(getattr(data, "web_hotkey_chat_key", "c"), "c")
    web_projects_modifier = _norm_modifier(getattr(data, "web_hotkey_projects_modifier", "option_command"), "option_command")
    web_projects_key = _norm_key(getattr(data, "web_hotkey_projects_key", "j"), "j")
    web_actions_modifier = _norm_modifier(getattr(data, "web_hotkey_actions_modifier", "option_command"), "option_command")
    web_actions_key = _norm_key(getattr(data, "web_hotkey_actions_key", "a"), "a")
    web_snippets_modifier = _norm_modifier(getattr(data, "web_hotkey_snippets_modifier", "option_command"), "option_command")
    web_snippets_key = _norm_key(getattr(data, "web_hotkey_snippets_key", "n"), "n")
    web_workflows_modifier = _norm_modifier(getattr(data, "web_hotkey_workflows_modifier", "option_command"), "option_command")
    web_workflows_key = _norm_key(getattr(data, "web_hotkey_workflows_key", "w"), "w")
    web_preferences_modifier = _norm_modifier(getattr(data, "web_hotkey_preferences_modifier", "option_command"), "option_command")
    web_preferences_key = _norm_key(getattr(data, "web_hotkey_preferences_key", "grave"), "grave")

    if down_modifier == up_modifier and down_key == up_key:
        up_key = "right_bracket" if down_key != "right_bracket" else "left_bracket"

    settings = load_settings_from_db()
    settings["global_ptt_hotkey_enabled"] = bool(getattr(data, "global_ptt_hotkey_enabled", True))
    settings["global_ptt_hotkey_primary"] = primary
    settings["global_ptt_hotkey_secondary"] = secondary
    settings["oracle_size_hotkey_decrease_modifier"] = down_modifier
    settings["oracle_size_hotkey_decrease_key"] = down_key
    settings["oracle_size_hotkey_increase_modifier"] = up_modifier
    settings["oracle_size_hotkey_increase_key"] = up_key
    settings["recording_hotkey_enabled"] = bool(getattr(data, "recording_hotkey_enabled", True))
    settings["recording_hotkey_modifier"] = record_modifier
    settings["recording_hotkey_key"] = record_key
    settings["skin_nav_hotkey_previous_modifier"] = skin_prev_modifier
    settings["skin_nav_hotkey_previous_key"] = skin_prev_key
    settings["skin_nav_hotkey_next_modifier"] = skin_next_modifier
    settings["skin_nav_hotkey_next_key"] = skin_next_key
    settings["skin_select_hotkey_modifier"] = skin_select_modifier
    settings["web_hotkey_chat_modifier"] = web_chat_modifier
    settings["web_hotkey_chat_key"] = web_chat_key
    settings["web_hotkey_projects_modifier"] = web_projects_modifier
    settings["web_hotkey_projects_key"] = web_projects_key
    settings["web_hotkey_actions_modifier"] = web_actions_modifier
    settings["web_hotkey_actions_key"] = web_actions_key
    settings["web_hotkey_snippets_modifier"] = web_snippets_modifier
    settings["web_hotkey_snippets_key"] = web_snippets_key
    settings["web_hotkey_workflows_modifier"] = web_workflows_modifier
    settings["web_hotkey_workflows_key"] = web_workflows_key
    settings["web_hotkey_preferences_modifier"] = web_preferences_modifier
    settings["web_hotkey_preferences_key"] = web_preferences_key
    save_settings_to_db(settings)


def apply_voice_selection_to_settings(
    settings: Dict[str, Any],
    voice_provider_id: str,
    voice_model: str,
) -> bool:
    """Mutate *settings* in place to match General-tab voice semantics.

    Sets canonical ``voice_provider`` (lowercase id), human-readable ``tts_provider``
    (descriptor ``name``, same legacy meaning as "Kokoro (Offline)" in DB), legacy
    ``tts_voice`` (active voice id string), and each registered provider's
    ``settings_key`` column when present on ORM.

    Returns True if fields were applied, False if inputs are empty or provider unknown.
    """
    from distr.core.agent.constants import normalize_voice_provider
    from distr.core.agent.services.tts.registry import tts_registry
    from distr.core.db import Settings

    vp_raw = (voice_provider_id or "").strip()
    vm_raw = (voice_model or "").strip()
    if not vp_raw or not vm_raw:
        return False

    pid = normalize_voice_provider(vp_raw)

    try:
        active_desc = tts_registry.get(pid)
    except KeyError:
        logger.warning("apply_voice_selection_to_settings: unknown TTS provider %r", pid)
        return False

    settings["voice_provider"] = pid
    settings["tts_provider"] = active_desc.name
    settings["tts_voice"] = vm_raw

    # Same pattern as settings/js/general.js saveGeneralSettings — one column per provider
    for d in tts_registry.all_providers():
        sk = d.settings_key
        val = vm_raw if d.id == pid else (d.default_voice or "")
        if hasattr(Settings, sk):
            settings[sk] = val

    return True


def save_voice_selection(voice_provider_id: str, voice_model: str) -> None:
    """Persist global TTS provider and voice model (same in-memory shape as General tab)."""
    vp_raw = (voice_provider_id or "").strip()
    vm_raw = (voice_model or "").strip()
    if not vp_raw or not vm_raw:
        logger.warning("save_voice_selection: missing provider or voice model")
        return

    settings = load_settings_from_db()
    if not apply_voice_selection_to_settings(settings, vp_raw, vm_raw):
        return

    save_settings_to_db(settings)

    from distr.core.agent.constants import normalize_voice_provider as _norm_vp

    pid = _norm_vp(vp_raw)
    if pid == "elevenlabs":
        try:
            from distr.core.audio.tts_handler import clear_elevenlabs_voice_cache
            clear_elevenlabs_voice_cache()
        except Exception as e:
            logger.warning("Failed to clear ElevenLabs voice cache: %s", e)

    # Do NOT emit reload_agent here. Chat UI calls this right after POST /chats while the
    # desktop agent is handling web_create_chat_emits_requested → current_chat_changed.
    # reload_agent_session() stops the agent process and races that flow, causing crashes /
    # dropped first replies. Voice is persisted to DB; agent reload is only needed for a full
    # configuration refresh — use Settings → General Save for that (save_general_settings).


# ---------------------------------------------------------------------------
# Individual voice / oracle updaters
# ---------------------------------------------------------------------------

def update_playback_speed(speed: float) -> float:
    """Clamp, persist, and emit playback speed."""
    speed = max(0.5, min(2.0, float(speed)))
    update_setting("playback_speed", speed,
                   signal=signal_manager.playback_speed_changed,
                   signal_args=(speed,),
                   signal_label="playback_speed_changed")
    return speed


def update_speech_volume(volume: int) -> int:
    """Clamp, persist, and emit speech volume."""
    volume = max(0, min(100, int(volume)))
    update_setting("speech_volume", volume,
                   signal=signal_manager.speech_volume_changed,
                   signal_args=(volume,),
                   signal_label="speech_volume_changed")
    return volume


def update_vad_threshold(threshold: int) -> int:
    """Clamp, persist, and emit VAD threshold."""
    threshold = max(0, min(100, int(threshold)))
    update_setting("vad_threshold", threshold,
                   signal=signal_manager.vad_threshold_changed,
                   signal_args=(threshold,),
                   signal_label="vad_threshold_changed")
    return threshold


def update_elevenlabs_settings(
    stability: float,
    similarity_boost: float,
    style: float,
    use_speaker_boost: bool,
) -> Tuple[float, float, float, bool]:
    """Clear cache, persist, and emit ElevenLabs voice settings."""
    from distr.core.audio.tts_handler import clear_elevenlabs_voice_cache
    clear_elevenlabs_voice_cache()

    stability = max(0.0, min(1.0, float(stability)))
    similarity_boost = max(0.0, min(1.0, float(similarity_boost)))
    style = max(0.0, min(1.0, float(style)))

    settings = load_settings_from_db()
    settings["elevenlabs_stability"] = stability
    settings["elevenlabs_similarity_boost"] = similarity_boost
    settings["elevenlabs_style"] = style
    settings["elevenlabs_use_speaker_boost"] = use_speaker_boost
    save_settings_to_db(settings)

    _safe_emit(signal_manager.elevenlabs_voice_settings_changed,
               stability, similarity_boost, style, use_speaker_boost,
               label="elevenlabs_voice_settings_changed")
    return stability, similarity_boost, style, use_speaker_boost


def update_oracle_skin(skin: str) -> None:
    """Run migration, persist, and emit oracle skin change.

    Requirements: 11.2, 11.8
    """
    from distr.core.skin_migration import migrate_selected_oracle

    migrated = migrate_selected_oracle(skin)
    update_setting("selected_oracle", migrated,
                   signal=signal_manager.direct_oracle_change,
                   signal_args=(migrated,),
                   signal_label="direct_oracle_change")


POSITION_DISPLAY = {
    "custom": "Custom",
    "top_left": "Top Left",
    "top_right": "Top Right",
    "middle_left": "Middle Left",
    "middle_right": "Middle Right",
    "bottom_left": "Bottom Left",
    "bottom_right": "Bottom Right",
}


def update_oracle_position(position: str) -> str:
    """Persist and emit oracle position. Returns the display name."""
    pos = (position or "custom").strip().lower()
    display = POSITION_DISPLAY.get(pos, "Custom")
    update_setting("oracle_position", pos,
                   signal=signal_manager.oracle_position_changed,
                   signal_args=(display,),
                   signal_label="oracle_position_changed")
    return pos


def update_oracle_size(sphere_size: int) -> int:
    """Persist and emit oracle size. Returns pixel size."""
    actual_size = sphere_size * 20
    update_setting("sphere_size", sphere_size,
                   signal=signal_manager.oracle_size_changed,
                   signal_args=(actual_size,),
                   signal_label="oracle_size_changed")
    return actual_size


# ---------------------------------------------------------------------------
# Third-party keys
# ---------------------------------------------------------------------------

def save_thirdparty_settings(data, resolve_secret_fn) -> None:
    """Persist third-party API keys (with masking) and reload agent."""
    settings = load_settings_from_db()

    settings["ollama_url"] = data.ollama_url
    for field_pair in [
        ("assemblyai_enabled", "assemblyai_key"),
        ("openai_enabled", "openai_key"),
        ("anthropic_enabled", "anthropic_key"),
        ("elevenlabs_enabled", "elevenlabs_key"),
        ("openrouter_enabled", "openrouter_key"),
        ("groq_enabled", "groq_key"),
        ("kilo_enabled", "kilo_key"),
        ("gemini_enabled", "gemini_key"),
        ("masko_enabled", "masko_key"),
    ]:
        enabled_field, key_field = field_pair
        settings[enabled_field] = getattr(data, enabled_field)
        settings[key_field] = resolve_secret_fn(
            settings.get(key_field, ""), getattr(data, key_field)
        )

    save_settings_to_db(settings)
    _safe_emit(signal_manager.reload_agent, label="reload_agent (third-party save)")


# ---------------------------------------------------------------------------
# Audio settings
# ---------------------------------------------------------------------------

def save_audio_settings(data) -> None:
    """Persist audio device selections and emit audio_devices_changed."""
    settings = load_settings_from_db()
    settings["input_device"] = data.input_device
    settings["output_device"] = data.output_device
    settings["lock_sound"] = data.remember_audio_settings
    if data.locked_output:
        settings["locked_output"] = data.locked_output
    if data.locked_input:
        settings["locked_input"] = data.locked_input
    save_settings_to_db(settings)

    _safe_emit(signal_manager.audio_devices_changed,
               data.input_device, data.output_device,
               label="audio_devices_changed")
