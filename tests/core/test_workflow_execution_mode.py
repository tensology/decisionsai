from __future__ import annotations


def test_high_complexity_multi_phase_instruction_uses_workflow():
    from distr.core.workflow.execution_mode import choose_development_execution_mode

    decision = choose_development_execution_mode(
        "Refactor the integration, test every path, and validate the production migration.",
        assessment={"complexity": "high", "operational_state": "risk"},
    )

    assert decision["mode"] == "workflow"
    assert "multi_phase_scope" in decision["signals"]


def test_explicit_direct_override_wins():
    from distr.core.workflow.execution_mode import choose_development_execution_mode

    decision = choose_development_execution_mode(
        "Work directly and do not use a workflow, even though this is a production test.",
        assessment={"complexity": "high", "operational_state": "risk"},
    )

    assert decision["mode"] == "direct"
    assert decision["signals"] == ["explicit_direct_override"]


def test_infrastructure_skills_are_available_to_every_step():
    from distr.core.workflow.execution_mode import workflow_step_skills

    implementation = workflow_step_skills("implementation", ["python-testing"])
    review = workflow_step_skills("review", ["security-review"])

    assert implementation[:2] == ["decisions-harness-stack", "decisions-headroom"]
    assert "python-testing" in implementation
    assert "agent-watchdog" not in implementation
    assert "decisions-headroom" in review
    assert "agent-watchdog" in review
