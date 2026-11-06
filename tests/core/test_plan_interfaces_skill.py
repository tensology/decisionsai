from pathlib import Path

from distr.core.planning.conversation import _interfaces_skill_text, _planning_images


def test_interfaces_skill_covers_flow_spacing_and_planning_images():
    text = _interfaces_skill_text()
    assert "link" in text
    assert "/.planning/" in text
    assert "Do not invent routes" in text
    assert "backend" in text and "frontend" in text and "ajax" in text and "guidance" in text
    assert Path("skills/plan-interfaces/SKILL.md").is_file()


def test_planning_images_are_filenames_only(tmp_path):
    folder = tmp_path / ".planning"
    folder.mkdir()
    (folder / "customers.png").write_bytes(b"x")
    (folder / "notes.txt").write_text("no", encoding="utf-8")
    assert _planning_images(str(tmp_path)) == ["customers.png"]
    assert _planning_images("") == []
