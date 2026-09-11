from types import SimpleNamespace
from unittest.mock import MagicMock

from distr.gui.oracle import window as oracle_window


def test_ptt_capture_starts_immediately_instead_of_waiting_for_debounce(monkeypatch):
    start_signal = MagicMock()
    stop_signal = MagicMock()
    monkeypatch.setattr(
        oracle_window,
        "signal_manager",
        SimpleNamespace(
            push_to_talk_start=SimpleNamespace(emit=start_signal),
            push_to_talk_stop=SimpleNamespace(emit=stop_signal),
        ),
    )
    monkeypatch.setattr(
        oracle_window.QtWidgets,
        "QApplication",
        SimpleNamespace(instance=lambda: SimpleNamespace()),
    )
    timer = MagicMock()
    dispatcher = MagicMock()
    oracle = SimpleNamespace(
        is_hands_free=False,
        dragging=False,
        stt_ready=True,
        ptt_requested=False,
        is_listening=True,
        hold_to_talk_active=False,
        ptt_delay_ms=300,
        ptt_delay_timer=timer,
        _event_dispatcher=dispatcher,
        _dismiss_tts_player_for_capture=MagicMock(),
        _cleanup_ptt=MagicMock(),
        _mark_voice_capture_blocked_not_listening=MagicMock(),
        _reconcile_interaction_visual_state=MagicMock(),
        _maybe_prompt_enable_listening_on_release=MagicMock(),
    )

    oracle_window.OracleWindow.start_hold_to_talk(oracle)

    start_signal.assert_called_once_with()
    assert oracle.ptt_requested is True
    timer.start.assert_not_called()

    timer.isActive.return_value = False
    oracle_window.OracleWindow.stop_hold_to_talk(oracle)

    stop_signal.assert_called_once_with()
    assert oracle.ptt_requested is False
