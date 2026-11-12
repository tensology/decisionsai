"""Development request assessment and execution mode.

Workflows run only when the user explicitly chooses a named workflow (passed as
workflow_id into dispatch_work_item). Prompts and tickets never auto-wrap.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def assess_development_request(
    instruction: str, *, ticket_title: str = "", ticket_description: str = "", has_images: bool = False,
) -> dict[str, Any]:
    """Classify the current Development request for preview or direct dispatch."""
    from distr.core.kanban.ticket_policy import infer_ticket_complexity
    from distr.core.laya_runtime import assess_request, get_mode

    # Historical responses can describe unrelated risks. Assess only the new request.
    routing_text = "\n".join((instruction, ticket_title, ticket_description))
    combined = routing_text.lower()
    complexity = infer_ticket_complexity(
        ticket_title or instruction[:160], f"{ticket_description}\n{instruction}",
        file_count=1 if has_images else 0,
    )
    signal_terms = {
        "blocked": ("blocked", "stuck", "cannot continue", "permission denied", "unavailable"),
        "failure": ("failed", "failing", "regression", "exception", "traceback", "broken", "error"),
        "risk": ("security", "production", "data loss", "migration", "payment", "credential"),
    }
    signals = [signal for signal, terms in signal_terms.items() if any(term in combined for term in terms)]
    if has_images:
        signals.append("vision")
    laya = {"mode": "off", "used": False}
    try:
        laya["mode"] = get_mode()
        if laya["mode"] != "off":
            candidate = assess_request(routing_text)
            use_candidate = laya["mode"] == "prefer" or candidate["confidence"] >= 0.60
            if use_candidate:
                candidate_complexity = candidate["complexity"]
                if candidate["is_sensitive"] and "risk" not in signals:
                    signals.append("risk")
                complexity = candidate_complexity
            laya.update(candidate, used=use_candidate)
    except Exception as exc:
        logger.warning("Laya routing failed; using deterministic fallback: %s", exc)
        laya.update(used=False, fallback_reason=str(exc))
    operational_state = next((state for signal, state in (
        ("blocked", "blocked"), ("failure", "failing"), ("risk", "risk"),
    ) if signal in signals), "neutral")
    if complexity == "low" and signals:
        complexity = "medium"
    if complexity == "medium" and ("risk" in signals or {"blocked", "failure"}.issubset(signals)):
        complexity = "high"
    return {
        "complexity": complexity,
        "operational_state": operational_state,
        "sentiment": operational_state,
        "signals": signals,
        "decision_engine": "laya" if laya["used"] else "heuristic",
        "laya": laya,
    }


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
