"""
Centralized settings service — single place for load→mutate→save→emit patterns.

Route handlers should call these functions instead of inlining the same
load/save/signal boilerplate everywhere.
"""
import logging
from typing import Any, Callable, Dict, Optional, Tuple

from distr.core.settings import load_settings_from_db, save_settings_to_db
from distr.core.signals import signal_manager
from distr.core.hotkeys import (
    CHORD_MODIFIERS,
    PTT_MODIFIERS,
    VALID_HOTKEY_KEYS,
    DEFAULTS as HOTKEY_DEFAULTS,
)

logger = logging.getLogger(__name__)

SHORTCUT_SETTING_KEYS = (
    "global_ptt_hotkey_enabled",
    "global_ptt_hotkey_combo",
    "oracle_size_hotkey_decrease_modifier",
    "oracle_size_hotkey_decrease_key",
    "oracle_size_hotkey_increase_modifier",
    "oracle_size_hotkey_increase_key",
    "recording_hotkey_enabled",
    "recording_hotkey_modifier",
    "recording_hotkey_key",
    "skin_nav_hotkey_previous_modifier",
    "skin_nav_hotkey_previous_key",
    "skin_nav_hotkey_next_modifier",
    "skin_nav_hotkey_next_key",
    "skin_select_hotkey_modifier",
    "web_hotkey_chat_modifier",
    "web_hotkey_chat_key",
    "web_hotkey_projects_modifier",
    "web_hotkey_projects_key",
    "web_hotkey_actions_modifier",
    "web_hotkey_actions_key",
    "web_hotkey_snippets_modifier",
    "web_hotkey_snippets_key",
    "web_hotkey_workflows_modifier",
    "web_hotkey_workflows_key",
    "web_hotkey_preferences_modifier",
    "web_hotkey_preferences_key",
    "dictation_hotkey_enabled",
    "dictation_hotkey_modifier",
    "dictation_hotkey_key",
    "ticket_dictation_hotkey_enabled",
    "ticket_dictation_hotkey_modifier",
    "ticket_dictation_hotkey_key",
    "dictation_ticket_use_llm",
    "dictation_ticket_model",
    "dictation_ticket_timeout",
    "dictation_ticket_prompt",
)


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


_qt_main_invoker: Any = None
_qt_call_event_tid: Optional[int] = None


def _run_on_qt_main_thread(fn: Callable[[], None], *, label: str) -> None:
    """Run *fn* on the Qt GUI thread.

    The web server runs on a worker thread; ``QTimer.singleShot(0, fn)`` would still run *fn*
    on that worker. We ``postEvent`` to a ``QObject`` affined to ``app.thread()`` instead.
    """
    global _qt_main_invoker, _qt_call_event_tid
    try:
        from PyQt6.QtCore import QEvent, QObject
        from PyQt6.QtWidgets import QApplication

        app = QApplication.instance()
        if app is None:
            fn()
            return

        if _qt_main_invoker is None:
            _qt_call_event_tid = QEvent.registerEventType()
            tid = _qt_call_event_tid

            class _CallOnMainEvent(QEvent):
                def __init__(self, callback: Callable[[], None]):
                    super().__init__(QEvent.Type(tid))
                    self.callback = callback

            class _MainThreadInvoker(QObject):
                def event(self, a0: QEvent) -> bool:
                    if _qt_call_event_tid is not None and a0.type() == _qt_call_event_tid:
                        cb = getattr(a0, "callback", None)
                        if callable(cb):
                            try:
                                cb()
                            except Exception:
                                logger.exception("Qt main-thread callback failed (%s)", label)
                        return True
                    return super().event(a0)

            _qt_main_invoker = _MainThreadInvoker()
            _qt_main_invoker.moveToThread(app.thread())
            # Stash event class for posting (same tid as invoker expects)
            _qt_main_invoker._CallOnMainEvent = _CallOnMainEvent  # type: ignore[attr-defined]

        ev_cls = getattr(_qt_main_invoker, "_CallOnMainEvent", None)
        if ev_cls is None:
            fn()
            return
        QApplication.postEvent(_qt_main_invoker, ev_cls(fn))
        logger.info("Posted to Qt main thread: %s", label)
        return
    except Exception as e:
        logger.debug("Qt main-thread post unavailable (%s); running inline", e)
    fn()


