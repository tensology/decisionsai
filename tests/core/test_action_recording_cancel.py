"""Cancel on the post-recording name dialog should discard the saved action."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch


def test_dismissing_the_naming_dialog_keeps_the_recording():
    """A dismissed naming dialog must not destroy a just-finished recording.

    This used to call cancel_recorded_action(action_id), which deleted the action
    and its recording file - so an Esc, a click-away or a stray right-click lost
    the recording. Naming is not a destructive decision.
    """
    from pathlib import Path

    source = Path("distr/app/main.py").read_text(encoding="utf-8")
    start = source.index("def _on_waiting_for_action_name")
    end = source.index("def _install_macos_wake_observer")
    handler = source[start:end]

    assert "cancel_recorded_action(action_id)" not in handler, (
        "dismissing the naming dialog must not delete the recording"
    )
    assert "keep_title" in handler
    assert "Rename it any time from the Actions list" in handler


def test_cancel_recorded_action_deletes_action_and_recording(tmp_path):
    from distr.core.actions.recorder_host import ActionRecorderHost

    recording_name = "one-42.json"
    recording_path = tmp_path / recording_name
    recording_path.write_text("[]", encoding="utf-8")

    action = SimpleNamespace(
        id=42,
        recording_filename=recording_name,
    )
    session = MagicMock()
    session.query.return_value.get.return_value = action

    with patch("distr.core.actions.recorder_host.signal_manager") as signal_mock:
        host = ActionRecorderHost()
        host.waiting_for_action_name_id = 42
        with patch("distr.core.actions.recorder_host.get_session") as get_session_mock:
            get_session_mock.return_value.__enter__.return_value = session
            with patch("distr.core.actions.recorder_host.RECORDINGS_DIR", str(tmp_path)):
                with patch("distr.core.actions.recorder_host.speak_text_directly_event_queue") as speak_mock:
                    ok = host.cancel_recorded_action(42)

    assert ok is True
    session.delete.assert_called_once_with(action)
    session.commit.assert_called_once()
    assert not recording_path.exists()
    assert host.waiting_for_action_name_id is None
    signal_mock.action_recording_cancelled.emit.assert_called_once_with(42)
    signal_mock.interrupt_tts.emit.assert_called_once()
    speak_mock.assert_called_once_with("Action cancelled.")


def test_stop_recording_does_not_speak_naming_prompt_after_waiting_dialog():
    """Naming TTS must not run after the blocking name dialog returns."""
    from pathlib import Path

    source = Path("distr/core/actions/recorder_host.py").read_text(encoding="utf-8")
    emit_idx = source.index("signal_manager.waiting_for_action_name.emit(action_id)")
    after_emit = source[emit_idx:emit_idx + 500]
    assert "speak_text_directly_event_queue" not in after_emit
    assert "Confirm or provide a new name" not in after_emit


def test_cancel_recorded_action_missing_action_returns_false():
    from distr.core.actions.recorder_host import ActionRecorderHost

    session = MagicMock()
    session.query.return_value.get.return_value = None

    with patch("distr.core.actions.recorder_host.signal_manager"):
        host = ActionRecorderHost()
        host.waiting_for_action_name_id = 99
        with patch("distr.core.actions.recorder_host.get_session") as get_session_mock:
            get_session_mock.return_value.__enter__.return_value = session
            ok = host.cancel_recorded_action(99)

    assert ok is False
    assert host.waiting_for_action_name_id is None
