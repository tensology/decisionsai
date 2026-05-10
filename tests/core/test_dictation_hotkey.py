"""Tests for the dictation hotkey feature and related computer-use components."""

from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# 1. Test DEFAULTS in hotkeys.py
# ---------------------------------------------------------------------------

def test_dictation_hotkey_defaults_in_hotkeys_py():
    from distr.core.hotkeys import DEFAULTS
    assert "dictation_hotkey_enabled" in DEFAULTS
    assert DEFAULTS["dictation_hotkey_enabled"] is True
    assert "dictation_hotkey_modifier" in DEFAULTS
    assert DEFAULTS["dictation_hotkey_modifier"] == "control_command"
    assert "dictation_hotkey_key" in DEFAULTS
    assert DEFAULTS["dictation_hotkey_key"] == ""


# ---------------------------------------------------------------------------
# 2. Test DEFAULT_SETTINGS in settings.py
# ---------------------------------------------------------------------------

def test_dictation_hotkey_defaults_in_settings_py():
    # conftest.py stubs distr.core.settings with a MagicMock, so we load the
    # real module directly via importlib to inspect its source-level dict.
    import importlib.util
    import sys
    import os

    settings_path = os.path.join(
        os.path.dirname(__file__), "..", "..", "distr", "core", "settings.py"
    )
    spec = importlib.util.spec_from_file_location("_real_settings", settings_path)
    real_settings = importlib.util.module_from_spec(spec)
    # Provide enough of the environment for the module to load
    # It imports from distr.core.db and distr.core.hotkeys
    # hotkeys has no external deps, so it can be imported normally.
    # We patch db imports to avoid SQLite side effects.
    _saved = {}
    for name in ("distr.core.db", "distr.core.utils"):
        _saved[name] = sys.modules.get(name)
        sys.modules[name] = MagicMock()
    try:
        spec.loader.exec_module(real_settings)
        DEFAULT_SETTINGS = real_settings.DEFAULT_SETTINGS
        assert "dictation_hotkey_enabled" in DEFAULT_SETTINGS
        assert DEFAULT_SETTINGS["dictation_hotkey_enabled"] is True
        assert "dictation_hotkey_modifier" in DEFAULT_SETTINGS
        assert DEFAULT_SETTINGS["dictation_hotkey_modifier"] == "control_command"
        assert "dictation_hotkey_key" in DEFAULT_SETTINGS
        assert DEFAULT_SETTINGS["dictation_hotkey_key"] == ""
    finally:
        for name, mod in _saved.items():
            if mod is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = mod


# ---------------------------------------------------------------------------
# 3. Test GlobalPttHotkeyListener accepts dictation callbacks
# ---------------------------------------------------------------------------

def test_global_ptt_listener_accepts_dictation_callbacks():
    from distr.gui.oracle.global_ptt_hotkey import GlobalPttHotkeyListener

    on_pressed = MagicMock()
    on_released = MagicMock()
    on_combo_pressed = MagicMock()
    on_combo_released = MagicMock()
    get_enabled = MagicMock(return_value=True)
    get_combo = MagicMock(return_value={"option", "command"})
    get_dictation_combo = MagicMock(return_value=("control_command", "d"))

    listener = GlobalPttHotkeyListener(
        on_combo_pressed=on_combo_pressed,
        on_combo_released=on_combo_released,
        get_enabled=get_enabled,
        get_combo=get_combo,
        get_dictation_combo=get_dictation_combo,
        on_dictation_pressed=on_pressed,
        on_dictation_released=on_released,
    )

    assert listener._get_dictation_combo is get_dictation_combo
    assert listener._on_dictation_pressed is on_pressed
    assert listener._on_dictation_released is on_released
    assert listener._dictation_active is False


# ---------------------------------------------------------------------------
# 4. Test dictation hotkey not triggered when disabled (combo returns None)
# ---------------------------------------------------------------------------

def test_dictation_hotkey_not_triggered_when_disabled():
    from distr.gui.oracle.global_ptt_hotkey import GlobalPttHotkeyListener

    pressed_cb = MagicMock()
    # get_dictation_combo returns None (disabled)
    get_disabled_combo = MagicMock(return_value=None)

    listener = GlobalPttHotkeyListener(
        on_combo_pressed=MagicMock(),
        on_combo_released=MagicMock(),
        get_enabled=MagicMock(return_value=True),
        get_combo=MagicMock(return_value=set()),
        get_dictation_combo=get_disabled_combo,
        on_dictation_pressed=pressed_cb,
        on_dictation_released=MagicMock(),
    )

    # Simulate an internal press without actually starting the pynput listener
    # The dictation check is inside _on_press; with combo=None it must not fire
    # We test the logic directly by calling the private method with a mock key object.
    class FakeKey:
        name = "d"
        char = "d"
        vk = None

    import threading
    listener._lock = threading.Lock()
    listener._on_press(FakeKey())

    pressed_cb.assert_not_called()
    assert listener._dictation_active is False


