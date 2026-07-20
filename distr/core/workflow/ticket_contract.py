"""Deterministic ticket applicability rules shared across workflow execution.

The LLM may interpret a ticket, but explicit scope and non-goals are contracts.
This module prevents generic development gates from overriding declarations such
as ``No code changes`` or ``browser evidence is N/A``.
"""

from __future__ import annotations

import re
from typing import Any


_NO_CODE_PHRASES = (
    "no code changes",
    "research only",
    "research-only",
    "documentation only",
    "documentation-only",
    "do not edit code",
    "do not modify code",
)
_EXPLICIT_UI_EVIDENCE = (
    "browser evidence required",
    "screenshot required",
    "screenshots required",
    "screen recording required",
    "playwright required",
)
_COMPLETION_PHRASES = (
    "all acceptance criteria met",
    "all acceptance criteria are met",
    "all acceptance criteria verified",
    "all deliverables verified",
    "ticket is complete",
    "ticket has already been completed",
)
_UNRESOLVED_PHRASES = (
    "status: failed",
    "status: needs_input",
    "status: needs input",
    "unresolved blocker",
    "blockers: credential",
    "blockers: missing",
    "cannot complete",
)


def classify_ticket_execution(text: str) -> dict[str, Any]:
    """Return an execution profile derived from explicit ticket language."""
    lower = " ".join(str(text or "").lower().split())
    no_code = any(phrase in lower for phrase in _NO_CODE_PHRASES)
    research = no_code or any(
        phrase in lower
        for phrase in ("research and design direction", "research brief", "source-backed research")
    )
    ui_evidence_required = any(phrase in lower for phrase in _EXPLICIT_UI_EVIDENCE)
    copy_first_required = "copy-first" in lower or "must copy" in lower
    return {
        "kind": "research_documentation" if research and no_code else "implementation",
        "research_only": bool(research and no_code),
        "implementation_required": not no_code,
        "repository_checks_required": not no_code,
        "ui_evidence_required": bool(ui_evidence_required),
        "copy_first_required": bool(copy_first_required),
        "explicit_no_code": bool(no_code),
    }


def result_reports_completion(result: str) -> bool:
    """Recognize a structured, evidence-bearing completion report."""
    lower = str(result or "").lower()
    if any(phrase in lower for phrase in _UNRESOLVED_PHRASES):
        return False
    completed = "status: completed" in lower or any(
        phrase in lower for phrase in _COMPLETION_PHRASES
    )
    no_blockers = "blockers: none" in lower or "no blockers" in lower
    # Require a concrete artifact/path rather than accepting a bare completion claim.
    artifact = bool(
        re.search(r"(?:^|[\s`(])(?:docs?/)?[\w./-]+\.(?:md|txt|pdf|json|png|jpe?g|webp|mp4|mov)\b", lower)
    )
    return bool(completed and no_blockers and artifact)


def research_review_has_evidence(result: str) -> bool:
    """Return true when a no-code review proves its documentary deliverables."""
    lower = str(result or "").lower()
    if not result_reports_completion(result):
        return False
    acceptance = "acceptance criteria" in lower or "deliverables" in lower
    verification = any(
        token in lower
        for token in ("verified", "evidence", "source inventory", "citations", "citeable", "citable")
    )
    return bool(acceptance and verification)


def existing_work_satisfies_contract(ticket_text: str, result: str) -> bool:
    """Guard the already-complete shortcut with explicit evidence requirements."""
    profile = classify_ticket_execution(ticket_text)
    lower = str(result or "").lower()
    if not result_reports_completion(result):
        return False
    if profile.get("ui_evidence_required"):
        # A planning/context report cannot prove that a named media path exists
        # or that it depicts the required source. Keep the run on its normal
        # implementation/review path where the evidence gate can inspect files.
        return False
    if profile.get("copy_first_required") and not any(
        token in lower for token in ("rsync ", "cp -a", "copied from", "copy manifest")
    ):
        return False
    return True


def step_scope_overlay(step_name: str, ticket_text: str) -> str:
    """Explain how a generic development phase applies to an explicit ticket."""
    profile = classify_ticket_execution(ticket_text)
    if not profile["research_only"]:
        return ""
    name = str(step_name or "").lower()
    base = (
        "Ticket applicability: this is a research/documentation ticket with an explicit no-code contract. "
        "Do not modify application code. Repository lint/build/test and UI screenshots are not completion "
        "requirements unless the ticket explicitly requires them. Validate the named documentary artifacts "
        "and citations against the ticket acceptance criteria."
    )
    evidence_rule = ""
    if profile.get("ui_evidence_required"):
        evidence_rule = (
            " This ticket explicitly requires browser media evidence, so browser screenshots/recordings are not N/A. "
            "The run may pass only after the exact existing media artifact paths are reported and verified. "
            "Verification means inspecting the actual image contents with a vision-capable tool/model and stating what "
            "each artifact visibly proves against the ticket; file existence or dimensions alone are not visual evidence."
        )
    if any(word in name for word in ("understand", "context", "ingest")):
        return (
            base
            + evidence_rule
            + " Inspect the named documentation/evidence index and at most the single explicitly identified preservation surface; "
            "do not explore unrelated implementation components or old ticket/session history. Stop as soon as the missing evidence is identified."
        )
    if "implement" in name:
        return base + evidence_rule + " Produce only missing research, documentation, and evidence deliverables."
    if "plan" in name:
        return base + evidence_rule + " Plan the research and evidence deliverables, not an implementation diff."
    if any(word in name for word in ("review", "validate", "quality")):
        return (
            base
            + evidence_rule
            + " A documented N/A reason is sufficient only for checks that the ticket does not explicitly require."
        )
    if "correct" in name:
        return base + evidence_rule + " Correct only concrete documentary or evidence defects reported by review."
    if any(word in name for word in ("polish", "ship")):
        return base + evidence_rule + " Do not run a code-production polish pass; preserve scope and prepare the evidence handoff."
    return base + evidence_rule
