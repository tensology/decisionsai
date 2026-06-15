"""Regression tests for global voice selection save + hot reload."""

from unittest.mock import patch

from distr.core.services import settings_service


def test_save_voice_selection_emits_hot_reload_after_persist():
    settings = {
        "voice_provider": "kokoro",
        "kokoro_voice": "af_heart",
    }
    notified = []

    def _fake_notify(pid, vm):
        notified.append((pid, vm))

    with patch.object(settings_service, "load_settings_from_db", return_value=dict(settings)):
        with patch.object(settings_service, "save_settings_to_db") as save_mock:
            with patch.object(settings_service, "apply_voice_selection_to_settings", return_value=True):
                with patch.object(settings_service, "notify_voice_hot_reload_for_running_agent", side_effect=_fake_notify):
                    settings_service.save_voice_selection("kokoro", "af_bella")

    save_mock.assert_called_once()
    assert notified
    assert notified[0][0] == "kokoro"