def test_dictation_hotkey_maps_control_character_letter_and_releases():
    from distr.gui.oracle.global_ptt_hotkey import GlobalPttHotkeyListener

    pressed_cb = MagicMock()
    released_cb = MagicMock()

    listener = GlobalPttHotkeyListener(
        on_combo_pressed=MagicMock(),
        on_combo_released=MagicMock(),
        get_enabled=MagicMock(return_value=True),
        get_combo=MagicMock(return_value=set()),
        get_dictation_combo=MagicMock(return_value=("control_command", "d")),
        on_dictation_pressed=pressed_cb,
        on_dictation_released=released_cb,
    )

    class ControlKey:
        name = "ctrl"
        char = None
        vk = None

    class CommandKey:
        name = "cmd"
        char = None
        vk = None

    class ControlD:
        name = ""
        char = "\x04"
        vk = None

    listener._on_press(ControlKey())
    listener._on_press(CommandKey())
    listener._on_press(ControlD())

    pressed_cb.assert_called_once()
    assert listener._dictation_active is True

    listener._on_release(ControlD())

    released_cb.assert_called_once()
    assert listener._dictation_active is False


def test_dictation_hotkey_uses_dynamic_combo_from_settings_callback():
    from distr.gui.oracle.global_ptt_hotkey import GlobalPttHotkeyListener

    pressed_cb = MagicMock()
    released_cb = MagicMock()
    combo = {"value": ("option_command", "m")}

    listener = GlobalPttHotkeyListener(
        on_combo_pressed=MagicMock(),
        on_combo_released=MagicMock(),
        get_enabled=MagicMock(return_value=True),
        get_combo=MagicMock(return_value=set()),
        get_dictation_combo=lambda: combo["value"],
        on_dictation_pressed=pressed_cb,
        on_dictation_released=released_cb,
    )

    class OptionKey:
        name = "alt"
        char = None
        vk = None

    class CommandKey:
        name = "cmd"
        char = None
        vk = None

    class MKey:
        name = "m"
        char = "m"
        vk = None

    listener._on_press(OptionKey())
    listener._on_press(CommandKey())
    listener._on_press(MKey())

    pressed_cb.assert_called_once()
    assert listener._dictation_active is True

    listener._on_release(MKey())

    released_cb.assert_called_once()
    assert listener._dictation_active is False


def test_dictation_hotkey_accepts_modifier_only_hold_combo():
    from distr.gui.oracle.global_ptt_hotkey import GlobalPttHotkeyListener

    pressed_cb = MagicMock()
    released_cb = MagicMock()

    listener = GlobalPttHotkeyListener(
        on_combo_pressed=MagicMock(),
        on_combo_released=MagicMock(),
        get_enabled=MagicMock(return_value=True),
        get_combo=MagicMock(return_value=set()),
        get_dictation_combo=MagicMock(return_value=("control_command", "")),
        on_dictation_pressed=pressed_cb,
        on_dictation_released=released_cb,
    )

    class ControlKey:
        name = "ctrl"
        char = None
        vk = None

    class CommandKey:
        name = "cmd"
        char = None
        vk = None

    listener._on_press(ControlKey())
    pressed_cb.assert_not_called()

    listener._on_press(CommandKey())
    pressed_cb.assert_called_once()
    assert listener._dictation_active is True

    listener._on_release(CommandKey())
    released_cb.assert_called_once()
    assert listener._dictation_active is False


def test_modifier_only_non_dictation_shortcut_fires_once_per_hold():
    from distr.gui.oracle.global_ptt_hotkey import GlobalPttHotkeyListener

    record_cb = MagicMock()

    listener = GlobalPttHotkeyListener(
        on_combo_pressed=MagicMock(),
        on_combo_released=MagicMock(),
        get_enabled=MagicMock(return_value=False),
        get_combo=MagicMock(return_value=set()),
        get_record_toggle_combo=MagicMock(return_value=("control_command", "")),
        on_record_toggle=record_cb,
    )

    class ControlKey:
        name = "ctrl"
        char = None
        vk = None

    class CommandKey:
        name = "cmd"
        char = None
        vk = None

    listener._on_press(ControlKey())
    listener._on_press(CommandKey())
    listener._on_press(CommandKey())

    record_cb.assert_called_once()

    listener._on_release(CommandKey())
    listener._on_press(CommandKey())

    assert record_cb.call_count == 2


