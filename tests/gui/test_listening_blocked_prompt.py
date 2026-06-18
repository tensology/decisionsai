"""Tests for the not-listening prompt when voice capture is blocked."""

from pathlib import Path


def test_oracle_gates_dictation_when_not_listening():
    source = Path("distr/gui/oracle/window.py").read_text()
    for method_name in (
        "_on_dictation_hotkey_pressed",
        "_on_ticket_dictation_hotkey_pressed",
    ):
        start = source.index(f"    def {method_name}")
        end = source.index("\n    def ", start + 1)
        method = source[start:end]
        assert "if not self.is_listening:" in method
        assert "_mark_voice_capture_blocked_not_listening" in method


def test_oracle_prompts_on_release_after_blocked_capture():
    source = Path("distr/gui/oracle/window.py").read_text()
    assert "_prompt_enable_listening_after_blocked_capture" in source
    assert "The agent wasn't listening" in source
    assert "_is_listening_blocked_prompt_open" in source
    assert "_listening_blocked_prompt" in source
    for method_name in (
        "stop_hold_to_talk",
        "_on_dictation_hotkey_released",
        "_on_ticket_dictation_hotkey_released",
    ):
        start = source.index(f"    def {method_name}")
        end = source.index("\n    def ", start + 1)
        method = source[start:end]
        assert "_maybe_prompt_enable_listening_on_release" in method


def test_oracle_does_not_duplicate_listening_prompt():
    source = Path("distr/gui/oracle/window.py").read_text()
    start = source.index("    def _maybe_prompt_enable_listening_on_release")
    end = source.index("\n    def ", start + 1)
    method = source[start:end]
    assert "_is_listening_blocked_prompt_open" in method
    start = source.index("    def _prompt_enable_listening_after_blocked_capture")
    end = source.index("\n    def ", start + 1)
    method = source[start:end]
    assert "if self._is_listening_blocked_prompt_open():" in method


def test_oracle_marks_ptt_blocked_when_not_listening():
    source = Path("distr/gui/oracle/window.py").read_text()
    start = source.index("    def start_hold_to_talk")
    end = source.index("\n    def ", start + 1)
    method = source[start:end]
    assert "if not self.is_listening" in method
    assert "_mark_voice_capture_blocked_not_listening" in method
