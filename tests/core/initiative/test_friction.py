"""H: the platform watching its own friction, off unless switched on."""

import json

import pytest

from distr.core.initiative import friction


@pytest.fixture()
def seen_file(monkeypatch, tmp_path):
    path = tmp_path / "seen.json"
    monkeypatch.setattr(friction, "_seen_path", lambda: path)
    return path


def _log(tmp_path, lines):
    path = tmp_path / "decisions.log"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


# ------------------------------------------------------------------ gating

def test_off_by_default():
    result = friction.run(settings={})
    assert result["enabled"] is False
    assert result["written"] == []


def test_off_when_the_setting_is_absent():
    assert friction.run(settings=None if False else {})["enabled"] is False


def test_on_only_when_the_setting_is_set(monkeypatch, tmp_path, seen_file):
    monkeypatch.setattr(friction, "collect", lambda **k: [])
    assert friction.run(settings={friction.ENABLED_SETTING: True}, directory=tmp_path)["enabled"] is True


# ------------------------------------------------------------------ scanning

def test_groups_and_scores_findings(tmp_path):
    path = _log(tmp_path, [
        "2026-11-26 10:00:00,000 - distr.core.foo - WARNING - Same thing 11111",
        "2026-11-26 10:00:01,000 - distr.core.foo - WARNING - Same thing 22222",
        "2026-11-26 10:00:02,000 - distr.core.foo - WARNING - Same thing 33333",
        "2026-11-26 10:00:03,000 - distr.core.bar - ERROR - Something broke",
        "2026-11-26 10:00:04,000 - distr.core.baz - INFO - all good",
    ])
    findings = friction.collect(path=path)
    kinds = {f.kind for f in findings}
    assert kinds == {"repeated_warning", "error"}
    # the error outranks the repeated warning
    assert findings[0].kind == "error"
    assert next(f for f in findings if f.kind == "repeated_warning").count == 3


def test_a_failed_tool_call_is_a_finding_even_at_info(tmp_path):
    """distr.agent.activity logs tool failures at INFO."""
    path = _log(tmp_path, [
        "2026-11-26 10:00:00,000 - distr.agent.activity - INFO - [agent_tool] tool=vision_analyzer status=failed instruction=x",
    ])
    findings = friction.collect(path=path)
    assert [f.kind for f in findings] == ["tool_failure"]
    assert findings[0].severity == friction.SEVERITY["tool_failure"]


def test_numbers_and_uuids_are_normalised_so_a_repeat_groups(tmp_path):
    path = _log(tmp_path, [
        "2026-11-26 10:00:00,000 - distr.core.foo - ERROR - failed for ticket 1234567",
        "2026-11-26 10:00:01,000 - distr.core.foo - ERROR - failed for ticket 7654321",
    ])
    findings = friction.collect(path=path)
    assert len(findings) == 1
    assert findings[0].count == 2


def test_score_grows_with_repeats():
    a = friction.Finding(kind="error", where="x", summary="s", count=1, severity=4)
    b = friction.Finding(kind="error", where="x", summary="s", count=5, severity=4)
    assert b.score > a.score


def test_a_missing_log_is_not_an_error(tmp_path):
    assert friction.collect(path=tmp_path / "nope.log") == []


def test_junk_lines_are_ignored(tmp_path):
    path = _log(tmp_path, ["not a log line", "", "2026-13-99 nonsense"])
    assert friction.collect(path=path) == []


# ------------------------------------------------------------------ filing

def _finding(**over):
    base = dict(kind="error", where="distr.core.foo", summary="Something broke", count=2, severity=4)
    base.update(over)
    return friction.Finding(**base)


def test_findings_are_written_as_markdown(tmp_path, seen_file):
    result = friction.file_findings([_finding()], directory=tmp_path, now=1000.0)
    assert result["written"] == ["error-something-broke.md"]
    text = (tmp_path / "error-something-broke.md").read_text()
    assert text.startswith("# Something broke")
    assert "Frustration score" in text
    assert "Suggested fix" in text


def test_the_same_finding_is_not_filed_twice(tmp_path, seen_file):
    friction.file_findings([_finding()], directory=tmp_path, now=1000.0)
    again = friction.file_findings([_finding()], directory=tmp_path, now=1000.0 + 60)
    assert again["written"] == []
    assert again["skipped"] == 1


def test_a_finding_is_refiled_after_the_window(tmp_path, seen_file):
    friction.file_findings([_finding()], directory=tmp_path, now=1000.0)
    later = friction.file_findings([_finding()], directory=tmp_path, now=1000.0 + friction.DEDUP_WINDOW_S + 1)
    assert later["written"] == ["error-something-broke.md"]


def test_nothing_to_file_writes_nothing(tmp_path, seen_file):
    result = friction.file_findings([], directory=tmp_path, now=1000.0)
    assert result["written"] == []
    assert list(tmp_path.iterdir()) == []


def test_the_output_directory_is_the_platform_notes_folder():
    """NOT a project's planning folder."""
    path = str(friction.output_dir())
    assert path.endswith("Downloads/DecisionsAI/Tickets")
    assert "planning" not in path


def test_a_run_files_into_the_downloads_folder(monkeypatch, tmp_path, seen_file):
    monkeypatch.setattr(friction, "collect", lambda **k: [_finding()])
    result = friction.run(settings={friction.ENABLED_SETTING: True}, directory=tmp_path)
    assert result["enabled"] is True
    assert result["findings"] == 1
    assert result["written"] == ["error-something-broke.md"]


def test_a_run_never_raises_when_the_directory_is_unwritable(monkeypatch, tmp_path, seen_file):
    monkeypatch.setattr(friction, "collect", lambda **k: [_finding()])
    result = friction.run(settings={friction.ENABLED_SETTING: True}, directory=tmp_path / "a" / "\0bad")
    assert result["enabled"] is True


def test_the_seen_file_survives_a_reload(tmp_path, seen_file):
    friction.file_findings([_finding()], directory=tmp_path, now=1000.0)
    assert seen_file.is_file()
    assert json.loads(seen_file.read_text())