def test_one_shot_dictation_does_not_consume_voice_commands():
    from distr.core.agent.services.llm.mixins.voice import VoiceDictationMixin

    class DummyDictation(VoiceDictationMixin):
        def __init__(self):
            self._is_dictating = True
            self._dictation_one_shot = True
            self._stop_dictation = MagicMock()

    dummy = DummyDictation()

    assert dummy._check_dictation_commands("enter this", "enter this") is False
    assert dummy._check_dictation_commands("stop dictating", "stop dictating") is False
    dummy._stop_dictation.assert_not_called()


def test_global_hotkey_refresh_releases_active_dictation_without_restart():
    from distr.gui.oracle.global_ptt_hotkey import GlobalPttHotkeyListener

    released_cb = MagicMock()

    listener = GlobalPttHotkeyListener(
        on_combo_pressed=MagicMock(),
        on_combo_released=MagicMock(),
        get_enabled=MagicMock(return_value=True),
        get_combo=MagicMock(return_value=set()),
        get_dictation_combo=MagicMock(return_value=("option_command", "m")),
        on_dictation_pressed=MagicMock(),
        on_dictation_released=released_cb,
    )
    listener._dictation_active = True
    listener.start = MagicMock()
    listener.stop = MagicMock()

    listener.refresh()

    released_cb.assert_called_once()
    assert listener._dictation_active is False
    listener.start.assert_not_called()
    listener.stop.assert_not_called()


# ---------------------------------------------------------------------------
# 5. Test WorkflowAgent.enable_computer_use sets flags
# ---------------------------------------------------------------------------

def test_workflow_agent_computer_use_mode_sets_flag():
    from distr.core.workflow_agent import WorkflowAgent

    settings = {
        "llm_provider": "Ollama",
        "llm_model": "llama3",
        "ollama_url": "http://localhost:11434/",
    }

    with patch("distr.core.llm_factory.resolve_settings_keys", return_value=("Ollama", "llama3")):
        with patch.object(WorkflowAgent, "_load_tools"):
            with patch("distr.core.agent.tools.loader.ensure_tool_cache_warmed_if_empty"):
                agent = WorkflowAgent(settings=settings)

    assert agent._computer_use_mode is False
    agent.enable_computer_use("test goal")
    assert agent._computer_use_mode is True
    assert agent._computer_use_goal == "test goal"


# ---------------------------------------------------------------------------
# 6. Test _CU_SYSTEM_PROMPT contains {goal} and key phrases
# ---------------------------------------------------------------------------

def test_workflow_agent_computer_use_system_prompt():
    from distr.core.workflow_agent import _CU_SYSTEM_PROMPT

    assert "{goal}" in _CU_SYSTEM_PROMPT
    assert "one physical action per turn" in _CU_SYSTEM_PROMPT.lower() or \
           "execute one physical action" in _CU_SYSTEM_PROMPT.lower() or \
           "one physical" in _CU_SYSTEM_PROMPT.lower()


# ---------------------------------------------------------------------------
# 7. Test _CU_ACTION_TOOLS contains expected tool names
# ---------------------------------------------------------------------------

def test_workflow_agent_computer_use_action_tools_set():
    from distr.core.workflow_agent import _CU_ACTION_TOOLS

    assert "click_at" in _CU_ACTION_TOOLS
    assert "type_clipboard" in _CU_ACTION_TOOLS
    assert "press_keys" in _CU_ACTION_TOOLS


# ---------------------------------------------------------------------------
# 8. Test StepExecutorMixin._is_computer_use_instruction
# ---------------------------------------------------------------------------

def test_step_executor_detects_computer_use_from_instruction():
    from distr.core.workflow.step_executor import StepExecutorMixin

    assert StepExecutorMixin._is_computer_use_instruction("click the save button") is True
    assert StepExecutorMixin._is_computer_use_instruction("run unit tests") is False


# ---------------------------------------------------------------------------
# 9. Test StepType.COMPUTER_USE value
# ---------------------------------------------------------------------------

def test_computer_use_step_type_registered():
    from distr.core.workflow_engine.step_types import StepType

    assert StepType.COMPUTER_USE.value == "computer_use"


# ---------------------------------------------------------------------------
# 10. Test ComputerUseConfig defaults
# ---------------------------------------------------------------------------

def test_computer_use_config_defaults():
    from distr.core.workflow_engine.step_types import ComputerUseConfig

    cfg = ComputerUseConfig()
    assert cfg.goal == ""
    assert cfg.max_iterations == 15
    assert cfg.stuck_threshold == 3