def notify_stt_model_saved_for_running_agent(full_transcription_model: str) -> None:
    """After the web LLMs API persists ``transcription_model``, notify the Qt app.

    ``Application.update_agent_stt_model`` updates in-memory settings and sends
    ``update_stt_model`` to the live agent (same path as the desktop signal).
    """
    full = (full_transcription_model or "").strip()
    if not full:
        return

    def _do():
        _safe_emit(
            signal_manager.stt_model_changed,
            full,
            label="stt_model_changed (llms web save)",
        )

    _run_on_qt_main_thread(_do, label="stt_model_changed")


def notify_voice_hot_reload_for_running_agent(voice_provider: str, voice_model: str) -> None:
    """After voice selection changes, hot-swap the live agent TTS without a full restart."""
    vp = (voice_provider or "kokoro").strip()
    vm = (voice_model or "").strip()
    if not vp or not vm:
        return

    def _do():
        _safe_emit(
            signal_manager.voice_hot_reload,
            vp,
            vm,
            label="voice_hot_reload (voice save)",
        )

    _run_on_qt_main_thread(_do, label="voice_hot_reload")


def notify_conversational_llm_saved_for_running_agent(
    provider: str,
    model_name: str,
    chat_id: Optional[Any] = None,
) -> None:
    """After web LLMs save changes conversational provider/model, hot-swap the live agent LLM.

    Mirrors ``signal_manager.model_hot_reload`` → ``hot_swap_llm`` on the app (see ``signals.py``).
    """
    p = (provider or "ollama").strip()
    m = (model_name or "").strip()
    ci: Optional[int] = None
    if chat_id is not None and chat_id != "":
        try:
            ci = int(chat_id)
        except (TypeError, ValueError):
            ci = None

    def _do():
        _safe_emit(
            signal_manager.model_hot_reload,
            p,
            m,
            ci,
            label="model_hot_reload (llms web save)",
        )

    _run_on_qt_main_thread(_do, label="model_hot_reload")


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
    settings = load_settings_from_db()
    previous_settings = dict(settings)

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
        "supertonic_voice",
        "chatterbox_voice",
    )
    should_reload_agent = any(
        previous_settings.get(field) != settings.get(field)
        for field in reload_sensitive_fields
    )
    if should_reload_agent:
        from distr.core.chat import resolve_voice_model_from_global_settings
        from distr.core.agent.constants import normalize_voice_provider

        vp = normalize_voice_provider(settings.get("voice_provider") or "kokoro")
        vm = resolve_voice_model_from_global_settings(vp, settings) or ""
        notify_voice_hot_reload_for_running_agent(vp, vm)
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


