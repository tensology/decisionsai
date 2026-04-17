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
