"""Machine-checkable completion manifests for non-trivial workflow results."""

from __future__ import annotations

from dataclasses import dataclass
import json
import re
from typing import Any


@dataclass(frozen=True)
class CompletionManifestResult:
    valid: bool
    complete: bool
    errors: tuple[str, ...]
    manifest: dict[str, Any]


def validate_completion_manifest(value: dict[str, Any] | None) -> CompletionManifestResult:
    manifest = dict(value or {})
    errors: list[str] = []
    if not str(manifest.get("objective") or "").strip():
        errors.append("objective is required")
    inventory = manifest.get("inventory")
    if not isinstance(inventory, list) or not inventory:
        errors.append("inventory must contain at least one affected surface")
        inventory = []
    exclusions = manifest.get("exclusions") if isinstance(manifest.get("exclusions"), list) else []
    approved_exclusions = {
        str(item.get("item") or "").strip()
        for item in exclusions
        if isinstance(item, dict)
        and bool(item.get("approved"))
        and str(item.get("reason") or "").strip()
    }
    for index, item in enumerate(inventory):
        if not isinstance(item, dict):
            errors.append(f"inventory[{index}] must be an object")
            continue
        item_id = str(item.get("id") or "").strip()
        status = str(item.get("status") or "").strip().lower()
        evidence = item.get("evidence") if isinstance(item.get("evidence"), list) else []
        reason = str(item.get("reason") or "").strip()
        if not item_id:
            errors.append(f"inventory[{index}] id is required")
        if status not in {"verified", "excluded", "blocked"}:
            errors.append(f"inventory[{index}] has invalid status")
        elif status == "verified" and not any(str(entry).strip() for entry in evidence):
            errors.append(f"inventory item {item_id or index} lacks verification evidence")
        elif status == "excluded" and (not reason or item_id not in approved_exclusions):
            errors.append(f"inventory item {item_id or index} lacks an approved exclusion")
    negative_checks = manifest.get("negative_space_checks")
    if not isinstance(negative_checks, list) or not negative_checks:
        errors.append("negative_space_checks must contain at least one omission check")
        negative_checks = []
    for index, check in enumerate(negative_checks):
        if not isinstance(check, dict) or not bool(check.get("passed")) or not str(check.get("evidence") or "").strip():
            errors.append(f"negative_space_checks[{index}] must pass with evidence")
    journeys = manifest.get("journeys")
    if not isinstance(journeys, list) or not journeys:
        errors.append("journeys must contain at least one end-to-end flow")
        journeys = []
    for index, journey in enumerate(journeys):
        if not isinstance(journey, dict) or not bool(journey.get("verified")) or not str(journey.get("evidence") or "").strip():
            errors.append(f"journeys[{index}] must be verified with evidence")
    for index, exclusion in enumerate(exclusions):
        if not isinstance(exclusion, dict) or not bool(exclusion.get("approved")) or not str(exclusion.get("reason") or "").strip():
            errors.append(f"exclusions[{index}] requires approval and a reason")
    blockers = manifest.get("blockers") if isinstance(manifest.get("blockers"), list) else []
    audit = manifest.get("independent_audit") if isinstance(manifest.get("independent_audit"), dict) else {}
    audit_findings = audit.get("findings") if isinstance(audit.get("findings"), list) else []
    requested_complete = str(manifest.get("verdict") or "").strip().lower() == "complete"
    completion_blocked = bool(
        blockers
        or any(isinstance(item, dict) and str(item.get("status") or "").lower() == "blocked" for item in inventory)
        or not bool(audit.get("passed"))
        or not bool(audit.get("reviewed_against_original"))
        or audit_findings
    )
    if requested_complete and completion_blocked:
        errors.append("complete verdict conflicts with blockers or an incomplete independent audit")
    if str(manifest.get("verdict") or "").strip().lower() not in {"complete", "incomplete"}:
        errors.append("verdict must be complete or incomplete")
    return CompletionManifestResult(
        valid=not errors,
        complete=bool(requested_complete and not errors and not completion_blocked),
        errors=tuple(errors),
        manifest=manifest,
    )


def parse_completion_manifest(report: str) -> CompletionManifestResult:
    match = re.search(r"(?im)^\s*(?:[-*]\s*)?completion_manifest\s*:\s*(\{.*\})\s*$", str(report or ""))
    if not match:
        return CompletionManifestResult(False, False, ("completion_manifest must be one-line JSON",), {})
    try:
        payload = json.loads(match.group(1))
    except json.JSONDecodeError as exc:
        return CompletionManifestResult(False, False, (f"completion_manifest is invalid JSON: {exc.msg}",), {})
    if not isinstance(payload, dict):
        return CompletionManifestResult(False, False, ("completion_manifest must be a JSON object",), {})
    return validate_completion_manifest(payload)


def classify_completion_gap(feedback: str) -> str:
    """Classify recurring completion feedback without inventing a preference."""
    text = " ".join(str(feedback or "").lower().split())
    patterns = (
        ("unsupported_completion_claim", ("claimed complete", "said it was done", "not completed", "unfinished", "half-finished")),
        ("omitted_surface", ("missed", "forgot", "omitted", "whole section", "products", "categories", "placeholders")),
        ("missing_state", ("empty state", "loading state", "error state", "dark mode", "mobile state")),
        ("missing_data_source", ("database content", "cms content", "email template", "metadata", "fallback string")),
        ("broken_continuity", ("no continuity", "inconsistent across", "only some", "partially translated", "partial implementation")),
        ("stale_evidence", ("stale screenshot", "old screenshot", "not rerun", "stale evidence")),
        ("scope_drift", ("unrelated changes", "scope creep", "made up", "invented feature")),
    )
    for label, markers in patterns:
        if any(marker in text for marker in markers):
            return label
    return ""


def record_completion_correction(
    feedback: str,
    *,
    project_id: int | None = None,
    settings: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Record a classified omission centrally and promote only after repetition."""
    root_cause = classify_completion_gap(feedback)
    if not root_cause:
        return None
    clean = " ".join(str(feedback or "").split())[:900]
    from distr.core.mempalace.wiring import dual_write_learning
    from distr.core.orchestrator import record_learning_signal

    payload = {"root_cause": root_cause, "feedback": clean}
    rule_id = record_learning_signal(
        scope="project" if project_id is not None else "global",
        scope_id=project_id,
        rule_type="completion_correction",
        summary=f"Completion correction [{root_cause}]: {clean}",
        payload=payload,
        enabled=False,
        promote_after=2,
    )
    memory = dual_write_learning(
        insight=json.dumps(payload, sort_keys=True),
        key=f"completion:{root_cause}",
        learning_type="completion_correction",
        settings=settings,
    )
    return {"root_cause": root_cause, "rule_id": rule_id, "mempalace": memory}
