from distr.core.workflow.risk_and_audit import (
    build_audit_gates,
    infer_risk_profile,
    validation_rules_for_risk,
)


def test_infer_risk_profile_high_for_sensitive_keywords():
    profile = infer_risk_profile("update auth token handling and payments flow")
    assert profile["level"] == "high"
    assert "auth" in profile["signals"]


def test_build_audit_gates_includes_escalation_for_high_risk_failure():
    gates = build_audit_gates(status="failed", risk_level="high", tests_passed=False)
    names = [g["gate"] for g in gates]
    assert "A" in names
    assert "B" in names
    assert "C" in names
    assert "D" in names


def test_infer_risk_profile_flags_product_conversion_risk():
    profile = infer_risk_profile("ui flow is inconsistent and buttons feel low value")
    assert profile["level"] == "high"
    assert profile["risk_type"] == "product_conversion"


def test_validation_rules_include_ui_quality_for_product_signals():
    rules = validation_rules_for_risk("high", ["ui", "flow"])
    joined = " ".join(rules).lower()
    assert "visually consistent" in joined
    assert "interaction flow" in joined


def test_backend_copy_and_authoritative_language_do_not_invent_ui_or_auth_risk():
    profile = infer_risk_profile(
        "Copy-first backend foundation. The recovery note is authoritative. Preserve the frontend."
    )

    assert "auth" not in profile["signals"]
    assert "copy" not in profile["signals"]
    rules = validation_rules_for_risk(profile["level"], profile["signals"])
    assert not any("UI remains" in rule for rule in rules)
