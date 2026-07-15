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

        with patch("distr.core.services.settings_service.signal_manager"):
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

        with patch("distr.core.services.settings_service.signal_manager") as signal_manager:
            saved = save_shortcut_settings(SimpleNamespace(
                global_ptt_hotkey_enabled=True,
                global_ptt_hotkey_combo="option_command",
                dictation_hotkey_enabled=True,
                dictation_hotkey_modifier="control_command",
                dictation_hotkey_key="m",
            ))

        assert saved["global_ptt_hotkey_combo"] == "option_command"
        assert saved["dictation_hotkey_enabled"] is True
        assert saved["dictation_hotkey_modifier"] == "control_command"
        assert saved["dictation_hotkey_key"] == "m"
        mock_emit.assert_any_call(
            signal_manager.shortcut_settings_changed,
            label="shortcut_settings_changed",
        )

    @patch("distr.core.services.settings_service.save_settings_to_db")
    @patch("distr.core.services.settings_service.load_settings_from_db", return_value={
        "global_ptt_hotkey_enabled": True,
        "global_ptt_hotkey_combo": "option_command",
        "dictation_hotkey_enabled": True,
        "dictation_hotkey_modifier": "control_command",
        "dictation_hotkey_key": "",
    })
    def test_rejects_dictation_modifier_overlap_with_ptt(self, _load, mock_save):
        from distr.core.services.settings_service import save_shortcut_settings

        try:
            save_shortcut_settings(SimpleNamespace(
                global_ptt_hotkey_enabled=True,
                global_ptt_hotkey_combo="option_command",
                dictation_hotkey_enabled=True,
                dictation_hotkey_modifier="option_command",
                dictation_hotkey_key="",
            ))
        except ValueError as exc:
            assert "overlaps Push-to-Talk" in str(exc)
        else:
            raise AssertionError("Expected overlapping dictation/PTT modifiers to be rejected")

        mock_save.assert_not_called()

    @patch("distr.core.services.settings_service.save_settings_to_db")
    @patch("distr.core.services.settings_service.load_settings_from_db", return_value={
        "recording_hotkey_enabled": True,
        "recording_hotkey_modifier": "option_command",
        "recording_hotkey_key": "s",
        "web_hotkey_chat_modifier": "control_shift",
        "web_hotkey_chat_key": "c",
    })
    def test_rejects_any_changed_shortcut_overlap(self, _load, mock_save):
        from distr.core.services.settings_service import save_shortcut_settings

        try:
            save_shortcut_settings(SimpleNamespace(
                global_ptt_hotkey_enabled=False,
                global_ptt_hotkey_combo="option_command",
                recording_hotkey_enabled=True,
                recording_hotkey_modifier="control_shift",
                recording_hotkey_key="c",
                dictation_hotkey_enabled=False,
            ))
        except ValueError as exc:
            message = str(exc)
            assert "overlaps" in message
            assert "Recording" in message
            assert "Chat launcher" in message
        else:
            raise AssertionError("Expected duplicate shortcut combo to be rejected")

        mock_save.assert_not_called()

    @patch("distr.core.services.settings_service.save_settings_to_db")
    @patch("distr.core.services.settings_service.load_settings_from_db", return_value={})
    def test_accepts_enabled_dictation_modifier_only_hold_combo(self, _load, mock_save):
        from distr.core.services.settings_service import save_shortcut_settings

        saved = save_shortcut_settings(SimpleNamespace(
            global_ptt_hotkey_enabled=True,
            global_ptt_hotkey_combo="option_command",
            dictation_hotkey_enabled=True,
            dictation_hotkey_modifier="control_command",
            dictation_hotkey_key="",
        ))

        assert saved["dictation_hotkey_modifier"] == "control_command"
        assert saved["dictation_hotkey_key"] == ""
        mock_save.assert_called_once()
