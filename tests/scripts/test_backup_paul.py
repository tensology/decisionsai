from __future__ import annotations

from pathlib import Path

import pytest

from scripts import backup_paul


def test_plan_excludes_scraping_and_keeps_existing_files(tmp_path: Path) -> None:
    source = tmp_path / "source"
    destination = tmp_path / "destination"
    source.mkdir()
    destination.mkdir()
    (source / "new.txt").write_text("new", encoding="utf-8")
    (source / "nested").mkdir()
    (source / "nested" / "inside.txt").write_text("inside", encoding="utf-8")
    (source / "existing.txt").write_text("replacement", encoding="utf-8")
    (destination / "existing.txt").write_text("original", encoding="utf-8")
    (source / "SCRAPING").mkdir()
    (source / "SCRAPING" / "secret.txt").write_text("excluded", encoding="utf-8")
    (source / "._SCRAPING").write_text("excluded metadata", encoding="utf-8")
    (source / ".Trashes").mkdir()
    (source / ".Trashes" / "deleted.txt").write_text("system metadata", encoding="utf-8")

    plan = backup_paul.build_plan(source, destination)

    assert {item.source.relative_to(source) for item in plan.files} == {
        Path("new.txt"),
        Path("nested/inside.txt"),
    }
    assert plan.required_bytes == 9
    assert plan.skipped_existing == 1
    assert all("SCRAPING" not in str(item.source) for item in plan.files)
    assert all(".Trashes" not in str(item.source) for item in plan.files)


def test_execute_plan_never_overwrites_an_existing_file(tmp_path: Path) -> None:
    source = tmp_path / "source"
    destination = tmp_path / "destination"
    source.mkdir()
    destination.mkdir()
    (source / "existing.txt").write_text("source version", encoding="utf-8")
    (source / "new.txt").write_text("new version", encoding="utf-8")
    (destination / "existing.txt").write_text("backup version", encoding="utf-8")

    plan = backup_paul.build_plan(source, destination)
    result = backup_paul.execute_plan(plan)
    backup_paul.verify_result(plan, result)

    assert (destination / "existing.txt").read_text(encoding="utf-8") == "backup version"
    assert (destination / "new.txt").read_text(encoding="utf-8") == "new version"
    assert result.copied_files == 1


def test_exclusive_copy_loses_race_without_overwrite(tmp_path: Path) -> None:
    source = tmp_path / "source.txt"
    destination = tmp_path / "destination.txt"
    source.write_text("source version", encoding="utf-8")
    item = backup_paul.PlannedFile(source, destination, source.stat().st_size)
    destination.write_text("appeared first", encoding="utf-8")

    class Progress:
        def update(self, amount: int) -> None:
            raise AssertionError(f"progress should not update: {amount}")

    assert backup_paul.copy_file_exclusive(item, Progress()) is False
    assert destination.read_text(encoding="utf-8") == "appeared first"


def test_insufficient_space_stops_before_copy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source"
    destination = tmp_path / "destination"
    source.mkdir()
    destination.mkdir()
    (source / "large.bin").write_bytes(b"1234567890")
    plan = backup_paul.build_plan(source, destination)
    fake_usage = shutil_disk_usage(total=100, used=95, free=5)
    monkeypatch.setattr(backup_paul.shutil, "disk_usage", lambda _: fake_usage)

    with pytest.raises(backup_paul.BackupError, match="Not enough free space"):
        backup_paul.validate_plan(plan, reserve_bytes=0)


class shutil_disk_usage(tuple):
    __slots__ = ()

    def __new__(cls, total: int, used: int, free: int):
        return tuple.__new__(cls, (total, used, free))

    total = property(lambda self: self[0])
    used = property(lambda self: self[1])
    free = property(lambda self: self[2])
