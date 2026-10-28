"""A step that did not clear must not be reported as completed."""

from __future__ import annotations

from distr.core.workflow_engine.agent_bridge import WorkflowAgentBridge

_BACKEND_RESULT = (
    "Project CLI backend: cursor\n"
    "Project: 2\n"
    "Status: completed\n\n"
    "I finished the turn."
)


def _report(status: str, *, success: bool) -> str:
    return WorkflowAgentBridge._generate_report(
        {
            "success": success,
            "cancelled": False,
            "steps_summary": [
                {
                    "title": "Implement the planned change",
                    "status": status,
                    "result": _BACKEND_RESULT,
                }
            ],
        }
    )


def test_a_step_that_did_not_clear_is_not_called_completed():
    report = _report("failed", success=False)

    assert "didn't clear" in report
    assert "stopped before this part was finished" in report
    # Must not claim the work was finished in any form.
    assert "finished this part of the work" not in report


def test_a_passing_step_reads_as_finished_in_plain_words():
    report = _report("passed", success=True)

    assert "finished this part of the work" in report
    assert "didn't clear" not in report


def test_a_cancelled_run_says_it_was_stopped():
    report = WorkflowAgentBridge._generate_report(
        {"success": False, "cancelled": True, "steps_summary": []}
    )

    assert report.startswith("I stopped that run.")
