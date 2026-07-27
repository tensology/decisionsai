"""Adaptive workflow quality standards.

This module keeps the non-negotiable workflow quality bar close to execution.
Workflow-specific Agent Context rows can still tune the rules, but these
defaults stop ticket runs from treating a code change as completion.
"""

from __future__ import annotations

import re
from typing import Optional

from distr.core.db import get_session
from distr.core.db.workflow import AutoWorkflow, AutoWorkflowVariable


STANDARDS_CONTEXT_TITLE = "Universal Quality Standards"
ADAPTIVE_CONTEXT_TITLE = "Adaptive Quality Memory"
GLOBAL_STANDARDS_CONTEXT_TITLE = "Global User Standards"

GLOBAL_STANDARD_CATEGORIES = {
    "quality_standard",
    "ui_design_standard",
    "product_standard",
    "user_preference",
}


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


def build_standards_context(
    context_rules: Optional[str] = None,
    board_id: int | None = None,
    *,
    include_ui_standards: bool = True,
) -> str:
    """Append universal, global user, and board-learned standards to context."""
    existing = (context_rules or "").strip()
    standards = UNIVERSAL_WORKFLOW_STANDARDS.strip()
    if not existing:
        base = standards
    elif "[UNIVERSAL WORKFLOW QUALITY STANDARDS]" in existing:
        base = existing
    else:
        base = existing + "\n\n" + standards

    additions = []
    try:
        global_standards = build_global_user_standards_context(
            include_ui_standards=include_ui_standards,
        )
        if global_standards and "[GLOBAL USER STANDARDS]" not in base:
            additions.append(global_standards)
    except Exception:
        pass

    if board_id:
        try:
            from distr.core.orchestrator import build_learned_rules_context, build_visual_taste_context

            learned = build_learned_rules_context(
                int(board_id),
                include_ui_standards=include_ui_standards,
            )
            if learned and "[BOARD LEARNED RULES]" not in base:
                additions.append(learned)
            if include_ui_standards:
                taste = build_visual_taste_context(board_id=int(board_id))
                if taste and "[VISUAL TASTE MEMORY]" not in base:
                    additions.append(taste)
        except Exception:
            pass
    return base + ("\n\n" + "\n\n".join(additions) if additions else "")


def _looks_like_project_specific_instruction(content: str) -> bool:
    """Reject run inputs that were accidentally promoted as universal policy."""
    text = " ".join(str(content or "").split())
    lowered = text.lower()
    return bool(
        re.search(r"https?://", text)
        or re.search(r"\b[A-Z][A-Z0-9]+-\d+\b", text)
        or re.search(r"\bticket\s+#?\d+\b", lowered)
    )


def _is_reusable_global_standard(memory: dict, *, include_ui_standards: bool) -> bool:
    category = str(memory.get("category") or "")
    content = " ".join(str(memory.get("content") or "").split())
    lowered = content.lower()
    if not content or _looks_like_project_specific_instruction(content):
        return False
    if not include_ui_standards and (
        category == "ui_design_standard"
        or any(
            marker in lowered
            for marker in (
                " ui ", "ux", "visual", "layout", "screenshot", "playwright",
                "browser flow", "responsive", "spotify", "now-playing",
            )
        )
    ):
        return False
    if category == "quality_standard":
        reusable_language = (
            "always", "never", "must", "should", "do not", "don't", "avoid",
            "prefer", "require", "needs to", "need to", "reject", "keep ",
        )
        if not any(marker in lowered for marker in reusable_language):
            return False
    return True


