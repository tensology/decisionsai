from distr.core.workflow.risk_and_audit import enforce_validation_requirements


def _base_packet():
    return {
        "status": "success",
        "tests_and_checks": {"tests_run": ["unit"]},
        "next_actions": {"recommended": []},
        "audit": {"audits_run": [], "final_verdict": "pass", "rationale": ""},
    }


def test_enforce_validation_requirements_downgrades_high_risk_without_checks():
    status, packet, missing = enforce_validation_requirements(
        packet=_base_packet(),
        run_status="completed",
        risk_profile={"level": "high", "signals": ["auth"]},
    )
    assert status == "failed"
    assert missing == ["lint", "typecheck", "build", "tests"]
    assert packet["audit"]["final_verdict"] == "needs_changes"
    assert packet["status"] == "partial_success"


def test_enforce_validation_requirements_keeps_status_when_checks_present():
    packet = _base_packet()
    packet["tests_and_checks"]["tests_run"] = ["lint", "typecheck", "build", "tests"]
    status, updated, missing = enforce_validation_requirements(
        packet=packet,
        run_status="completed",
        risk_profile={"level": "high", "signals": ["auth"]},
    )
    assert status == "completed"
    assert missing == []
    assert updated["audit"]["final_verdict"] == "pass"
