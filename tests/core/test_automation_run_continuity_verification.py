"""2026-11-12 merrypak-1114: automation "Run continuity" boilerplate is not project work.

Thread 575 turn 1114 (Load latest Merrypak DB) ran `manage.py load_latest_db`, exited 0,
changed no project files and was still blocked as "Verification incomplete": the trailing
"read and update the retained Decisions automation memory" sentence made the turn
change-expected, so the receipt gate demanded a passing test that the task never asked for.
"""
from __future__ import annotations

import subprocess

import pytest

from distr.core.workflow import development_harness as harness
from tests.core.test_development_harness import _database

MERRYPAK_1114 = (
    "In /Users/paul/development/WORK/CRYSTALLOGIC/www.merrypak.co.za/backend/webapp, run the Merrypak virtualenv "
    "Python command `/Users/paul/.virtualenvs/merrypak/bin/python /Users/paul/development/WORK/CRYSTALLOGIC/"
    "www.merrypak.co.za/backend/webapp/manage.py load_latest_db`. Report whether it completed successfully, and "
    "include any important stdout/stderr if it fails.\n\n"
    "Run continuity: read and update the retained Decisions automation memory at "
    "/Users/paul/.codex/automations/.decisions-state/load-latest-merrypak-db/memory.md. "
    "Do not create another scheduler or recurring task."
)
DAILY_DECLARATION_CONTINUITY = (
    "Run the daily Tensology work-planning check. Then prompt Paul concisely to confirm today's work.\n\n"
    "Run continuity: use this automation's owned Development thread in DecisionsAI. Do not create a duplicate "
    "scheduler in Codex, Cursor, launchd, or cron.\n\n"
    "Avoid overlap: reuse the existing 09:00 Decisions Daily plan as the morning brief."
)


def test_run_continuity_bookkeeping_is_not_a_project_change():
    assert harness._prompt_expects_project_change(MERRYPAK_1114, autonomy_level="full") is False
    assert harness._prompt_expects_project_change(DAILY_DECLARATION_CONTINUITY, autonomy_level="full") is False


@pytest.mark.parametrize("prompt", [
    "Fix the footer and verify it.\n\nRun continuity: read and update the retained memory at /tmp/memory.md.",
    "Run continuity: read and update the retained memory at /tmp/memory.md.\n\nThen update the README.",
    "Append new rows with scripts/append.py and write the manifest.\n\nRun continuity: read the memory.",
])
def test_real_work_around_run_continuity_still_expects_a_change(prompt):
    assert harness._prompt_expects_project_change(prompt, autonomy_level="full") is True


def _dispatch(tmp_path, monkeypatch, prompt):
    from distr.core.turn_runtime import TurnResult
    _, chat_id, _ = _database(tmp_path, monkeypatch)
    root = tmp_path / "project"
    subprocess.run(["git", "init", "-b", "main", str(root)], check=True, capture_output=True)
    (root / "app.txt").write_text("before\n")
    subprocess.run(["git", "-C", str(root), "add", "."], check=True)
    subprocess.run(["git", "-C", str(root), "-c", "user.name=Test", "-c", "user.email=test@example.com",
                    "commit", "-m", "baseline"], check=True, capture_output=True)

    async def execute(request, *, on_event, **kwargs):
        # Same shape as 1114: command ran fine, memory outside the repo updated, no project edits,
        # no recognised test/build command (grep/awk/psql checks are not receipts).
        return TurnResult(success=True, backend_id="pi", model="fixture", runtime_id="cli_harness",
                          output="`manage.py load_latest_db` completed successfully - exit code 0, clean log.")

    monkeypatch.setattr("distr.core.turn_runtime.execute_turn", execute)
    monkeypatch.setattr("distr.core.project_cli_backends.catalog_probe.probe_cli_backend",
                        lambda *a, **k: {"status": {"ready": True}})
    return harness.dispatch_development_prompt(chat_id, prompt, dispatch_async=False)


def test_clean_automation_run_is_not_blocked_as_verification_incomplete(tmp_path, monkeypatch):
    result = _dispatch(tmp_path, monkeypatch, MERRYPAK_1114)
    assert result["status"] == "completed", result.get("error")
    assert not result.get("error")
    assert "Verification incomplete" not in result["summary"]
    assert "can't call it done" not in result["summary"]
    # The receipt report stays honest: nothing was required and nothing was run.
    assert result["outcome"]["verification"] == {"status": "not_run", "checks": [], "required": [], "missing": []}


def test_receipt_gate_still_blocks_real_fix_with_run_continuity(tmp_path, monkeypatch):
    prompt = "Fix the implementation and verify it.\n\n" + MERRYPAK_1114.split("\n\n", 1)[1]
    result = _dispatch(tmp_path, monkeypatch, prompt)
    assert result["status"] == "blocked"
    assert result["error"] == "Required verification has not passed. Inspect the recorded checks and rerun them."
