"""Deterministic selection between one direct agent turn and an orchestrated workflow.

Workflows run only when the user explicitly chooses a named workflow (passed as
workflow_id into dispatch_work_item). Prompts and tickets never auto-wrap.
"""

from __future__ import annotations

from typing import Any


def workflow_step_skills(role: str, configured: list[Any] | None = None) -> list[str]:
    """Return infrastructure skills available to one orchestrated worker."""
    values: list[Any] = ["decisions-harness-stack", "decisions-headroom", *(configured or [])]
    if str(role or "").strip().lower() in {"review", "final_polish", "reporting"}:
        values.append("agent-watchdog")
    output: list[str] = []
    for value in values:
        skill_id = str(value or "").strip()
        if skill_id and skill_id not in output:
            output.append(skill_id)
    return output


def choose_development_execution_mode(
    instruction: str,
    *,
    assessment: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Always choose a direct worker turn.

    An orchestrated workflow is started only when the caller passes an explicit
    workflow_id (named workflow chosen by the user). Auto-wrap is retired.
    """
    assessment = dict(assessment or {})
    if isinstance(assessment.get("proposal_scope"), dict):
        return choose_proposal_execution_mode(assessment["proposal_scope"])
    complexity = str(assessment.get("complexity") or "medium").strip().lower()
    if complexity not in {"low", "medium", "high"}:
        complexity = "medium"
    operational_state = str(
        assessment.get("operational_state") or assessment.get("sentiment") or "neutral"
    ).strip().lower()
    return {
        "mode": "direct",
        "complexity": complexity,
        "operational_state": operational_state,
        "signals": ["direct_by_default"],
        "reason": "Prompts and tickets run as one direct development-agent turn; workflows require an explicit named choice.",
    }


def proposal_execution_scope(proposal: dict[str, Any]) -> dict[str, Any]:
    """Only requested work participates in routing, never transport scaffolding."""
    return {
        "title": str(proposal.get("title") or "").strip(),
        "description": str(proposal.get("description") or "").strip(),
        "tasks": [
            str(task).strip()
            for task in (proposal.get("tasks") if isinstance(proposal.get("tasks"), list) else [])
            if str(task).strip()
        ],
    }


def choose_proposal_execution_mode(proposal: dict[str, Any]) -> dict[str, Any]:
    """Always hand off as one direct agent turn unless a named workflow is chosen elsewhere."""
    import hashlib
    import json

    scope = proposal_execution_scope(proposal)
    return {
        "mode": "direct",
        "complexity": "low",
        "workflow_class": None,
        "policy": "proposal-scope-v1",
        "scope_hash": hashlib.sha256(json.dumps(scope, sort_keys=True).encode()).hexdigest(),
        "signals": ["direct_by_default"],
        "reason": "Requested work runs as one direct agent turn; workflows require an explicit named choice.",
        "scope_text": "\n".join([scope["title"], scope["description"], *scope["tasks"]]),
    }