def save_shortcut_settings(data) -> Dict[str, Any]:
    """Persist shortcut-related settings with validation."""
    valid_ptt_modifiers = PTT_MODIFIERS
    valid_chord_modifiers = CHORD_MODIFIERS
    valid_keys = VALID_HOTKEY_KEYS
    modifier_order = ("control", "option", "shift", "command")

    def _tokens(value: str) -> set[str]:
        return {p for p in str(value or "").strip().lower().split("_") if p in valid_ptt_modifiers}

    def _norm_modifier(value: str, default: str = HOTKEY_DEFAULTS["recording_hotkey_modifier"]) -> str:
        parts = _tokens(value)
        if not parts:
            parts = _tokens(default)
        v = "_".join(mod for mod in modifier_order if mod in parts)
        return v if v in valid_chord_modifiers else default

    def _norm_key(value: str, default: str = "") -> str:
        v = str(value or "").strip().lower()
        if not v:
            return ""
        return v if v in valid_keys else default

    def _shortcut_label(modifier: str, key: str = "") -> str:
        labels = {
            "control": "Control",
            "option": "Option",
            "shift": "Shift",
            "command": "Command",
        }
        parts = [labels[p] for p in modifier_order if p in _tokens(modifier)]
        if key:
            parts.append(str(key).replace("_", " ").title())
        return " + ".join(parts)

    def _validate_shortcut_collisions(shortcuts: list[dict[str, Any]], previous_settings: dict[str, Any]) -> None:
        seen: dict[tuple[tuple[str, ...], str], str] = {}
        seen_changed: dict[tuple[tuple[str, ...], str], bool] = {}
        for shortcut in shortcuts:
            name = shortcut["name"]
            enabled = bool(shortcut["enabled"])
            modifier = shortcut["modifier"]
            key = shortcut["key"]
            if not enabled:
                continue
            mods = tuple(mod for mod in modifier_order if mod in _tokens(modifier))
            if not mods:
                raise ValueError(f"{name} shortcut requires at least one modifier key.")
            signature = (mods, str(key or "").strip().lower())
            enabled_field = shortcut.get("enabled_field")
            modifier_field = shortcut.get("modifier_field")
            key_field = shortcut.get("key_field")
            previous_enabled = bool(previous_settings.get(enabled_field, enabled)) if enabled_field else True
            previous_modifier = _norm_modifier(previous_settings.get(modifier_field, modifier), modifier) if modifier_field else modifier
            previous_key = _norm_key(previous_settings.get(key_field, key)) if key_field else str(key or "")
            previous_signature = (
                tuple(mod for mod in modifier_order if mod in _tokens(previous_modifier)),
                str(previous_key or "").strip().lower(),
            )
            changed = (previous_enabled != enabled) or (previous_signature != signature)
            if signature in seen:
                other_name = seen[signature]
                if changed or seen_changed.get(signature, False):
                    raise ValueError(
                        f"{name} shortcut overlaps {other_name} ({_shortcut_label(modifier, key)}). "
                        "Choose a different shortcut combo."
                    )
            seen[signature] = name
            seen_changed[signature] = changed

    # PTT: unified combo field (e.g. "option_command", "control_command_option")
    # Parse each token and keep only valid modifier names.
    _raw_combo = str(getattr(data, "global_ptt_hotkey_combo", HOTKEY_DEFAULTS["global_ptt_hotkey_combo"]) or "").strip().lower()
    _ptt_parts = [p for p in _raw_combo.split("_") if p in valid_ptt_modifiers]
    ptt_enabled = bool(getattr(data, "global_ptt_hotkey_enabled", True))
    if not _ptt_parts:
        if ptt_enabled:
            raise ValueError("Push-to-Talk requires at least one modifier key.")
        _ptt_parts = [p for p in HOTKEY_DEFAULTS["global_ptt_hotkey_combo"].split("_") if p in valid_ptt_modifiers]
    ptt_combo = _norm_modifier(_raw_combo, HOTKEY_DEFAULTS["global_ptt_hotkey_combo"])

    down_modifier = _norm_modifier(getattr(data, "oracle_size_hotkey_decrease_modifier", HOTKEY_DEFAULTS["oracle_size_hotkey_decrease_modifier"]), HOTKEY_DEFAULTS["oracle_size_hotkey_decrease_modifier"])
    up_modifier = _norm_modifier(getattr(data, "oracle_size_hotkey_increase_modifier", HOTKEY_DEFAULTS["oracle_size_hotkey_increase_modifier"]), HOTKEY_DEFAULTS["oracle_size_hotkey_increase_modifier"])
    if down_modifier not in valid_chord_modifiers:
        down_modifier = HOTKEY_DEFAULTS["oracle_size_hotkey_decrease_modifier"]
    if up_modifier not in valid_chord_modifiers:
        up_modifier = HOTKEY_DEFAULTS["oracle_size_hotkey_increase_modifier"]
    down_key = _norm_key(getattr(data, "oracle_size_hotkey_decrease_key", HOTKEY_DEFAULTS["oracle_size_hotkey_decrease_key"]))
    up_key = _norm_key(getattr(data, "oracle_size_hotkey_increase_key", HOTKEY_DEFAULTS["oracle_size_hotkey_increase_key"]))
    record_modifier = _norm_modifier(getattr(data, "recording_hotkey_modifier", HOTKEY_DEFAULTS["recording_hotkey_modifier"]), HOTKEY_DEFAULTS["recording_hotkey_modifier"])
    record_key = _norm_key(getattr(data, "recording_hotkey_key", HOTKEY_DEFAULTS["recording_hotkey_key"]))
    if record_modifier not in valid_chord_modifiers:
        record_modifier = HOTKEY_DEFAULTS["recording_hotkey_modifier"]
    skin_prev_modifier = _norm_modifier(getattr(data, "skin_nav_hotkey_previous_modifier", HOTKEY_DEFAULTS["skin_nav_hotkey_previous_modifier"]), HOTKEY_DEFAULTS["skin_nav_hotkey_previous_modifier"])
    skin_prev_key = _norm_key(getattr(data, "skin_nav_hotkey_previous_key", HOTKEY_DEFAULTS["skin_nav_hotkey_previous_key"]))
    skin_next_modifier = _norm_modifier(getattr(data, "skin_nav_hotkey_next_modifier", HOTKEY_DEFAULTS["skin_nav_hotkey_next_modifier"]), HOTKEY_DEFAULTS["skin_nav_hotkey_next_modifier"])
    skin_next_key = _norm_key(getattr(data, "skin_nav_hotkey_next_key", HOTKEY_DEFAULTS["skin_nav_hotkey_next_key"]))
    skin_select_modifier = _norm_modifier(getattr(data, "skin_select_hotkey_modifier", HOTKEY_DEFAULTS["skin_select_hotkey_modifier"]), HOTKEY_DEFAULTS["skin_select_hotkey_modifier"])
    web_chat_modifier = _norm_modifier(getattr(data, "web_hotkey_chat_modifier", HOTKEY_DEFAULTS["web_hotkey_chat_modifier"]), HOTKEY_DEFAULTS["web_hotkey_chat_modifier"])
    web_chat_key = _norm_key(getattr(data, "web_hotkey_chat_key", HOTKEY_DEFAULTS["web_hotkey_chat_key"]))
    web_projects_modifier = _norm_modifier(getattr(data, "web_hotkey_projects_modifier", HOTKEY_DEFAULTS["web_hotkey_projects_modifier"]), HOTKEY_DEFAULTS["web_hotkey_projects_modifier"])
    web_projects_key = _norm_key(getattr(data, "web_hotkey_projects_key", HOTKEY_DEFAULTS["web_hotkey_projects_key"]))
    web_actions_modifier = _norm_modifier(getattr(data, "web_hotkey_actions_modifier", HOTKEY_DEFAULTS["web_hotkey_actions_modifier"]), HOTKEY_DEFAULTS["web_hotkey_actions_modifier"])
    web_actions_key = _norm_key(getattr(data, "web_hotkey_actions_key", HOTKEY_DEFAULTS["web_hotkey_actions_key"]))
    web_snippets_modifier = _norm_modifier(getattr(data, "web_hotkey_snippets_modifier", HOTKEY_DEFAULTS["web_hotkey_snippets_modifier"]), HOTKEY_DEFAULTS["web_hotkey_snippets_modifier"])
    web_snippets_key = _norm_key(getattr(data, "web_hotkey_snippets_key", HOTKEY_DEFAULTS["web_hotkey_snippets_key"]))
    web_workflows_modifier = _norm_modifier(getattr(data, "web_hotkey_workflows_modifier", HOTKEY_DEFAULTS["web_hotkey_workflows_modifier"]), HOTKEY_DEFAULTS["web_hotkey_workflows_modifier"])
    web_workflows_key = _norm_key(getattr(data, "web_hotkey_workflows_key", HOTKEY_DEFAULTS["web_hotkey_workflows_key"]))
    web_preferences_modifier = _norm_modifier(getattr(data, "web_hotkey_preferences_modifier", HOTKEY_DEFAULTS["web_hotkey_preferences_modifier"]), HOTKEY_DEFAULTS["web_hotkey_preferences_modifier"])
    web_preferences_key = _norm_key(getattr(data, "web_hotkey_preferences_key", HOTKEY_DEFAULTS["web_hotkey_preferences_key"]))

    settings = load_settings_from_db()
    previous_settings = dict(settings)
    settings["global_ptt_hotkey_enabled"] = bool(getattr(data, "global_ptt_hotkey_enabled", True))
    settings["global_ptt_hotkey_combo"] = ptt_combo
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
    raw_dictation_modifier = str(getattr(data, "dictation_hotkey_modifier", HOTKEY_DEFAULTS["dictation_hotkey_modifier"]) or "").strip().lower()
    raw_dictation_key = str(getattr(data, "dictation_hotkey_key", "") or "").strip().lower()
    dictation_modifier = _norm_modifier(raw_dictation_modifier, HOTKEY_DEFAULTS["dictation_hotkey_modifier"])
    dictation_key = _norm_key(raw_dictation_key)
    if dictation_modifier not in valid_chord_modifiers:
        dictation_modifier = HOTKEY_DEFAULTS["dictation_hotkey_modifier"]
    dictation_enabled = bool(getattr(data, "dictation_hotkey_enabled", HOTKEY_DEFAULTS["dictation_hotkey_enabled"]))
    raw_ticket_dictation_modifier = str(getattr(data, "ticket_dictation_hotkey_modifier", HOTKEY_DEFAULTS["ticket_dictation_hotkey_modifier"]) or "").strip().lower()
    raw_ticket_dictation_key = str(getattr(data, "ticket_dictation_hotkey_key", "") or "").strip().lower()
    ticket_dictation_modifier = _norm_modifier(raw_ticket_dictation_modifier, HOTKEY_DEFAULTS["ticket_dictation_hotkey_modifier"])
    ticket_dictation_key = _norm_key(raw_ticket_dictation_key)
    if ticket_dictation_modifier not in valid_chord_modifiers:
        ticket_dictation_modifier = HOTKEY_DEFAULTS["ticket_dictation_hotkey_modifier"]
    ticket_dictation_enabled = bool(getattr(data, "ticket_dictation_hotkey_enabled", HOTKEY_DEFAULTS["ticket_dictation_hotkey_enabled"]))
    _validate_shortcut_collisions([
        {"name": "Push-to-Talk", "enabled": ptt_enabled, "modifier": ptt_combo, "key": "", "enabled_field": "global_ptt_hotkey_enabled", "modifier_field": "global_ptt_hotkey_combo"},
        {"name": "Dictation", "enabled": dictation_enabled, "modifier": dictation_modifier, "key": dictation_key, "enabled_field": "dictation_hotkey_enabled", "modifier_field": "dictation_hotkey_modifier", "key_field": "dictation_hotkey_key"},
        {"name": "Ticket dictation", "enabled": ticket_dictation_enabled, "modifier": ticket_dictation_modifier, "key": ticket_dictation_key, "enabled_field": "ticket_dictation_hotkey_enabled", "modifier_field": "ticket_dictation_hotkey_modifier", "key_field": "ticket_dictation_hotkey_key"},
        {"name": "Recording", "enabled": bool(getattr(data, "recording_hotkey_enabled", True)), "modifier": record_modifier, "key": record_key, "enabled_field": "recording_hotkey_enabled", "modifier_field": "recording_hotkey_modifier", "key_field": "recording_hotkey_key"},
        {"name": "Oracle size decrease", "enabled": True, "modifier": down_modifier, "key": down_key, "modifier_field": "oracle_size_hotkey_decrease_modifier", "key_field": "oracle_size_hotkey_decrease_key"},
        {"name": "Oracle size increase", "enabled": True, "modifier": up_modifier, "key": up_key, "modifier_field": "oracle_size_hotkey_increase_modifier", "key_field": "oracle_size_hotkey_increase_key"},
        {"name": "Previous skin", "enabled": True, "modifier": skin_prev_modifier, "key": skin_prev_key, "modifier_field": "skin_nav_hotkey_previous_modifier", "key_field": "skin_nav_hotkey_previous_key"},
        {"name": "Next skin", "enabled": True, "modifier": skin_next_modifier, "key": skin_next_key, "modifier_field": "skin_nav_hotkey_next_modifier", "key_field": "skin_nav_hotkey_next_key"},
        {"name": "Skin number modifier", "enabled": True, "modifier": skin_select_modifier, "key": "", "modifier_field": "skin_select_hotkey_modifier"},
        {"name": "Chat launcher", "enabled": True, "modifier": web_chat_modifier, "key": web_chat_key, "modifier_field": "web_hotkey_chat_modifier", "key_field": "web_hotkey_chat_key"},
        {"name": "Projects launcher", "enabled": True, "modifier": web_projects_modifier, "key": web_projects_key, "modifier_field": "web_hotkey_projects_modifier", "key_field": "web_hotkey_projects_key"},
        {"name": "Actions launcher", "enabled": True, "modifier": web_actions_modifier, "key": web_actions_key, "modifier_field": "web_hotkey_actions_modifier", "key_field": "web_hotkey_actions_key"},
        {"name": "Snippets launcher", "enabled": True, "modifier": web_snippets_modifier, "key": web_snippets_key, "modifier_field": "web_hotkey_snippets_modifier", "key_field": "web_hotkey_snippets_key"},
        {"name": "Workflows launcher", "enabled": True, "modifier": web_workflows_modifier, "key": web_workflows_key, "modifier_field": "web_hotkey_workflows_modifier", "key_field": "web_hotkey_workflows_key"},
        {"name": "Preferences launcher", "enabled": True, "modifier": web_preferences_modifier, "key": web_preferences_key, "modifier_field": "web_hotkey_preferences_modifier", "key_field": "web_hotkey_preferences_key"},
    ], previous_settings)
    settings["dictation_hotkey_enabled"] = dictation_enabled
    settings["dictation_hotkey_modifier"] = dictation_modifier
    settings["dictation_hotkey_key"] = dictation_key
    settings["ticket_dictation_hotkey_enabled"] = ticket_dictation_enabled
    settings["ticket_dictation_hotkey_modifier"] = ticket_dictation_modifier
    settings["ticket_dictation_hotkey_key"] = ticket_dictation_key
    settings["dictation_ticket_use_llm"] = bool(getattr(data, "dictation_ticket_use_llm", True))
    settings["dictation_ticket_model"] = str(getattr(data, "dictation_ticket_model", "qwen2.5:0.5b") or "qwen2.5:0.5b").strip()
    settings["dictation_ticket_timeout"] = str(getattr(data, "dictation_ticket_timeout", "1.2") or "1.2").strip()
    settings["dictation_ticket_prompt"] = str(getattr(data, "dictation_ticket_prompt", "") or "").strip()
    save_settings_to_db(settings)

    def _do():
        _safe_emit(
            signal_manager.shortcut_settings_changed,
            label="shortcut_settings_changed",
        )

    _run_on_qt_main_thread(_do, label="shortcut_settings_changed")
    return {key: settings.get(key) for key in SHORTCUT_SETTING_KEYS}


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

    from distr.core.chat import resolve_voice_model_from_global_settings
    from distr.core.agent.constants import normalize_voice_provider as _norm_vp

    pid = _norm_vp(vp_raw)
    notify_voice_hot_reload_for_running_agent(
        pid,
        resolve_voice_model_from_global_settings(pid, settings) or vm_raw,
    )

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
    """Persist and emit oracle size. Returns pixel size.

    The slider sends a scale value (4–10) which we convert to a pixel value
    (80–200) before persisting.  Storing the pixel value ensures the oracle
    window always reads a usable size even if the Qt signal (which also
    carries the pixel value) is delayed or dropped.
    """
    actual_size = sphere_size * 20
    update_setting("sphere_size", actual_size,
                   signal=signal_manager.oracle_size_changed,
                   signal_args=(actual_size,),
                   signal_label="oracle_size_changed")
    return actual_size


