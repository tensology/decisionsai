import os
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6 import QtCore, QtGui, QtWidgets

from distr.app.main import Application
from distr.gui.oracle.hidden_voice_indicator import HiddenVoiceIndicator
from distr.gui.oracle.window import OracleWindow


def test_indicator_renders_every_hidden_voice_state() -> None:
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    indicator = HiddenVoiceIndicator()
    indicator._system_cursor_visible = lambda: True

    indicator.set_state("idle")
    assert not indicator.isVisible()

    indicator.set_oracle_hidden(True)
    app.processEvents()
    assert indicator.isVisible()

    pointer = QtGui.QGuiApplication.primaryScreen().availableGeometry().center()
    QtGui.QCursor.setPos(pointer)
    indicator._move_beside_pointer()
    assert indicator.pos() == pointer + QtCore.QPoint(16, 16)

    for state in ("idle", "listening", "dictating", "hands_free", "loading", "thinking", "speaking"):
        indicator.set_state(state)
        image = QtGui.QImage(indicator.size(), QtGui.QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(0)
        indicator.render(image)
        assert any(image.pixelColor(x, y).alpha() for x in range(image.width()) for y in range(image.height()))

    indicator.set_oracle_hidden(False)
    assert not indicator.isVisible()
    indicator._timer.stop()
    indicator.close()
    indicator.deleteLater()
    app.processEvents()


def test_indicator_hides_when_the_system_cursor_hides() -> None:
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    indicator = HiddenVoiceIndicator()
    visible = {"cursor": False}
    indicator._system_cursor_visible = lambda: visible["cursor"]

    indicator.set_oracle_hidden(True)
    indicator.set_state("listening")
    app.processEvents()
    assert indicator.state == "listening"
    assert not indicator.isVisible()

    visible["cursor"] = True
    indicator._tick()
    app.processEvents()
    assert indicator.isVisible()
    assert indicator.state == "listening"
    indicator._timer.stop()
    indicator.close()
    indicator.deleteLater()
    app.processEvents()


def test_oracle_maps_hidden_voice_states_including_idle() -> None:
    indicator = MagicMock()
    oracle = SimpleNamespace(
        oracle_visible=False,
        isVisible=MagicMock(return_value=False),
        _hidden_tts_active=False,
        _dictation_hotkey_active=False,
        _hidden_voice_indicator=indicator,
        _event_dispatcher=MagicMock(),
    )

    OracleWindow._sync_hidden_voice_indicator(oracle, "ptt_active")
    indicator.set_state.assert_called_with("listening")

    oracle._hidden_dictation_processing = False
    oracle._dictation_hotkey_active = True
    OracleWindow._sync_hidden_voice_indicator(oracle, "dictation")
    indicator.set_state.assert_called_with("dictating")

    oracle._dictation_hotkey_active = False
    OracleWindow._sync_hidden_voice_indicator(oracle, "dictation")
    indicator.set_state.assert_called_with("idle")

    OracleWindow._sync_hidden_voice_indicator(oracle, "hands_free_listening")
    indicator.set_state.assert_called_with("hands_free")

    oracle._hidden_dictation_processing = True
    OracleWindow._sync_hidden_voice_indicator(oracle, "idle")
    indicator.set_state.assert_called_with("loading")

    OracleWindow._sync_hidden_voice_indicator(oracle, "ptt_active")
    indicator.set_state.assert_called_with("listening")

    oracle._dictation_hotkey_active = True
    OracleWindow._sync_hidden_voice_indicator(oracle, "dictation")
    indicator.set_state.assert_called_with("dictating")
    oracle._hidden_dictation_processing = False
    oracle._dictation_hotkey_active = False

    OracleWindow._sync_hidden_voice_indicator(oracle, "thinking")
    indicator.set_state.assert_called_with("thinking")

    oracle._hidden_tts_active = True
    OracleWindow._sync_hidden_voice_indicator(oracle, "idle")
    indicator.set_state.assert_called_with("speaking")

    oracle._hidden_tts_active = False
    OracleWindow._sync_hidden_voice_indicator(oracle, "idle")
    indicator.set_state.assert_called_with("idle")


def test_dictation_hue_changes_over_time() -> None:
    start = HiddenVoiceIndicator._dictation_color(0.0)
    later = HiddenVoiceIndicator._dictation_color(1.0)

    assert start != later


def test_dictation_keeps_the_idle_glyph_shape() -> None:
    color = QtGui.QColor("magenta")
    indicator = SimpleNamespace(
        _dictation_color=MagicMock(return_value=color),
        _paint_idle=MagicMock(),
    )
    painter = MagicMock()

    HiddenVoiceIndicator._paint_dictating(indicator, painter, 1.0)

    indicator._paint_idle.assert_called_once_with(painter, color)


def test_hands_free_uses_blue_hues_and_restarts_each_user_turn(monkeypatch) -> None:
    times = iter((10.0, 12.0))
    monkeypatch.setattr(
        "distr.gui.oracle.hidden_voice_indicator.time.monotonic",
        lambda: next(times),
    )
    indicator = SimpleNamespace(
        STATES=HiddenVoiceIndicator.STATES,
        _state=None,
        _started_at=0.0,
        _sync_visibility=MagicMock(),
    )

    HiddenVoiceIndicator.set_state(indicator, "hands_free")
    first_start = indicator._started_at
    HiddenVoiceIndicator.set_state(indicator, "hands_free")

    assert indicator._started_at > first_start
    for phase in (0.0, 1.0, 2.0, 4.0):
        color = HiddenVoiceIndicator._hands_free_color(phase)
        assert 0.54 <= color.hsvHueF() <= 0.62


def test_dictation_release_returns_to_idle_until_large_paste_starts() -> None:
    oracle = SimpleNamespace(
        _dictation_hotkey_active=True,
        _last_dictation_hotkey_release_mono=0.0,
        _hidden_dictation_processing=False,
        _dictation_started_from_hotkey_deadline=0.0,
        is_dictating=False,
        _sync_hidden_voice_indicator=MagicMock(),
        _event_dispatcher=MagicMock(),
        _reconcile_interaction_visual_state=MagicMock(),
        update=MagicMock(),
        _maybe_prompt_enable_listening_on_release=MagicMock(),
    )

    OracleWindow._on_dictation_hotkey_released(oracle)

    assert oracle._hidden_dictation_processing is False
    oracle._sync_hidden_voice_indicator.assert_called()

    OracleWindow.on_dictation_processing_started(oracle)
    assert oracle._hidden_dictation_processing is True

    OracleWindow.on_dictation_processing_finished(oracle)

    assert oracle._hidden_dictation_processing is False


def test_visible_oracle_suppresses_indicator_when_saved_flag_is_stale() -> None:
    indicator = MagicMock()
    oracle = SimpleNamespace(
        oracle_visible=False,
        isVisible=MagicMock(return_value=True),
        _hidden_tts_active=False,
        _hidden_voice_indicator=indicator,
        _suppress_hidden_voice_indicator=MagicMock(
            side_effect=lambda: OracleWindow._suppress_hidden_voice_indicator(oracle)
        ),
        _event_dispatcher=MagicMock(),
    )

    OracleWindow._sync_hidden_voice_indicator(oracle, "thinking")

    indicator.set_oracle_hidden.assert_called_once_with(False)
    indicator.set_state.assert_called_once_with(None)


def test_state_change_does_not_reorder_visible_indicator() -> None:
    indicator = SimpleNamespace(
        _oracle_hidden=True,
        _state="listening",
        _move_beside_pointer=MagicMock(),
        isVisible=MagicMock(return_value=True),
        show=MagicMock(),
        raise_=MagicMock(),
        _ensure_macos_overlay_level=MagicMock(),
        _timer=MagicMock(),
        hide=MagicMock(),
        _system_cursor_visible=lambda: True,
    )
    indicator._timer.isActive.return_value = False

    HiddenVoiceIndicator._sync_visibility(indicator)

    indicator.show.assert_not_called()
    indicator.raise_.assert_not_called()
    indicator._ensure_macos_overlay_level.assert_not_called()
    indicator._timer.start.assert_called_once_with()


def test_macos_overlay_is_promoted_above_inactive_apps(monkeypatch) -> None:
    native_window = MagicMock()
    native_view = SimpleNamespace(window=lambda: native_window)
    objc_object = MagicMock(return_value=native_view)
    monkeypatch.setattr("distr.gui.oracle.hidden_voice_indicator.platform.system", lambda: "Darwin")
    monkeypatch.setattr(QtGui.QGuiApplication, "platformName", lambda: "cocoa")
    monkeypatch.setitem(sys.modules, "objc", SimpleNamespace(objc_object=objc_object))
    monkeypatch.setitem(
        sys.modules,
        "AppKit",
        SimpleNamespace(
            NSStatusWindowLevel=25,
            NSWindowCollectionBehaviorCanJoinAllSpaces=1,
            NSWindowCollectionBehaviorFullScreenAuxiliary=2,
            NSWindowCollectionBehaviorStationary=4,
        ),
    )
    indicator = SimpleNamespace(winId=lambda: 123)

    HiddenVoiceIndicator._ensure_macos_overlay_level(indicator)

    objc_object.assert_called_once()
    native_window.setLevel_.assert_called_once_with(25)
    native_window.setHidesOnDeactivate_.assert_called_once_with(False)
    native_window.orderFrontRegardless.assert_called_once_with()


def test_oracle_visibility_actions_persist_the_restart_state(monkeypatch) -> None:
    saved = []
    monkeypatch.setattr("distr.gui.oracle.window.save_settings_to_db", saved.append)
    monkeypatch.setattr("distr.gui.oracle.window.QTimer.singleShot", lambda *_args: None)

    oracle = SimpleNamespace(
        oracle_visible=True,
        settings={},
        _hidden_voice_indicator=MagicMock(),
        _sync_hidden_voice_indicator=MagicMock(),
        _chat_bubble=MagicMock(),
        player_window=None,
        isVisible=MagicMock(return_value=True),
        gif_label=MagicMock(),
        show=MagicMock(),
        hide=MagicMock(),
        raise_=MagicMock(),
        _ensure_macos_on_top=MagicMock(),
        _position_player_after_show=MagicMock(),
        update_menu=MagicMock(),
    )

    OracleWindow.hide_oracle(oracle)
    assert oracle.settings['oracle_visible'] is False
    assert saved[-1] == {'oracle_visible': False}

    OracleWindow.show_oracle(oracle)
    assert oracle.settings['oracle_visible'] is True
    assert saved[-1] == {'oracle_visible': True}


def test_hiding_oracle_resyncs_indicator_after_window_is_hidden(monkeypatch) -> None:
    visible = [True]
    hidden_state = [False]
    monkeypatch.setattr("distr.gui.oracle.window.save_settings_to_db", lambda *_args: None)
    monkeypatch.setattr(
        "distr.gui.oracle.window.QTimer.singleShot",
        lambda _delay, callback: callback(),
    )
    oracle = SimpleNamespace(
        oracle_visible=True,
        settings={},
        _hidden_voice_indicator=SimpleNamespace(
            set_oracle_hidden=lambda hidden: hidden_state.__setitem__(0, hidden),
        ),
        _sync_hidden_voice_indicator=MagicMock(),
        _chat_bubble=MagicMock(),
        player_window=None,
        isVisible=lambda: visible[0],
        hide=lambda: visible.__setitem__(0, False),
        update_menu=MagicMock(),
    )

    OracleWindow.hide_oracle(oracle)

    assert visible[0] is False
    assert hidden_state[0] is True
    oracle._sync_hidden_voice_indicator.assert_called_once_with()


def test_post_eula_startup_does_not_reshow_a_saved_hidden_oracle(monkeypatch) -> None:
    monkeypatch.setattr("distr.app.main.is_dock_app", lambda: False)
    monkeypatch.setattr("distr.app.main.QTimer.singleShot", lambda *_args: None)
    oracle = SimpleNamespace(
        oracle_visible=False,
        update_menu=MagicMock(),
        show=MagicMock(),
    )
    app = SimpleNamespace(
        oracle_window=oracle,
        settings={},
        about_window=MagicMock(),
        _splash_sound_played=False,
        _enable_device_check_timer=MagicMock(),
    )

    Application._continue_startup_after_eula(app)

    oracle.update_menu.assert_called_once_with()
    oracle.show.assert_not_called()
