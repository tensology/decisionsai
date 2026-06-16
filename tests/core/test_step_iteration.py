from distr.core.workflow.step_iteration import (
    build_step_iteration_protocol,
    parse_harness_step_report,
    record_harness_step_report,
)


def test_build_step_iteration_protocol_includes_skills_and_validation():
    text = build_step_iteration_protocol(
        {
            "skills": ["tdd-workflow", "webapp-testing"],
            "validation_prompt": "Tests pass and UI flow is sane.",
            "failure_checklist": ["Skipped tests", "Random UI chrome added"],
        }
    )
    assert "Iteration protocol" in text
    assert "tdd-workflow" in text
    assert "Tests pass and UI flow is sane" in text
    assert "Skipped tests" in text


def test_parse_harness_step_report():
    report = """Status: completed
Summary: Updated session report layout
Tests run: npm test — pass
Drift check: none
Security: none
UI assessment: checked snackbar and loading state
Self-corrections: fixed clipped title
Files changed: frontend/src/Report.tsx
Blockers: none"""
    parsed = parse_harness_step_report(report)
    assert parsed["status"] == "completed"
    assert "session report" in parsed["summary"].lower()
    assert "npm test" in parsed["tests_run"]
    assert parsed["drift_check"] == "none"
    assert "snackbar" in parsed["ui_assessment"]


def test_record_harness_step_report_persists_on_run():
    from distr.core.db import get_session
    from distr.core.db.workflow import AutoWorkflow, AutoWorkflowRun

    with get_session() as db:
        wf = AutoWorkflow(name="Iteration test", workflow_type="manual", status="active")
        db.add(wf)
        db.flush()
        run = AutoWorkflowRun(workflow_id=wf.id, status="waiting", run_data="{}")
        db.add(run)
        db.commit()
        run_id = int(run.id)

    result = record_harness_step_report(
        run_id=run_id,
        step_id=None,
        report_text="Status: completed\nSummary: Fixed button spacing\nBlockers: none",
        source="test",
    )
    assert result["recorded"] is True

    with get_session() as db:
        row = db.query(AutoWorkflowRun).filter(AutoWorkflowRun.id == run_id).one()
        data = __import__("json").loads(row.run_data or "{}")
        assert data.get("latest_step_report", {}).get("fields", {}).get("summary") == "Fixed button spacing"
        improvements = data.get("result_packet", {}).get("step_improvements") or []
        assert improvements and "button spacing" in improvements[-1]["summary"]
