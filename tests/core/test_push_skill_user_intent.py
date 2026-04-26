"""Tests for Pi skill push user intent file (USER_INTENT.md)."""
import tempfile
from pathlib import Path

from distr.core.pi_skill_push_files import USER_INTENT_FILENAME, write_pi_skill_user_intent


def test_write_pi_skill_user_intent_creates_file_with_use_block() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp) / "my-skill"
        d.mkdir()
        p = write_pi_skill_user_intent(d, "my-skill", "audit the auth layer")
        assert p is not None
        assert p.name == USER_INTENT_FILENAME
        text = p.read_text(encoding="utf-8")
        assert "Use this skill to" in text
        assert "audit the auth layer" in text
        assert ".pi/skills/my-skill/" in text


def test_write_pi_skill_user_intent_empty_removes_file() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp) / "x"
        d.mkdir()
        target = d / USER_INTENT_FILENAME
        target.write_text("old", encoding="utf-8")
        out = write_pi_skill_user_intent(d, "x", "   ")
        assert out is None
        assert not target.exists()