# ---------------------------------------------------------------------------
# Third-party keys
# ---------------------------------------------------------------------------

def thirdparty_llm_provider_ready(
    settings: dict,
    enabled_key: str,
    key_key: str,
) -> bool:
    """True when a cloud LLM provider is enabled and has a non-empty API key."""
    enabled = settings.get(enabled_key)
    if enabled in (False, 0, None, ""):
        return False
    return bool((settings.get(key_key) or "").strip())


def save_thirdparty_settings(data, resolve_secret_fn) -> None:
    """Persist third-party API keys (with masking) and reload agent."""
    settings = load_settings_from_db()

    settings["ollama_url"] = data.ollama_url
    for field_pair in [
        ("assemblyai_enabled", "assemblyai_key"),
        ("openai_enabled", "openai_key"),
        ("anthropic_enabled", "anthropic_key"),
        ("cursor_enabled", "cursor_key"),
        ("elevenlabs_enabled", "elevenlabs_key"),
        ("openrouter_enabled", "openrouter_key"),
        ("groq_enabled", "groq_key"),
        ("kilo_enabled", "kilo_key"),
        ("gemini_enabled", "gemini_key"),
        ("nvidia_enabled", "nvidia_key"),
        ("masko_enabled", "masko_key"),
    ]:
        enabled_field, key_field = field_pair
        settings[enabled_field] = getattr(data, enabled_field)
        incoming_key = getattr(data, key_field)
        if not settings[enabled_field] and not (incoming_key or "").strip():
            settings[key_field] = ""
        else:
            settings[key_field] = resolve_secret_fn(
                settings.get(key_field, ""), incoming_key
            )

    # Composio (stored as rube_* for backward compatibility)
    settings["rube_enabled"] = data.composio_enabled
    if not data.composio_enabled and not (data.composio_key or "").strip():
        settings["rube_token"] = ""
    else:
        settings["rube_token"] = resolve_secret_fn(
            settings.get("rube_token", ""), data.composio_key
        )

    save_settings_to_db(settings)
    _safe_emit(signal_manager.reload_agent, label="reload_agent (third-party save)")
    try:
        from distr.core.mcp_harness import recalibrate_mcp_harness_quiet

        recalibrate_mcp_harness_quiet()
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Audio settings
# ---------------------------------------------------------------------------

def save_audio_settings(data) -> None:
    """Persist audio device selections and emit audio_devices_changed."""
    settings = load_settings_from_db()
    settings["input_device"] = data.input_device
    settings["output_device"] = data.output_device
    settings["lock_sound"] = data.remember_audio_settings
    if data.remember_audio_settings:
        if data.locked_output:
            settings["locked_output"] = data.locked_output
        if data.locked_input:
            settings["locked_input"] = data.locked_input
    save_settings_to_db(settings)

    _safe_emit(signal_manager.audio_devices_changed,
               data.input_device, data.output_device,
               label="audio_devices_changed")
