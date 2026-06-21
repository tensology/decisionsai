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
