from distr.core.workflow.step_iteration import (
    build_step_iteration_protocol,
    load_step_handoff_meta,
    parse_harness_step_report,
    record_harness_step_report,
)
from distr.core.workflow.tools import tools_for_action


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


def test_build_step_iteration_protocol_includes_explicit_browser_tools():
    text = build_step_iteration_protocol(
        {
            "tools": ["browser_use", "playwright"],
            "skills": ["browser-qa"],
            "validation_prompt": "Browser evidence proves the changed flow works.",
        }
    )

    assert "Use these tools explicitly" in text
    assert "browser_use" in text
    assert "playwright" in text
    assert "Treat these as required step capabilities" in text


def test_load_step_handoff_meta_infers_browser_use_tool_from_action_type():
    from distr.core.db import get_session
    from distr.core.db.workflow import AutoWorkflow, AutoWorkflowStep

    with get_session() as db:
        wf = AutoWorkflow(name="Browser tool inference", workflow_type="manual", status="active")
        db.add(wf)
        db.flush()
        step = AutoWorkflowStep(
            workflow_id=wf.id,
            position=1,
            name="Use browser",
            action_type="browser_use",
            step_type="browser_use",
            config="{}",
        )
        db.add(step)
        db.commit()
        step_id = int(step.id)

    meta = load_step_handoff_meta(step_id)

    assert meta["action_type"] == "browser_use"
    assert meta["tools"] == ["browser_use"]


def test_workflow_tool_registry_maps_actions_to_specific_capabilities():
    assert tools_for_action("agent_instruction") == ["agent"]
    assert tools_for_action("execute_code") == ["python"]
    assert tools_for_action("run_command") == ["shell"]
    assert tools_for_action("http_request") == ["http"]
    assert tools_for_action("play_recording") == ["macro"]
    assert tools_for_action("send_to_project_cli") == ["cli"]
    assert tools_for_action("playwright") == ["playwright", "browser_use"]
    assert tools_for_action("browser_use") == ["browser_use"]
    assert tools_for_action("computer_use") == ["computer_use"]
    assert "other" not in {
        tool
        for action in [
            "agent_instruction",
            "execute_code",
            "run_command",
            "http_request",
            "play_recording",
            "send_to_project_cli",
            "playwright",
            "computer_use",
        ]
        for tool in tools_for_action(action)
    }


def test_load_step_handoff_meta_uses_specific_tool_fallbacks_not_other():
    from distr.core.db import get_session
    from distr.core.db.workflow import AutoWorkflow, AutoWorkflowStep

    with get_session() as db:
        wf = AutoWorkflow(name="Specific tool inference", workflow_type="manual", status="active")
        db.add(wf)
        db.flush()
        step = AutoWorkflowStep(
            workflow_id=wf.id,
            position=1,
            name="Run Python",
            action_type="execute_code",
            step_type="execute_code",
            config="{}",
        )
        db.add(step)
        db.commit()
        step_id = int(step.id)

    meta = load_step_handoff_meta(step_id)

    assert meta["tools"] == ["python"]


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
