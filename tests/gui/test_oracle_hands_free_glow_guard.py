from pathlib import Path


def test_oracle_ignores_hands_free_glow_on_when_mode_is_off():
    source = Path("distr/gui/oracle/window.py").read_text(encoding="utf-8")
    block = source.split("def on_hands_free_glow_on", 1)[1].split("def on_hands_free_glow_off", 1)[0]
    assert "if not self.is_hands_free:" in block
    assert "Ignoring stale hands-free glow ON while hands-free mode is OFF" in block


def test_manual_disable_clears_pending_hands_free_restore():
    source = Path("distr/gui/oracle/window.py").read_text(encoding="utf-8")
    disable_block = source.split("def disable_hands_free", 1)[1].split("def save_hands_free_state", 1)[0]
    toggle_block = source.split("def toggle_hands_free", 1)[1].split("def start_hold_to_talk", 1)[0]
    assert "clear_pending_restore: bool = False" in disable_block
    assert "self._hands_free_before_dictation = False" in disable_block
    assert "self.disable_hands_free(clear_pending_restore=True)" in toggle_block
    assert "'clear_pending_restore': True" in disable_block
    assert 'force_idle("hands_free_disable_safety")' in disable_block


def test_dictation_suspend_does_not_persist_hands_free_off():
    source = Path("distr/gui/oracle/window.py").read_text(encoding="utf-8")
    start_block = source.split("def on_dictation_started", 1)[1].split(
        "def _on_hands_free_mode_changed", 1
    )[0]
    assert "self.disable_hands_free(persist=False)" in start_block


def test_oracle_defaults_to_ptt_and_menu_shows_explicit_state():
    window_source = Path("distr/gui/oracle/window.py").read_text(encoding="utf-8")
    menu_source = Path("distr/gui/oracle/menu.py").read_text(encoding="utf-8")
    settings_source = Path("distr/core/settings.py").read_text(encoding="utf-8")
    db_source = Path("distr/core/db/__init__.py").read_text(encoding="utf-8")

    assert "self.settings.get('hands_free_mode', False)" in window_source
    assert "'hands_free_mode': False" in settings_source
    assert "hands_free_mode = Column(Boolean, default=False)" in db_source
    assert 'hands_free_label = f"Hands-Free Mode: {' in menu_source
    assert 'action.setText(f"Hands-Free Mode: {' in window_source
    assert "self._sync_hands_free_menu_state()" in window_source


def test_hands_free_menu_syncs_label_and_check_state():
    from distr.gui.oracle.window import OracleWindow

    class Action:
        def __init__(self):
            self.checked = None
            self.text = None

        def setChecked(self, value):
            self.checked = value

        def setText(self, value):
            self.text = value

    class Harness:
        hands_free_action = Action()
        is_hands_free = False

    harness = Harness()
    OracleWindow._sync_hands_free_menu_state(harness)
    assert harness.hands_free_action.checked is False
    assert harness.hands_free_action.text == "Hands-Free Mode: OFF"

    harness.is_hands_free = True
    OracleWindow._sync_hands_free_menu_state(harness)
    assert harness.hands_free_action.checked is True
    assert harness.hands_free_action.text == "Hands-Free Mode: ON"


def test_disable_hands_free_forces_idle_when_revert_stack_is_stale():
    from distr.gui.oracle.window import OracleWindow

    class Dispatcher:
        current_hook = "hands_free_listening"
        forced_reasons = []

        def revert_hook(self, _hook, *, trigger=None):
            return None

        def get_current_hook(self):
            return self.current_hook

        def force_idle(self, reason):
            self.current_hook = "idle"
            self.forced_reasons.append(reason)

    class Harness:
        is_hands_free = True
        _event_dispatcher = Dispatcher()

        def _sync_hands_free_menu_state(self):
            return None

    harness = Harness()

    OracleWindow.disable_hands_free(harness, persist=False)

    assert harness.is_hands_free is False
    assert harness._event_dispatcher.current_hook == "idle"
    assert harness._event_dispatcher.forced_reasons == ["hands_free_disable_safety"]


class _InteractionDispatcher:
    def __init__(self, current_hook):
        self.current_hook = current_hook
        self.forced_reasons = []
        self.fired = []

    def get_current_hook(self):
        return self.current_hook

    def force_idle(self, reason):
        self.forced_reasons.append(reason)
        self.current_hook = "idle"

    def fire_hook(self, hook, *, trigger=None):
        self.fired.append((hook, trigger))
        self.current_hook = hook


def _interaction_harness(current_hook, **state):
    class Harness:
        is_dictating = False
        _dictation_hotkey_active = False
        hold_to_talk_active = False
        ptt_requested = False
        is_hands_free = False

    harness = Harness()
    harness._event_dispatcher = _InteractionDispatcher(current_hook)
    for name, value in state.items():
        setattr(harness, name, value)
    return harness


def test_interaction_reconciler_clears_stale_hands_free_glow_after_dictation_release():
    from distr.gui.oracle.window import OracleWindow

    harness = _interaction_harness("hands_free_listening")

    OracleWindow._reconcile_interaction_visual_state(harness, "dictation_hotkey_release")

    assert harness._event_dispatcher.current_hook == "idle"
    assert harness._event_dispatcher.forced_reasons == [
        "interaction_reconcile:dictation_hotkey_release"
    ]


def test_interaction_reconciler_clears_stale_hands_free_glow_after_ptt_release():
    from distr.gui.oracle.window import OracleWindow

    harness = _interaction_harness("hands_free_listening")

    OracleWindow._reconcile_interaction_visual_state(harness, "ptt_release")

    assert harness._event_dispatcher.current_hook == "idle"
    assert harness._event_dispatcher.forced_reasons == [
        "interaction_reconcile:ptt_release"
    ]


def test_interaction_reconciler_preserves_non_interaction_agent_visual():
    from distr.gui.oracle.window import OracleWindow

    harness = _interaction_harness("thinking")

    OracleWindow._reconcile_interaction_visual_state(harness, "dictation_stopped")

    assert harness._event_dispatcher.current_hook == "thinking"
    assert harness._event_dispatcher.forced_reasons == []
    assert harness._event_dispatcher.fired == []


def test_interaction_reconciler_repairs_visual_for_active_runtime_mode():
    from distr.gui.oracle.window import OracleWindow

    harness = _interaction_harness("idle", is_hands_free=True)

    OracleWindow._reconcile_interaction_visual_state(harness, "capture_release")

    assert harness._event_dispatcher.current_hook == "hands_free_listening"
    assert harness._event_dispatcher.fired == [
        ("hands_free_listening", "oracle:interaction_reconcile:capture_release")
    ]
