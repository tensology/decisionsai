from __future__ import annotations

import json

from distr.core.completion_protocol import (
    classify_completion_gap,
    parse_completion_manifest,
    record_completion_correction,
    validate_completion_manifest,
)
from distr.core.project_cli_backends.registry import _workflow_report_error


def _complete_manifest() -> dict:
    return {
        "objective": "Translate the complete customer buying journey into Japanese.",
        "inventory": [
            {
                "id": "product-catalog",
                "status": "verified",
                "evidence": ["Playwright catalog journey passed in ja locale"],
                "reason": "",
            },
            {
                "id": "brand-name",
                "status": "excluded",
                "evidence": [],
                "reason": "Brand remains unchanged by user approval.",
            },
        ],
        "negative_space_checks": [
            {
                "check": "Search rendered routes and locale files for English fallback text",
                "passed": True,
                "evidence": "locale audit reported zero unapproved English strings",
            }
        ],
        "journeys": [
            {
                "journey": "Browse category, open product, purchase, receive confirmation",
                "verified": True,
                "evidence": "Japanese Playwright checkout test passed",
            }
        ],
        "exclusions": [
            {"item": "brand-name", "approved": True, "reason": "User approved unchanged brand."}
        ],
        "blockers": [],
        "independent_audit": {
            "passed": True,
            "reviewed_against_original": True,
            "findings": [],
        },
        "verdict": "complete",
    }


def test_complete_manifest_requires_inventory_negative_space_journey_and_independent_audit():
    result = validate_completion_manifest(_complete_manifest())
    assert result.valid is True
    assert result.complete is True


def test_complete_manifest_rejects_unverified_surface_and_unsupported_complete_claim():
    manifest = _complete_manifest()
    manifest["inventory"][0] = {
        "id": "product-catalog", "status": "blocked", "evidence": [], "reason": "API unavailable"
    }
    manifest["blockers"] = ["Product API unavailable"]
    result = validate_completion_manifest(manifest)
    assert result.complete is False
    assert "complete verdict conflicts" in "; ".join(result.errors)


def test_parser_requires_one_line_json_after_exact_label():
    report = "completion_manifest: " + json.dumps(_complete_manifest(), separators=(",", ":"))
    assert parse_completion_manifest(report).complete is True
    assert parse_completion_manifest("Completion manifest is attached elsewhere.").complete is False


def test_workflow_report_rejects_shallow_manifest_even_when_named_fields_exist():
    output = "\n".join([
        "completion_manifest: {}",
        "Status: completed",
        "Summary: Everything is done.",
        "Files changed: app.py",
        "Commands run: pytest",
        "Blockers: none",
    ])
    error = _workflow_report_error(output, "Pi", expected_outputs=["completion_manifest"])
    assert "invalid completion manifest" in error


def test_workflow_report_accepts_valid_complete_manifest():
    output = "\n".join([
        "completion_manifest: " + json.dumps(_complete_manifest(), separators=(",", ":")),
        "Status: completed",
        "Summary: Complete inventory verified.",
        "Files changed: locale files",
        "Commands run: locale audit and Playwright",
        "Blockers: none",
    ])
    assert _workflow_report_error(output, "Pi", expected_outputs=["completion_manifest"]) == ""


def test_completion_feedback_is_classified_and_promoted_only_after_repetition(monkeypatch):
    recorded = {}
    monkeypatch.setattr(
        "distr.core.orchestrator.record_learning_signal",
        lambda **kwargs: recorded.setdefault("rule", kwargs) or 9,
    )
    monkeypatch.setattr(
        "distr.core.mempalace.wiring.dual_write_learning",
        lambda **kwargs: recorded.setdefault("memory", kwargs) or {"ok": True},
    )
    feedback = "You missed products, categories, placeholders, and a whole section."
    assert classify_completion_gap(feedback) == "omitted_surface"
    result = record_completion_correction(feedback, project_id=17)
    assert result["root_cause"] == "omitted_surface"
    assert recorded["rule"]["enabled"] is False
    assert recorded["rule"]["promote_after"] == 2
    assert recorded["memory"]["learning_type"] == "completion_correction"


def test_universal_workflow_context_requires_completion_protocol():
    from distr.core.workflow.standards_memory import build_standards_context

    context = build_standards_context()
    assert "global completion-protocol" in context
    assert "coverage ledger" in context
    assert "completion_manifest" in context
