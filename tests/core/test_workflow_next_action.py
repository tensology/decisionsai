from distr.core.workflow.service import decide_workflow_next_action


def test_next_action_needs_human_input_for_worker_question():
    decision = decide_workflow_next_action(
        run_data={
            "waiting_kind": "needs_human_input",
            "worker_question": "Which dashboard density should I keep?",
        }
    )

    assert decision["action"] == "needs_human_input"
    assert "waiting" in decision["reason"].lower()


def test_next_action_requires_validation_for_high_risk_without_pass():
    decision = decide_workflow_next_action(
        run_data={"risk_profile": {"level": "high"}},
        validation={"verdict": "cannot_determine"},
    )

    assert decision["action"] == "validation_required"


def test_next_action_requires_correction_for_failed_validation():
    decision = decide_workflow_next_action(
        run_data={},
        validation={"verdict": "fail", "missing": ["after_screenshot"]},
    )

    assert decision["action"] == "correction_required"
    assert decision["missing"] == ["after_screenshot"]


def test_next_action_continues_when_no_blockers():
    decision = decide_workflow_next_action(
        run_data={"risk_profile": {"level": "low"}},
        validation={"verdict": "pass"},
        confidence=0.9,
    )

    assert decision["action"] == "continue"
