"""Risk profiling and staged audit helpers for workflow result packets."""

from __future__ import annotations

from typing import Any, Dict, List, Tuple


HIGH_RISK_TERMS = (
    "auth",
    "authentication",
    "payment",
    "payments",
    "production config",
    "prod config",
    "migration",
    "migrations",
    "secret",
    "secrets",
    "token",
    "credential",
)

PRODUCT_RISK_TERMS = (
    "ui",
    "ux",
    "design system",
    "inconsistent",
    "consistency",
    "layout",
    "button",
    "navigation",
    "flow",
    "copy",
    "clarity",
    "onboarding",
    "conversion",
    "signup",
    "checkout",
    "pricing page",
    "landing page",
)


def validation_rules_for_risk(risk_level: str, signals: List[str]) -> List[str]:
    """Return baseline validation rules that must be checked before sign-off."""
    rules = [
        "Result packet fields are complete and internally consistent.",
        "No contradictory status/verdict across run, ticket, and audit notes.",
    ]
    if any(s in PRODUCT_RISK_TERMS for s in signals) or risk_level in ("high", "medium"):
        rules.extend(
            [
                "UI remains visually consistent with existing design patterns.",
                "Interaction flow is compact, clear, and easy to understand end-to-end.",
                "Buttons/controls add user value and do not introduce noisy or redundant actions.",
                "Copy/messaging is concise, consistent, and conversion-safe.",
            ]
        )
    if risk_level == "high":
        rules.extend(
            [
                "Deterministic checks required: lint, typecheck, build, tests.",
                "Regression and rollback risk is documented before marking done.",
            ]
        )
    return rules


def required_validation_checks(risk_level: str) -> List[str]:
    """Deterministic checks required before pass verdict."""
    if risk_level != "high":
        return []
    return ["lint", "typecheck", "build", "tests"]


def enforce_validation_requirements(
    *,
    packet: Dict[str, Any],
    run_status: str,
    risk_profile: Dict[str, Any],
) -> Tuple[str, Dict[str, Any], List[str]]:
    """Enforce required validation checks on a result packet.

    Returns: (possibly adjusted run_status, updated_packet, missing_checks)
    """
    updated = dict(packet or {})
    risk_level = (risk_profile or {}).get("level", "low")
    required = required_validation_checks(risk_level)
    tests_and_checks = dict(updated.get("tests_and_checks") or {})
    observed = [str(item).strip().lower() for item in (tests_and_checks.get("tests_run") or []) if str(item).strip()]
    missing = [name for name in required if name not in observed]

    audit = dict(updated.get("audit") or {})
    audits_run = list(audit.get("audits_run") or [])
    if missing:
        audits_run.append(
            {
                "gate": "V",
                "name": "required_validation_enforcement",
                "model": "rule-engine",
                "outcome": "needs_changes",
                "rationale": f"Missing required checks for high risk: {', '.join(missing)}",
            }
        )
        audit["final_verdict"] = "needs_changes"
        audit["rationale"] = f"High-risk validation missing required checks: {', '.join(missing)}"
        updated["status"] = "partial_success"
        next_actions = dict(updated.get("next_actions") or {})
        recommended = list(next_actions.get("recommended") or [])
        recommended.append(
            f"Run required validation checks before sign-off: {', '.join(missing)}."
        )
        next_actions["recommended"] = recommended
        updated["next_actions"] = next_actions
        adjusted_status = "failed" if (run_status or "completed") == "completed" else run_status
    else:
        adjusted_status = run_status

    audit["audits_run"] = audits_run
    updated["audit"] = audit
    return adjusted_status, updated, missing


def infer_risk_profile(text: str) -> Dict[str, Any]:
    """Classify a ticket/workflow request into low/medium/high risk."""
    lower = (text or "").lower()
    system_matched = [term for term in HIGH_RISK_TERMS if term in lower]
    product_matched = [term for term in PRODUCT_RISK_TERMS if term in lower]
    if system_matched:
        signals = system_matched + product_matched
        return {"level": "high", "signals": signals, "risk_type": "system_or_security"}
    if product_matched:
        return {"level": "high", "signals": product_matched, "risk_type": "product_conversion"}
    if any(word in lower for word in ("db", "database", "api", "backend", "deploy")):
        return {"level": "medium", "signals": ["broad_system_touch"], "risk_type": "technical_scope"}
    return {"level": "low", "signals": [], "risk_type": "standard"}


def build_audit_gates(
    *,
    status: str,
    risk_level: str,
    tests_passed: bool = True,
) -> List[Dict[str, Any]]:
    """Emit staged audit-gate outcomes for packet.audit.audits_run."""
    normalized_status = (status or "").strip().lower()
    gates: List[Dict[str, Any]] = [
        {
            "gate": "A",
            "name": "fast_schema_and_safety",
            "model": "cheap-default",
            "outcome": "pass" if tests_passed else "needs_changes",
            "rationale": "Schema/basic safety gate based on run health.",
        },
        {
            "gate": "B",
            "name": "deeper_reasoning",
            "model": "standard-reasoning",
            "outcome": "pass" if normalized_status == "completed" else "needs_changes",
            "rationale": "Step outcomes and terminal run status.",
        },
    ]
    if risk_level == "high":
        gates.append(
            {
                "gate": "C",
                "name": "strict_high_risk_review",
                "model": "strict-review",
                "outcome": "pass" if normalized_status == "completed" else "needs_changes",
                "rationale": "High-risk ticket requires deeper mandatory review.",
            }
        )
    # Conditional judge gate D
    needs_judge = (risk_level == "high" and normalized_status != "completed") or any(
        g.get("outcome") == "needs_changes" for g in gates
    )
    if needs_judge:
        gates.append(
            {
                "gate": "D",
                "name": "escalation_judge",
                "model": "premium-judge",
                "outcome": "escalate" if normalized_status != "completed" else "pass",
                "rationale": "Escalation due to risk and/or gate disagreement.",
            }
        )
    return gates
