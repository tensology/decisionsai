"""Outcome-scored Development eval pack (instruments I2 / drift needle).

Twenty lightweight fixtures spanning research, normal implementation, and
high-risk work. Scorers check outcomes, not preferred routes.
"""

from __future__ import annotations

from typing import Any, Callable

from distr.core.workflow.ticket_contract import (
    classify_ticket_execution,
    existing_work_satisfies_contract,
    result_reports_completion,
)


EvalCase = dict[str, Any]


BLUEPRINT_EVAL_PACK: tuple[EvalCase, ...] = (
    {"id": "R01", "kind": "research", "ticket": "Research the API and write docs/brief.md. No code changes.", "result": "Status: completed\nBlockers: none\nEvidence: docs/brief.md\nAcceptance criteria verified with citations.", "expect_pass": True},
    {"id": "R02", "kind": "research", "ticket": "Documentation only summary of auth flow. No code changes.", "result": "Status: needs_input\nBlockers: missing citations\nWrote notes.txt without sources.", "expect_pass": False},
    {"id": "R03", "kind": "research", "ticket": "Source-backed research brief. No code changes.", "result": "Status: failed\nBlockers: missing sources\nEvidence: docs/brief.md", "expect_pass": False},
    {"id": "R04", "kind": "research", "ticket": "Research-only competitive notes. No code changes.", "result": "Status: completed\nBlockers: none\nEvidence: docs/competitors.md\nDeliverables verified.", "expect_pass": True},
    {"id": "R05", "kind": "research", "ticket": "Research and design direction. No code changes.", "result": "Status: completed\nNo blockers\nEvidence: docs/direction.md\nAcceptance criteria met with source inventory.", "expect_pass": True},
    {"id": "N01", "kind": "normal", "ticket": "Implement checkout button fix and run tests.", "result": "Status: completed\nBlockers: none\nChanged frontend/Checkout.tsx\nTests passed.", "expect_pass": True},
    {"id": "N02", "kind": "normal", "ticket": "Rename the settings label and update snapshots.", "result": "Status: completed\nBlockers: none\nEvidence: frontend/Settings.tsx", "expect_pass": True},
    {"id": "N03", "kind": "normal", "ticket": "Add empty-state copy on the tickets list.", "result": "Status: needs_input\nBlockers: missing design token", "expect_pass": False},
    {"id": "N04", "kind": "normal", "ticket": "Fix flaky unit test in billing helpers.", "result": "Status: completed\nBlockers: none\nEvidence: backend/tests/test_billing.py", "expect_pass": True},
    {"id": "N05", "kind": "normal", "ticket": "Improve loading spinner accessibility text.", "result": "Status: completed\nBlockers: none\nEvidence: frontend/Spinner.tsx", "expect_pass": True},
    {"id": "N06", "kind": "normal", "ticket": "Document the webhook retry policy in docs/webhooks.md.", "result": "Status: completed\nBlockers: none\nEvidence: docs/webhooks.md\nAcceptance criteria verified.", "expect_pass": True},
    {"id": "N07", "kind": "normal", "ticket": "Refactor unused CSS without behavior change.", "result": "Status: failed\nBlockers: visual regression\nEvidence: screenshots/before.png", "expect_pass": False},
    {"id": "H01", "kind": "high_risk", "ticket": "Add OAuth login. Browser screenshots required.", "result": "Status: completed\nBlockers: none\nEvidence: docs/oauth.md", "expect_pass": False},
    {"id": "H02", "kind": "high_risk", "ticket": "Add OAuth login. Browser screenshots required.", "result": "Status: completed\nBlockers: none\nEvidence: artifacts/login.png\nAcceptance criteria verified.", "expect_pass": True},
    {"id": "H03", "kind": "high_risk", "ticket": "Migrate payments schema. Do not destroy production data.", "result": "Status: completed\nBlockers: none\nEvidence: migrations/0001_payments.sql\nRollback notes included.", "expect_pass": True},
    {"id": "H04", "kind": "high_risk", "ticket": "Send password-reset emails to affected users.", "result": "Status: needs_input\nBlockers: irreversible external send requires approval", "expect_pass": False},
    {"id": "H05", "kind": "high_risk", "ticket": "Rotate API keys for billing. Authentication critical.", "result": "Status: completed\nBlockers: none\nEvidence: runbooks/key-rotation.md\nAcceptance criteria verified.", "expect_pass": True},
    {"id": "H06", "kind": "high_risk", "ticket": "Delete abandoned user accounts older than 5 years.", "result": "Status: failed\nBlockers: irreversible delete blocked pending approval", "expect_pass": False},
    {"id": "H07", "kind": "high_risk", "ticket": "Enable Stripe capture for checkout. Payments in scope.", "result": "Status: completed\nBlockers: none\nEvidence: backend/payments/capture.py\nTests passed.", "expect_pass": True},
    {"id": "H08", "kind": "high_risk", "ticket": "Change role permissions for admin impersonation.", "result": "Status: completed\nBlockers: none\nEvidence: backend/auth/roles.py\nAcceptance criteria verified.", "expect_pass": True},
)


def score_eval_case(case: EvalCase) -> dict[str, Any]:
    """Score one fixture on outcome evidence, not preferred route."""
    ticket = str(case.get("ticket") or "")
    result = str(case.get("result") or "")
    kind = str(case.get("kind") or "normal")
    profile = classify_ticket_execution(ticket)
    if kind == "research" or profile.get("research_only") or profile.get("explicit_no_code"):
        passed = existing_work_satisfies_contract(ticket, result)
    elif profile.get("ui_evidence_required"):
        lower = result.lower()
        passed = result_reports_completion(result) and any(
            token in lower for token in (".png", ".jpg", ".jpeg", ".webp", "screenshot")
        )
    else:
        passed = result_reports_completion(result) or (
            "status: completed" in result.lower() and "blockers: none" in result.lower()
        )
    expected = bool(case.get("expect_pass"))
    return {
        "id": case.get("id"),
        "kind": kind,
        "passed": passed,
        "expect_pass": expected,
        "ok": passed == expected,
        "profile": profile,
    }


def run_blueprint_eval_pack(
    cases: tuple[EvalCase, ...] | list[EvalCase] | None = None,
) -> dict[str, Any]:
    """Run the standing outcome pack and return aggregate drift-friendly stats."""
    pack = tuple(cases or BLUEPRINT_EVAL_PACK)
    results = [score_eval_case(case) for case in pack]
    ok = sum(1 for item in results if item["ok"])
    by_kind: dict[str, dict[str, int]] = {}
    for item in results:
        bucket = by_kind.setdefault(str(item["kind"]), {"ok": 0, "total": 0})
        bucket["total"] += 1
        if item["ok"]:
            bucket["ok"] += 1
    return {
        "total": len(results),
        "ok": ok,
        "failed": len(results) - ok,
        "success_rate": round(ok / len(results), 4) if results else 0.0,
        "by_kind": by_kind,
        "results": results,
    }
