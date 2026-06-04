"""Adaptive workflow quality standards.

This module keeps the non-negotiable workflow quality bar close to execution.
Workflow-specific Agent Context rows can still tune the rules, but these
defaults stop ticket runs from treating a code change as completion.
"""

from __future__ import annotations

from typing import Optional

from distr.core.db import get_session
from distr.core.db.workflow import AutoWorkflow, AutoWorkflowVariable


STANDARDS_CONTEXT_TITLE = "Universal Quality Standards"
ADAPTIVE_CONTEXT_TITLE = "Adaptive Quality Memory"


UNIVERSAL_WORKFLOW_STANDARDS = """[UNIVERSAL WORKFLOW QUALITY STANDARDS]
- Do not mark a ticket complete just because code changed; completion requires evidence that the requested outcome works.
- Match validation to the work type: UI and user-facing flow changes need browser or Playwright validation, backend/API changes need relevant tests or integration checks, and data changes need integrity/rollback consideration.
- Prefer meaningful unit, regression, integration, or browser tests when they reduce real risk; do not invent filler tests or documentation.
- Documentation is only useful when it explains a real decision, behavior, integration, or operational change.
- Carry user feedback forward as operating standards when it points to recurring quality expectations, UI preferences, testing gaps, or unacceptable shortcuts.
- Distinguish universal standards from ticket-specific instructions; apply ticket-specific feedback only where it is relevant.
- Treat quality as a process: understand the ticket, plan the route, implement carefully, test meaningfully, validate visibly, and report evidence before completion.
- Avoid shallow work, rushed assumptions, duplicate UI representations, hidden state, and half-finished implementation.
"""


_FEEDBACK_SIGNALS = (
    "test",
    "validate",
    "validation",
    "playwright",
    "browser",
    "complete",
    "properly",
    "unfinished",
    "half",
    "ui",
    "ux",
    "duplicate",
    "duplicating",
    "unnecessary",
    "overdoing",
    "hallucinat",
    "common sense",
    "quality",
    "evidence",
    "audit",
    "documentation",
    "docs",
)


def build_standards_context(context_rules: Optional[str] = None, board_id: int | None = None) -> str:
    """Append universal workflow standards and board learned rules to context."""
    existing = (context_rules or "").strip()
    standards = UNIVERSAL_WORKFLOW_STANDARDS.strip()
    if not existing:
        base = standards
    elif "[UNIVERSAL WORKFLOW QUALITY STANDARDS]" in existing:
        base = existing
    else:
        base = existing + "\n\n" + standards

    if board_id:
        try:
            from distr.core.hermes import build_learned_rules_context, build_visual_taste_context

            additions = []
            learned = build_learned_rules_context(int(board_id))
            if learned and "[BOARD LEARNED RULES]" not in base:
                additions.append(learned)
            taste = build_visual_taste_context(board_id=int(board_id))
            if taste and "[VISUAL TASTE MEMORY]" not in base:
                additions.append(taste)
            if additions:
                return base + "\n\n" + "\n\n".join(additions)
        except Exception:
            pass
    return base


def should_capture_feedback(feedback: str) -> bool:
    """Return True when feedback is likely to describe a reusable standard."""
    text = (feedback or "").strip()
    if len(text) < 20:
        return False
    lowered = text.lower()
    return any(signal in lowered for signal in _FEEDBACK_SIGNALS)


def feedback_to_standard(feedback: str) -> str:
    """Convert raw run feedback into a conservative reusable rule."""
    text = " ".join((feedback or "").strip().split())
    if len(text) > 280:
        text = text[:277].rstrip() + "..."
    return f"- Review feedback to apply on future workflow runs: {text}"


def capture_feedback_as_standard(workflow_id: Optional[int], feedback: str) -> bool:
    """Persist meaningful feedback into the workflow Agent Context table."""
    if not workflow_id or not should_capture_feedback(feedback):
        return False
    standard = feedback_to_standard(feedback)
    with get_session() as db:
        wf = db.query(AutoWorkflow).filter(AutoWorkflow.id == int(workflow_id)).first()
        if not wf or (wf.workflow_type or "") == "audit":
            return False
        row = (
            db.query(AutoWorkflowVariable)
            .filter(
                AutoWorkflowVariable.workflow_id == int(workflow_id),
                AutoWorkflowVariable.name == ADAPTIVE_CONTEXT_TITLE,
            )
            .first()
        )
        if row:
            existing = row.default_value or ""
            if standard in existing:
                return False
            row.default_value = (existing.rstrip() + "\n" + standard).strip()
        else:
            row = AutoWorkflowVariable(
                workflow_id=int(workflow_id),
                name=ADAPTIVE_CONTEXT_TITLE,
                default_value=standard,
                description="Captured from workflow feedback and applied by future planning and validation.",
            )
            db.add(row)
        db.commit()
        return True


def ensure_universal_standards_context_item(workflow_id: int) -> bool:
    """Make the universal standards visible/editable in the Agent Context tab."""
    with get_session() as db:
        wf = db.query(AutoWorkflow).filter(AutoWorkflow.id == int(workflow_id)).first()
        if not wf or (wf.workflow_type or "") == "audit":
            return False
        row = (
            db.query(AutoWorkflowVariable)
            .filter(
                AutoWorkflowVariable.workflow_id == int(workflow_id),
                AutoWorkflowVariable.name == STANDARDS_CONTEXT_TITLE,
            )
            .first()
        )
        content = UNIVERSAL_WORKFLOW_STANDARDS.strip()
        if row:
            if (row.default_value or "").strip() == content:
                return False
            row.default_value = content
        else:
            db.add(
                AutoWorkflowVariable(
                    workflow_id=int(workflow_id),
                    name=STANDARDS_CONTEXT_TITLE,
                    default_value=content,
                    description="Baseline standards applied to workflow planning, execution, and validation.",
                )
            )
        db.commit()
        return True
