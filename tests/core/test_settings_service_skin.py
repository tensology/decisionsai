"""Tests for settings_service skin-related functions.

Validates:
- update_oracle_skin runs migration before persisting (Requirements 11.2, 11.8)
- save_general_settings no longer emits oracle skin/size signals (Requirement 8.8)
"""
from types import SimpleNamespace
from unittest.mock import patch, MagicMock, call


class TestUpdateOracleSkin:
    """update_oracle_skin should migrate the value before persisting."""

    @patch("distr.core.services.settings_service.update_setting")
    @patch("distr.core.services.settings_service.signal_manager")
    def test_gif_filename_migrated_to_oracle(self, mock_sm, mock_update):
        from distr.core.services.settings_service import update_oracle_skin
        update_oracle_skin("0.gif")
        mock_update.assert_called_once_with(
            "selected_oracle", "oracle",
            signal=mock_sm.direct_oracle_change,
            signal_args=("oracle",),
            signal_label="direct_oracle_change",
        )

    @patch("distr.core.services.settings_service.update_setting")
    @patch("distr.core.services.settings_service.signal_manager")
    def test_folder_name_passes_through(self, mock_sm, mock_update):
        from distr.core.services.settings_service import update_oracle_skin
        update_oracle_skin("clippy")
        mock_update.assert_called_once_with(
            "selected_oracle", "clippy",
            signal=mock_sm.direct_oracle_change,
            signal_args=("clippy",),
            signal_label="direct_oracle_change",
        )

    @patch("distr.core.services.settings_service.update_setting")
    @patch("distr.core.services.settings_service.signal_manager")
    def test_empty_string_defaults_to_oracle(self, mock_sm, mock_update):
        from distr.core.services.settings_service import update_oracle_skin
        update_oracle_skin("")
        mock_update.assert_called_once_with(
            "selected_oracle", "oracle",
            signal=mock_sm.direct_oracle_change,
            signal_args=("oracle",),
            signal_label="direct_oracle_change",
        )


class TestSaveGeneralSettingsNoOracleSignals:
    """save_general_settings must NOT emit direct_oracle_change or oracle_size_changed."""

    @patch("distr.core.services.settings_service.load_settings_from_db", return_value={})
    @patch("distr.core.services.settings_service.save_settings_to_db")
    @patch("distr.core.services.settings_service._safe_emit")
    def test_no_oracle_skin_signal(self, mock_emit, _save, _load):
        from distr.core.services.settings_service import save_general_settings

        data = MagicMock()
        data.__fields__ = {"oracle_position": None, "playback_speed": None,
                           "speech_volume": None, "vad_threshold": None,
                           "voice_provider": None}
        data.oracle_position = "custom"
        data.playback_speed = 1.0
        data.speech_volume = 100
        data.vad_threshold = 50
        data.voice_provider = "kokoro"

        save_general_settings(data)

        emitted_labels = [c.kwargs.get("label", "") for c in mock_emit.call_args_list]
        assert "direct_oracle_change" not in emitted_labels
        assert "oracle_size_changed" not in emitted_labels
        # But oracle_position_changed should still be emitted
        assert "oracle_position_changed" in emitted_labels


class TestSaveShortcutSettings:
    """Shortcut saves should notify the live hotkey listener immediately."""

    @patch("distr.core.services.settings_service._run_on_qt_main_thread", side_effect=lambda fn, *, label: fn())
    @patch("distr.core.services.settings_service._safe_emit")
    @patch("distr.core.services.settings_service.save_settings_to_db")
    @patch("distr.core.services.settings_service.load_settings_from_db", return_value={})
    def test_emits_shortcut_settings_changed(self, _load, _save, mock_emit, _run_qt):
        from distr.core.services.settings_service import save_shortcut_settings
        from distr.core.signals import signal_manager

        save_shortcut_settings(SimpleNamespace(
            dictation_hotkey_enabled=True,
            dictation_hotkey_modifier="option_command",
            dictation_hotkey_key="m",
        ))

        mock_emit.assert_any_call(
            signal_manager.shortcut_settings_changed,
            label="shortcut_settings_changed",
        )
