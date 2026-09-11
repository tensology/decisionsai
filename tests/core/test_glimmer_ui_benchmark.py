from __future__ import annotations

from pathlib import Path

from scripts.benchmark_glimmer_ui import FILES, REQUIRED_SKILLS, static_grade, write_workspace


def test_ui_fixture_and_static_grader(tmp_path: Path):
    write_workspace(tmp_path)
    originals = {
        relative: __import__("hashlib").sha256((tmp_path / relative).read_bytes()).hexdigest()
        for relative in FILES
    }
    grade = static_grade(tmp_path, originals)

    assert grade["tests"]["passed"] >= 5
    assert grade["max_score"] == 25
    assert not grade["scope"]["protected_changes"]


def test_required_harness_skills_cover_ui_quality_stack():
    assert {"ponytail", "impeccable", "browser-qa", "frontend-design-direction"}.issubset(REQUIRED_SKILLS)
