from __future__ import annotations


def test_even_high_complexity_stays_direct_by_default():
    from distr.core.workflow.execution_mode import choose_development_execution_mode

    decision = choose_development_execution_mode(
        "Refactor the integration, test every path, and validate the production migration.",
        assessment={"complexity": "high", "operational_state": "risk"},
    )

    assert decision["mode"] == "direct"
    assert "direct_by_default" in decision["signals"]


def test_explicit_workflow_language_does_not_auto_wrap():
    from distr.core.workflow.execution_mode import choose_development_execution_mode

    decision = choose_development_execution_mode(
        "Run an end-to-end workflow across the codebase with independent review.",
        assessment={"complexity": "high"},
    )

    assert decision["mode"] == "direct"


def test_short_request_is_direct():
    from distr.core.workflow.execution_mode import choose_development_execution_mode

    decision = choose_development_execution_mode(
        "hello",
        assessment={"complexity": "high", "operational_state": "risk"},
    )

    assert decision["mode"] == "direct"


def test_proposal_scope_also_stays_direct():
    from distr.core.workflow.execution_mode import choose_development_execution_mode

    decision = choose_development_execution_mode(
        "ignored wrapper text",
        assessment={
            "complexity": "high",
            "proposal_scope": {
                "title": "Migrate production",
                "description": "schema change",
                "tasks": ["a", "b", "c", "d"],
            },
        },
    )

    assert decision["mode"] == "direct"
    assert decision["policy"] == "proposal-scope-v1"


def test_infrastructure_skills_are_available_to_every_step():
    from distr.core.workflow.execution_mode import workflow_step_skills

    implementation = workflow_step_skills("implementation", ["python-testing"])
    review = workflow_step_skills("review", ["security-review"])

    assert implementation[:2] == ["decisions-harness-stack", "decisions-headroom"]
    assert "python-testing" in implementation
    assert "agent-watchdog" not in implementation
    assert "decisions-headroom" in review
    assert "agent-watchdog" in review
