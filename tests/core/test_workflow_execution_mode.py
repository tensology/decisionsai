from __future__ import annotations


def test_large_multi_part_request_starts_a_workflow():
    from distr.core.workflow.execution_mode import choose_development_execution_mode

    decision = choose_development_execution_mode(
        "Please implement and verify an end-to-end account area for this product: "
        "a sign-in page, a profile page, a billing history page, and a password reset flow. "
        "Add the pages to the plan, create tickets for each part, implement the backend APIs "
        "and the frontend, then review and report when the whole collection of tickets is done."
    )

    assert decision["mode"] == "workflow"
    assert decision["workflow_slug"] == "development-implement-verify"
    assert "Started workflow Development because" in decision["visible_reason"]
    assert "direct_by_default" not in decision["signals"]


def test_short_typo_fix_is_handled_directly():
    from distr.core.workflow.execution_mode import choose_development_execution_mode

    decision = choose_development_execution_mode("Fix a typo in the button text.")

    assert decision["mode"] == "direct"
    assert "Handled directly because" in decision["visible_reason"]


def test_short_heading_change_is_handled_directly():
    from distr.core.workflow.execution_mode import choose_development_execution_mode

    decision = choose_development_execution_mode(
        "Please rename the welcome heading on the home page to Hello."
    )

    assert decision["mode"] == "direct"
    assert "Handled directly because" in decision["visible_reason"]
    assert "one-step" in decision["visible_reason"]


def test_labelled_heading_change_is_still_direct():
    from distr.core.workflow.execution_mode import choose_development_execution_mode

    decision = choose_development_execution_mode(
        "E2E UX test 2 (safe to delete): Please rename the welcome heading on the home page to Hello."
    )

    assert decision["mode"] == "direct"
    assert "Handled directly because" in decision["visible_reason"]


def test_project_word_in_a_question_does_not_start_a_workflow():
    from distr.core.workflow.execution_mode import allocate_development_request

    decision = allocate_development_request(
        "What is the status of this project?",
        project_hint="E2E UX test 2 (safe to delete)",
        project_id=99,
    )

    assert decision["mode"] == "direct"
    assert "Handled directly because" in decision["visible_reason"]
    assert decision["workflow_slug"] is None


def test_design_first_large_request_is_workflow():
    from distr.core.workflow.execution_mode import choose_development_execution_mode

    decision = choose_development_execution_mode(
        "Please design, implement and verify an end-to-end account area for this product."
    )

    assert decision["mode"] == "workflow"
    assert decision["workflow_slug"] in {
        "development-implement-verify",
        "new-work-design-to-plan",
    }
    assert "Started workflow" in decision["visible_reason"]


def test_operator_can_override_a_workflow_to_go_direct():
    from distr.core.workflow.execution_mode import choose_development_execution_mode

    decision = choose_development_execution_mode(
        "Please implement and verify an end-to-end account area for this product.",
        assessment={"execution_mode_override": "direct"},
    )

    assert decision["mode"] == "direct"
    assert "you chose to send this as one agent turn" in decision["visible_reason"]


def test_incoming_proposal_handoff_stays_direct_until_the_development_thread():
    from distr.core.workflow.execution_mode import choose_development_execution_mode

    decision = choose_development_execution_mode(
        "ignored wrapper text",
        assessment={
            "complexity": "high",
            "proposal_scope": {
                "title": "Build an account area",
                "description": "Implement and verify sign-in, billing and password reset.",
                "tasks": [
                    "Design the sign-in page",
                    "Implement billing history",
                    "Add password reset",
                    "Review and report",
                ],
            },
        },
    )

    assert decision["mode"] == "direct"
    assert decision["policy"] == "proposal-scope-v1"


def test_short_request_is_direct():
    from distr.core.workflow.execution_mode import choose_development_execution_mode

    decision = choose_development_execution_mode(
        "hello",
        assessment={"complexity": "high", "operational_state": "risk"},
    )

    assert decision["mode"] == "direct"


def test_infrastructure_skills_are_available_to_every_step():
    from distr.core.workflow.execution_mode import workflow_step_skills

    implementation = workflow_step_skills("implementation", ["python-testing"])
    review = workflow_step_skills("review", ["security-review"])

    assert implementation[:2] == ["decisions-harness-stack", "decisions-headroom"]
    assert "python-testing" in implementation
    assert "agent-watchdog" not in implementation
    assert "decisions-headroom" in review
    assert "agent-watchdog" in review
