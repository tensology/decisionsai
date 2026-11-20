"""Development request assessment and execution mode.

The Development composer uses the same work-intake allocator as ordinary chat
and incoming messages. A short, one-step change is handled directly; a larger
multi-part request starts the matching built-in workflow. Either way, the
caller gets a one-line reason it can show to the operator.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

_BUILTIN_NAMES = {
    "new-work-design-to-plan": "New Work",
    "existing-project-to-plan": "Existing Project",
    "development-implement-verify": "Development",
    "deploy-to-development": "Deploy to Development",
    "deploy-to-production": "Deploy to Production",
}


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


def _instruction_from_assessment(instruction: str, assessment: dict[str, Any]) -> str:
    scope = assessment.get("proposal_scope")
    if isinstance(scope, dict):
        from_scope = proposal_execution_scope(scope)
        parts = [from_scope["title"], from_scope["description"], *from_scope["tasks"]]
        text = "\n".join(part for part in parts if part)
        if text.strip():
            return text
    return str(instruction or "").strip()


def _visible_direct_reason(intake_reason: str, *, lightweight: bool, conversational: bool) -> str:
    if lightweight:
        return "Handled directly because this is a short, one-step change."
    if conversational:
        return (
            "Handled directly because this reads as a question or a small request, "
            "not a multi-step piece of work."
        )
    reason = str(intake_reason or "").strip().rstrip(".")
    if reason:
        return f"Handled directly because {reason[0].lower()}{reason[1:]}."
    return "Handled directly because this can be done in one development-agent turn."


def _visible_workflow_reason(workflow_name: str, slug_reason: str, intake_reason: str) -> str:
    name = str(workflow_name or "a multi-step workflow").strip()
    detail = str(slug_reason or intake_reason or "").strip().rstrip(".")
    if detail:
        detail = detail[0].lower() + detail[1:]
        return f"Started workflow {name} because {detail}."
    return (
        f"Started workflow {name} because this is a larger piece of work that should "
        "be planned, built, reviewed and checked."
    )


def _is_short_copy_edit(value: str) -> bool:
    """True for a short typo, heading, or label change typed in Development.

    Chat/incoming intake still treats a bare "fix the checkout validation" as
    full work. In the Development composer, a one-line copy edit should run
    directly and complete.
    """
    import re

    clean = re.sub(r"\s+", " ", str(value or "")).strip()
    if not clean or "\n" in str(value or "") or len(clean.split()) > 16:
        return False
    if re.search(r"\b(?:and then|after that|multiple|across|audit|investigate|test|validate|refactor|redesign|build|implement|migrate|integrate)\b", clean, re.I):
        return False
    return bool(re.search(
        r"\b(?:typo|spelling|heading|label|title|button text|wording|copy|welcome)\b",
        clean,
        re.I,
    )) and bool(re.match(
        r"^(?:please\s+|can\s+you\s+|could\s+you\s+)?"
        r"(?:make|change|update|add|remove|rename|replace|fix|correct)\b",
        clean,
        re.I,
    ))


def allocate_development_request(
    instruction: str,
    *,
    assessment: dict[str, Any] | None = None,
    project_hint: str = "",
    project_id: int | None = None,
    project_folder: str | None = None,
    resolve_workflow: bool = False,
) -> dict[str, Any]:
    """Decide whether a Development request should run directly or as a workflow.

    Uses the same work-intake classifier as chat and incoming messages. The word
    "project" in a question is not enough to start a workflow. ``resolve_workflow``
    looks up the built-in workflow id (needs a database) and should be used only
    when the caller is about to dispatch.
    """
    assessment = dict(assessment or {})
    override = str(assessment.get("execution_mode_override") or "").strip().lower()
    text = _instruction_from_assessment(instruction, assessment)
    complexity = str(assessment.get("complexity") or "medium").strip().lower()
    if complexity not in {"low", "medium", "high"}:
        complexity = "medium"
    operational_state = str(
        assessment.get("operational_state") or assessment.get("sentiment") or "neutral"
    ).strip().lower()

    def result(**extra: Any) -> dict[str, Any]:
        payload = {
            "mode": "direct",
            "complexity": complexity,
            "operational_state": operational_state,
            "signals": [],
            "reason": "",
            "visible_reason": "",
            "workflow_slug": None,
            "workflow_name": None,
            "workflow_id": None,
            "intake_action": None,
        }
        payload.update(extra)
        return payload

    if override == "direct":
        reason = "Handled directly because you chose to send this as one agent turn."
        return result(
            signals=["direct_override"],
            reason=reason,
            visible_reason=reason,
            intake_action="direct_override",
        )

    from distr.core.work_intake import WorkIntake, WorkIntakeAction
    from distr.core.work_intake.service import OrchestratorIntakeService

    metadata: dict[str, Any] = {}
    if project_id is not None:
        metadata["project_id"] = int(project_id)
    intake = WorkIntake(
        source="web",
        user_text=text,
        project_hint=str(project_hint or "").strip() or None,
        metadata=metadata or None,
    )
    decision = OrchestratorIntakeService().classify(intake)
    lightweight = bool((decision.diagnostics or {}).get("execute_lightweight"))
    if not lightweight and _is_short_copy_edit(text):
        lightweight = True
    conversational = decision.action == WorkIntakeAction.ANSWER_DIRECTLY
    wants_workflow = (
        decision.action == WorkIntakeAction.RUN_WORKFLOW
        and not conversational
        and not lightweight
    )

    if not wants_workflow:
        visible = _visible_direct_reason(
            decision.reason, lightweight=lightweight, conversational=conversational,
        )
        signals = ["handled_directly"]
        if lightweight:
            signals.append("lightweight_project_work")
        if conversational:
            signals.append("conversational")
        return result(
            signals=signals,
            reason=decision.reason,
            visible_reason=visible,
            intake_action=decision.action.value,
        )

    from distr.core.workflow.builtin import discern_builtin

    builtin = discern_builtin(text, project_folder=project_folder)
    slug = builtin.get("slug")
    if not slug:
        visible = (
            "Handled directly because none of the built-in workflows fits this request; "
            "it will run as one development-agent turn."
        )
        return result(
            signals=["handled_directly", "no_builtin_fit"],
            reason=str(builtin.get("reason") or decision.reason),
            visible_reason=visible,
            intake_action=decision.action.value,
        )

    workflow_name = _BUILTIN_NAMES.get(str(slug), str(slug))
    workflow_id = None
    if resolve_workflow:
        try:
            from distr.core.workflow.builtin import builtin_workflow_ids, ensure_builtin_workflows

            ensure_builtin_workflows()
            workflow_id = builtin_workflow_ids().get(str(slug))
        except Exception:
            logger.warning("Could not resolve built-in workflow %s", slug, exc_info=True)
            workflow_id = None
        if not workflow_id:
            visible = (
                f"Handled directly because the {workflow_name} workflow was selected but "
                "could not be started, so this will run as one development-agent turn."
            )
            return result(
                signals=["handled_directly", "workflow_unresolved"],
                reason=str(builtin.get("reason") or decision.reason),
                visible_reason=visible,
                workflow_slug=str(slug),
                workflow_name=workflow_name,
                intake_action=decision.action.value,
            )

    visible = _visible_workflow_reason(
        workflow_name, str(builtin.get("reason") or ""), decision.reason,
    )
    return result(
        mode="workflow",
        signals=["work_intake", "builtin_workflow"],
        reason=str(builtin.get("reason") or decision.reason),
        visible_reason=visible,
        workflow_slug=str(slug),
        workflow_name=workflow_name,
        workflow_id=int(workflow_id) if workflow_id else None,
        intake_action=decision.action.value,
    )


def choose_development_execution_mode(
    instruction: str,
    *,
    assessment: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Choose a direct worker turn or a built-in workflow for a Development request."""
    assessment = dict(assessment or {})
    if isinstance(assessment.get("proposal_scope"), dict):
        return choose_proposal_execution_mode(assessment["proposal_scope"])
    return allocate_development_request(instruction, assessment=assessment)


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
    """Incoming proposals stay on the shared dispatcher as one direct hand-off.

    Typed Development-composer requests use ``allocate_development_request``
    instead. A large proposal that is opened as a new Development thread is
    allocated there, from the prompt text.
    """
    import hashlib
    import json

    scope = proposal_execution_scope(proposal)
    return {
        "mode": "direct",
        "complexity": "low",
        "workflow_class": None,
        "policy": "proposal-scope-v1",
        "scope_hash": hashlib.sha256(json.dumps(scope, sort_keys=True).encode()).hexdigest(),
        "signals": ["direct_by_default", "direct_proposal_handoff"],
        "reason": "Requested work is handed to Development as one agent turn unless that thread's composer starts a workflow.",
        "visible_reason": "Handled directly as a Development hand-off. If the work is large, Development will start a workflow when the thread is opened.",
        "scope_text": "\n".join([scope["title"], scope["description"], *scope["tasks"]]),
    }
