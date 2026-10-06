import os
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

    indicator.set_state("idle")
    assert not indicator.isVisible()

    indicator.set_oracle_hidden(True)
    app.processEvents()
    assert indicator.isVisible()

    pointer = QtGui.QGuiApplication.primaryScreen().availableGeometry().center()
    QtGui.QCursor.setPos(pointer)
    indicator._move_beside_pointer()
    assert indicator.pos() == pointer + QtCore.QPoint(16, 16)

    for state in ("idle", "listening", "thinking", "speaking"):
        indicator.set_state(state)
        image = QtGui.QImage(indicator.size(), QtGui.QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(0)
        indicator.render(image)
        assert any(image.pixelColor(x, y).alpha() for x in range(image.width()) for y in range(image.height()))

    indicator.set_oracle_hidden(False)
    assert not indicator.isVisible()
    indicator.close()


def test_oracle_maps_hidden_voice_states_including_idle() -> None:
    indicator = MagicMock()
    oracle = SimpleNamespace(
        oracle_visible=False,
        isVisible=MagicMock(return_value=False),
        _hidden_tts_active=False,
        _hidden_voice_indicator=indicator,
        _event_dispatcher=MagicMock(),
    )

    OracleWindow._sync_hidden_voice_indicator(oracle, "ptt_active")
    indicator.set_state.assert_called_with("listening")

    OracleWindow._sync_hidden_voice_indicator(oracle, "thinking")
    indicator.set_state.assert_called_with("thinking")

    oracle._hidden_tts_active = True
    OracleWindow._sync_hidden_voice_indicator(oracle, "idle")
    indicator.set_state.assert_called_with("speaking")

    oracle._hidden_tts_active = False
    OracleWindow._sync_hidden_voice_indicator(oracle, "idle")
    indicator.set_state.assert_called_with("idle")


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
    )

    HiddenVoiceIndicator._sync_visibility(indicator)

    indicator.show.assert_not_called()
    indicator.raise_.assert_not_called()
    indicator._ensure_macos_overlay_level.assert_not_called()
    indicator._timer.start.assert_called_once_with()


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
