from pathlib import Path

from distr.core.log_retention import rotate_oversize_file


def test_rotate_oversize_file_keeps_bounded_backups(tmp_path: Path) -> None:
    log = tmp_path / "agent.log"
    log.write_text("first", encoding="utf-8")
    assert rotate_oversize_file(log, max_bytes=1, backups=2) is True
    assert not log.exists()
    assert (tmp_path / "agent.log.1").read_text(encoding="utf-8") == "first"

    log.write_text("second", encoding="utf-8")
    assert rotate_oversize_file(log, max_bytes=1, backups=2) is True
    assert (tmp_path / "agent.log.1").read_text(encoding="utf-8") == "second"
    assert (tmp_path / "agent.log.2").read_text(encoding="utf-8") == "first"


def test_rotate_oversize_file_leaves_small_file_alone(tmp_path: Path) -> None:
    log = tmp_path / "agent.log"
    log.write_text("ok", encoding="utf-8")
    assert rotate_oversize_file(log, max_bytes=100) is False
    assert log.read_text(encoding="utf-8") == "ok"