def build_global_user_standards_context(
    *,
    limit: int = 16,
    include_ui_standards: bool = True,
) -> str:
    """Format durable cross-project user standards for workflow prompts."""
    from distr.core.orchestrator_memory import list_user_memories

    memories = list_user_memories(limit=max(30, int(limit) * 3), include_disabled=False)
    relevant = [
        memory for memory in memories
        if str(memory.get("scope") or "global") == "global"
        and str(memory.get("category") or "") in GLOBAL_STANDARD_CATEGORIES
        and _is_reusable_global_standard(
            memory,
            include_ui_standards=include_ui_standards,
        )
        and (
            str(memory.get("category") or "") != "user_preference"
            or int(memory.get("evidence_count") or 0) >= 2
        )
    ][:max(1, int(limit))]
    if not relevant:
        return ""
    lines = ["[GLOBAL USER STANDARDS]"]
    for memory in relevant:
        evidence = int(memory.get("evidence_count") or 0)
        suffix = f" (reinforced {evidence}x)" if evidence > 1 else ""
        content = " ".join(str(memory.get("content") or "").split())[:320]
        lines.append(f"- {content}{suffix}")
    return "\n".join(lines)


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


def capture_feedback_as_global_standard(
    feedback: str,
    *,
    category: str = "quality_standard",
    source_type: str = "workflow_feedback",
    source_id: str = "",
    project_id: int | None = None,
) -> bool:
    """Persist reusable feedback as a durable standard available to new projects."""
    if not should_capture_feedback(feedback):
        return False
    clean = " ".join((feedback or "").strip().split())[:900]
    from distr.core.orchestrator_memory import record_user_memory

    memory_uid = record_user_memory(
        clean,
        category=category if category in GLOBAL_STANDARD_CATEGORIES else "quality_standard",
        source_type=source_type,
        source_id=source_id,
        project_id=project_id,
        tags=["workflow", "learned_standard", "cross_project"],
        scope="global",
        confidence=0.82,
        payload={"original_feedback": clean},
    )
    return bool(memory_uid)


def _workflow_accepts_adaptive_standards(workflow_id: int) -> bool:
    with get_session() as db:
        wf = db.query(AutoWorkflow).filter(AutoWorkflow.id == int(workflow_id)).first()
        if not wf:
            return False
        return (wf.workflow_type or "") not in {"audit", "project_cli"}


def capture_feedback_as_memory(
    feedback: str,
    *,
    workflow_id: Optional[int] = None,
    linked_workflow_id: Optional[int] = None,
    board_id: Optional[int] = None,
    project_id: Optional[int] = None,
) -> bool:
    """Persist reusable feedback into learned rules and workflow adaptive context."""
    if not should_capture_feedback(feedback):
        return False

    captured = False
    standard = feedback_to_standard(feedback)
    captured = capture_feedback_as_global_standard(
        feedback,
        source_id=str(workflow_id or linked_workflow_id or ""),
        project_id=project_id,
    ) or captured
    target_workflow_id = None
    if workflow_id and _workflow_accepts_adaptive_standards(int(workflow_id)):
        target_workflow_id = int(workflow_id)
    elif linked_workflow_id and _workflow_accepts_adaptive_standards(int(linked_workflow_id)):
        target_workflow_id = int(linked_workflow_id)

    if target_workflow_id:
        captured = capture_feedback_as_standard(target_workflow_id, feedback) or captured

    try:
        from distr.core.orchestrator import record_learning_signal

        scope = "board" if board_id else "project" if project_id else "global"
        scope_id = board_id or project_id
        record_learning_signal(
            scope=scope,
            scope_id=scope_id,
            rule_type="adaptive_standard",
            summary=standard[:500],
            payload={
                "workflow_id": workflow_id,
                "linked_workflow_id": linked_workflow_id,
                "feedback_excerpt": feedback[:500],
            },
        )
        captured = True
    except Exception:
        pass
    return captured


def capture_feedback_as_standard(workflow_id: Optional[int], feedback: str) -> bool:
    """Persist meaningful feedback into the workflow Agent Context table."""
    if not workflow_id or not should_capture_feedback(feedback):
        return False
    if not _workflow_accepts_adaptive_standards(int(workflow_id)):
        return False
    standard = feedback_to_standard(feedback)
    with get_session() as db:
        wf = db.query(AutoWorkflow).filter(AutoWorkflow.id == int(workflow_id)).first()
        if not wf:
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
