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


def test_enforce_validation_requirements_uses_ui_gate_for_high_risk_product_work():
    packet = _base_packet()
    packet["tests_and_checks"]["tests_run"] = []
    packet["artifacts"] = {
        "screenshots": [],
        "ui_quality": {
            "before_screenshot": "/tmp/before.png",
            "after_screenshot": "/tmp/after.png",
            "flow_summary": "Queue ticket, run workflow, see loop go red to green.",
            "happy_path_steps": ["queue ticket", "start run", "watch green exit"],
            "click_count": 3,
            "layout_hierarchy_notes": "Queue, loop, runs, and activity states remain distinct.",
        },
    }
    packet["execution"] = {
        "action_trace": [],
        "validation_snapshots": [
            {
                "validation_type": "ui_quality",
                "verdict": "pass",
                "observed": "UI evidence present.",
                "standards_context": "[VISUAL TASTE MEMORY]\n- approved: Dense operational workflow panels.",
            }
        ],
    }

    status, updated, missing = enforce_validation_requirements(
        packet=packet,
        run_status="completed",
        risk_profile={"level": "high", "signals": ["ui", "flow"], "risk_type": "product_conversion"},
    )

    assert status == "completed"
    assert missing == []
    assert updated["audit"]["final_verdict"] == "pass"


def test_enforce_validation_requirements_blocks_ui_completion_without_screenshot_flow():
    packet = _base_packet()
    packet["artifacts"] = {"screenshots": []}
    packet["execution"] = {"action_trace": [], "validation_snapshots": []}

    status, updated, missing = enforce_validation_requirements(
        packet=packet,
        run_status="completed",
        risk_profile={"level": "medium", "signals": ["ui", "flow"]},
    )

    assert status == "failed"
    assert "ui_after_screenshot" in missing
    assert "ui_flow_summary" in missing
    assert updated["audit"]["final_verdict"] == "needs_changes"
    assert updated["status"] == "partial_success"


def test_enforce_validation_requirements_allows_ui_completion_with_screenshot_and_flow():
    packet = _base_packet()
    packet["artifacts"] = {
        "screenshots": ["/tmp/after.png"],
        "ui_quality": {
            "layout_hierarchy_notes": "Primary save action remains visually dominant.",
        },
    }
    packet["execution"] = {
        "action_trace": [{"action_type": "click", "description": "Save"}],
        "validation_snapshots": [
            {
                "validation_type": "ui_quality",
                "verdict": "pass",
                "observed": "Flow summary: open settings, save.",
                "standards_context": "[VISUAL TASTE MEMORY]\n- approved: Compact operational controls.",
            }
        ],
    }

    status, updated, missing = enforce_validation_requirements(
        packet=packet,
        run_status="completed",
        risk_profile={"level": "medium", "signals": ["ui", "flow"]},
    )

    assert status == "completed"
    assert missing == []
    assert updated["audit"]["final_verdict"] == "pass"


def test_enforce_validation_requirements_uses_packet_ui_quality_artifacts():
    packet = _base_packet()
    packet["artifacts"] = {
        "screenshots": [],
        "ui_quality": {
            "before_screenshot": "/tmp/before.png",
            "after_screenshot": "/tmp/after.png",
            "flow_summary": "Open settings, change theme, save.",
            "happy_path_steps": ["open settings", "save"],
            "click_count": 2,
            "layout_hierarchy_notes": "Primary save action remains visually dominant.",
        },
    }
    packet["execution"] = {
        "action_trace": [],
        "validation_snapshots": [
            {
                "validation_type": "ui_quality",
                "verdict": "pass",
                "observed": "UI evidence present.",
                "standards_context": "[VISUAL TASTE MEMORY]\n- approved: compact controls.",
            }
        ],
    }

    status, updated, missing = enforce_validation_requirements(
        packet=packet,
        run_status="completed",
        risk_profile={"level": "low", "risk_type": "product_conversion"},
    )

    assert status == "completed"
    assert missing == []
    assert updated["audit"]["final_verdict"] == "pass"


def test_enforce_validation_requirements_blocks_ui_completion_without_passing_ui_validation():
    packet = _base_packet()
    packet["artifacts"] = {"screenshots": ["/tmp/after.png"]}
    packet["execution"] = {
        "action_trace": [{"action_type": "click", "description": "Save"}],
        "validation_snapshots": [
            {
                "validation_type": "text_match",
                "verdict": "pass",
                "observed": "Flow summary: open settings, save.",
            }
        ],
    }

    status, updated, missing = enforce_validation_requirements(
        packet=packet,
        run_status="completed",
        risk_profile={"level": "medium", "signals": ["ui", "flow"]},
    )

    assert status == "failed"
    assert "ui_quality_validation" in missing
    assert updated["audit"]["final_verdict"] == "needs_changes"


def test_enforce_validation_requirements_blocks_ui_completion_without_visual_taste_context():
    packet = _base_packet()
    packet["artifacts"] = {"screenshots": ["/tmp/after.png"]}
    packet["execution"] = {
        "action_trace": [{"action_type": "click", "description": "Save"}],
        "validation_snapshots": [
            {
                "validation_type": "ui_quality",
                "verdict": "pass",
                "observed": "Flow summary: open settings, save.",
                "standards_context": "[UNIVERSAL WORKFLOW QUALITY STANDARDS]",
            }
        ],
    }

    status, updated, missing = enforce_validation_requirements(
        packet=packet,
        run_status="completed",
        risk_profile={"level": "medium", "signals": ["ui", "flow"]},
    )

    assert status == "failed"
    assert "ui_visual_taste_context" in missing
    assert updated["audit"]["final_verdict"] == "needs_changes"
