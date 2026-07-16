"""Orchestrator routing — policy-first with optional LLM override and learned rules."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Literal

logger = logging.getLogger(__name__)

RouteSource = Literal["policy", "board_override", "orchestrator_override", "harness_preference", "fallback"]

COMPLEXITY_ORDER = {"low": 0, "medium": 1, "high": 2}


@dataclass
class RouteDecision:
    backend_id: str
    model: str
    complexity: str
    source: RouteSource
    rationale: str
    requires_approval: bool = False
    codex_reasoning_effort: str = ""
    codex_service_tier: str = ""
    policy_route: dict[str, Any] = field(default_factory=dict)
    override_route: dict[str, Any] | None = None
    skills: list[str] = field(default_factory=list)
    task_intent: str = "general"
    risk_flags: list[str] = field(default_factory=list)
    ui_heavy: bool = False

    def to_route_dict(self) -> dict[str, Any]:
        """Dict consumed by run_project_task and API responses."""
        out: dict[str, Any] = {
            "complexity": self.complexity,
            "backend": self.backend_id,
            "model": self.model,
            "source": self.source,
            "rationale": self.rationale,
            "requires_approval": self.requires_approval,
            "skills": list(self.skills or []),
            "task_profile": {
                "intent": self.task_intent,
                "risk_flags": list(self.risk_flags or []),
                "ui_heavy": bool(self.ui_heavy),
            },
        }
        if self.codex_reasoning_effort:
            out["codex_reasoning_effort"] = self.codex_reasoning_effort
        if self.codex_service_tier:
            out["codex_service_tier"] = self.codex_service_tier
        return out


def resolve_board_for_ticket(session: Any, ticket: Any | None) -> Any | None:
    """Load KanbanBoard for a ticket via its lane."""
    if ticket is None or not getattr(ticket, "lane_id", None):
        return None
    try:
        from distr.core.db.kanban import KanbanBoard, KanbanLane

        lane = session.query(KanbanLane).filter(KanbanLane.id == int(ticket.lane_id)).first()
        if lane and getattr(lane, "board_id", None):
            return session.query(KanbanBoard).filter(KanbanBoard.id == int(lane.board_id)).first()
    except Exception:
        logger.debug("Could not resolve board for ticket", exc_info=True)
    return None


def _ticket_text(ticket: Any | None) -> str:
    if ticket is None:
        return ""
    title = str(getattr(ticket, "title", "") or "").strip()
    desc = str(getattr(ticket, "description", "") or "").strip()
    return f"{title}\n{desc}".strip()


def _infer_harness_category(text: str) -> str | None:
    lowered = (text or "").lower()
    if any(term in lowered for term in ("frontend", "react", "vue", "css", "ui", "tailwind", "playwright")):
        return "frontend"
    if any(term in lowered for term in ("api", "backend", "django", "postgres", "endpoint", "server")):
        return "api"
    if any(term in lowered for term in ("auth", "migration", "architecture", "refactor", "integration")):
        return "fullstack"
    return None


def _task_intent(text: str, intake_profile: dict[str, Any]) -> str:
    """Return a vendor-neutral task intent used by per-step Auto routing."""
    lowered = (text or "").lower()
    if any(term in lowered for term in ("deploy", "release", "publish", "ship")):
        return "deployment"
    if any(term in lowered for term in ("review", "audit", "validate", "verify", "qa")):
        return "review"
    if any(term in lowered for term in ("plan", "scope", "requirements", "architecture", "design")):
        return "planning"
    if intake_profile.get("ui_heavy"):
        return "ui_implementation"
    if any(term in lowered for term in ("fix", "implement", "build", "refactor", "code")):
        return "implementation"
    return "general"


def _apply_harness_preferences(
    route: dict[str, str],
    policy: dict[str, Any],
    ticket: Any | None,
) -> tuple[dict[str, str], str, list[str]]:
    """Apply board harness_preferences when ticket text matches a category."""
    prefs = policy.get("harness_preferences") or {}
    if not isinstance(prefs, dict):
        return route, "", []
    category = _infer_harness_category(_ticket_text(ticket))
    if not category:
        return route, "", []
    pref = prefs.get(category) or {}
    if not isinstance(pref, dict):
        return route, "", []
    skills = [str(s).strip() for s in (pref.get("skills") or []) if str(s).strip()]
    backend = str(pref.get("backend") or "").strip()
    if not backend:
        return route, "", skills
    from distr.core.project_cli_backends import normalize_backend_id

    updated = dict(route)
    updated["backend"] = normalize_backend_id(backend)
    if pref.get("model"):
        updated["model"] = str(pref["model"]).strip()
    rationale = f"Board harness preference for {category} tickets"
    return updated, rationale, skills


def _recent_harness_outcomes(
    *,
    project_id: int | None,
    board_id: int | None,
    limit: int = 5,
) -> list[dict[str, Any]]:
    try:
        from distr.core.orchestrator import list_events

        events = list_events(board_id=board_id, limit=40)
        outcomes: list[dict[str, Any]] = []
        for event in events:
            if event.get("event_type") not in {
                "execution_session_completed",
                "validation_recorded",
                "route_decided",
            }:
                continue
            if project_id and event.get("project_id") not in (None, project_id):
                continue
            outcomes.append(
                {
                    "type": event.get("event_type"),
                    "status": event.get("status"),
                    "summary": event.get("summary"),
                    "payload": event.get("payload") or {},
                }
            )
            if len(outcomes) >= limit:
                break
        return outcomes
    except Exception:
        return []


def _backend_status_summary() -> dict[str, bool]:
    try:
        from distr.core.project_cli_backends import get_backend_statuses, list_backends

        return {bid: bool((get_backend_statuses(bid) or {}).get("ready")) for bid in list_backends()}
    except Exception:
        return {}


def _call_orchestrator_llm(
    *,
    ticket: Any | None,
    complexity: str,
    baseline: dict[str, str],
    policy: dict[str, Any],
    learned_context: str,
    recent_outcomes: list[dict[str, Any]],
    backend_status: dict[str, bool],
) -> dict[str, Any] | None:
    """Optional LLM advisory for hybrid routing. Returns None on skip/failure."""
    try:
        from distr.core.orchestrator import get_orchestrator_role_model
        from distr.core.settings import load_settings_from_db

        settings = load_settings_from_db()
        if not bool(settings.get("orchestrator_routing_enabled", True)):
            return None
        if str(policy.get("routing_mode") or "hybrid").lower() == "policy":
            return None

        provider, model = get_orchestrator_role_model("orchestrator")
        if not provider and not model:
            return None

        from distr.core.skills.catalog import orchestrator_skill_catalog
        from distr.core.workflow.planning import _litellm_model

        import litellm

        prompt = {
            "task": "Suggest execution harness and bundled skills to transfer for a ticket. Return JSON only.",
            "ticket": _ticket_text(ticket)[:4000],
            "complexity": complexity,
            "baseline_route": baseline,
            "available_backends": backend_status,
            "learned_rules": learned_context[:3000],
            "recent_outcomes": recent_outcomes,
            "allowed_backends": ["pi", "cursor", "claude_code", "codex", "hermes_agent"],
            "available_skills": orchestrator_skill_catalog(limit=100),
            "response_schema": {
                "backend": "string",
                "model": "string",
                "rationale": "string",
                "confidence": "0-1 float",
                "escalate_to_ide": "boolean",
                "skills": ["skill_id strings from available_skills only"],
            },
        }
        response = litellm.completion(
            model=_litellm_model(provider.strip().lower(), model, settings),
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are the workflow orchestrator. "
                        "Suggest the best coding harness and which bundled skills to push into the project "
                        "before execution (use skill ids from available_skills). "
                        "Only override baseline when clearly beneficial. Respond with valid JSON only. "
                        "Do not mention internal system names."
                    ),
                },
                {"role": "user", "content": json.dumps(prompt, ensure_ascii=False)},
            ],
            max_tokens=512,
            temperature=0.2,
        )
        content = (response.choices[0].message.content or "").strip()
        content = re.sub(r"^```\w*\s*", "", content)
        content = re.sub(r"\s*```\s*$", "", content)
        parsed = json.loads(content)
        return parsed if isinstance(parsed, dict) else None
    except Exception as exc:
        logger.debug("Orchestrator LLM advisory skipped: %s", exc)
        return None


def _determine_source(baseline: dict[str, str], final: dict[str, str], board: Any | None) -> RouteSource:
    if final.get("backend") != baseline.get("backend") or final.get("model") != baseline.get("model"):
        if board is not None:
            from distr.core.orchestrator import normalize_board_orchestrator_policy

            policy = normalize_board_orchestrator_policy(getattr(board, "orchestrator_policy", None))
            board_routes = policy.get("complexity_routing") or {}
            level = final.get("complexity") or baseline.get("complexity") or "medium"
            if isinstance(board_routes, dict) and board_routes.get(level):
                return "board_override"
        return "orchestrator_override"
    if board is not None:
        from distr.core.orchestrator import normalize_board_orchestrator_policy

        policy = normalize_board_orchestrator_policy(getattr(board, "orchestrator_policy", None))
        board_routes = policy.get("complexity_routing") or {}
        level = final.get("complexity") or "medium"
        if isinstance(board_routes, dict) and board_routes.get(level):
            return "board_override"
    return "policy"


def resolve_execution_route(
    *,
    project: Any,
    ticket: Any | None = None,
    board: Any | None = None,
    complexity: str | None = None,
    run_id: int | None = None,
    step_id: int | None = None,
    workflow_id: int | None = None,
    allow_orchestrator_override: bool = True,
    emit_event: bool = True,
) -> RouteDecision:
    """Resolve the execution harness for a ticket using hybrid orchestrator routing."""
    from distr.core.kanban.ticket_policy import normalize_ticket_complexity, resolve_ticket_cli_route
    from distr.core.harness.intake import classify_intake
    from distr.core.project_cli_backends import get_backend, normalize_backend_id
    from distr.core.skills.catalog import filter_known_skill_ids, infer_skills_for_ticket
    from distr.core.orchestrator import (
        build_learned_rules_context,
        build_visual_taste_context,
        emit_event as orchestrator_emit,
        inspect_visual_baseline_readiness,
        normalize_board_orchestrator_policy,
        record_routing_override,
        resolve_board_id_for_ticket,
    )

    ticket_text = _ticket_text(ticket)
    intake_profile = classify_intake(ticket_text)
    level = normalize_ticket_complexity(
        complexity or (getattr(ticket, "complexity", None) if ticket else None)
    )
    if intake_profile.get("route_pressure") == "codex" and level == "low":
        level = "medium"
    board_id = getattr(board, "id", None) if board else None
    if board_id is None and ticket is not None:
        board_id = resolve_board_id_for_ticket(getattr(ticket, "id", None))

    policy = normalize_board_orchestrator_policy(getattr(board, "orchestrator_policy", None) if board else None)
    baseline = resolve_ticket_cli_route(project, level, board=board)
    baseline["complexity"] = level

    route = dict(baseline)
    skills: list[str] = []
    pref_rationale = ""

    pref_route, pref_rationale, pref_skills = _apply_harness_preferences(route, policy, ticket)
    if pref_route.get("backend") != route.get("backend"):
        route = pref_route
        source_hint: RouteSource = "harness_preference"
    else:
        source_hint = "policy"
    if pref_skills:
        skills.extend(pref_skills)

    inferred_skills = infer_skills_for_ticket(ticket_text)
    if inferred_skills:
        skills.extend(inferred_skills)

    visual_baseline_readiness: dict[str, Any] | None = None
    if intake_profile.get("ui_heavy") and board_id:
        try:
            visual_baseline_readiness = inspect_visual_baseline_readiness(
                board_id=board_id,
                project_id=getattr(project, "id", None) if project else None,
                include_global=True,
            )
        except Exception:
            visual_baseline_readiness = None

    intake_override = str(intake_profile.get("override") or "").strip()
    override_requested_backend = ""
    if intake_override in {"promote_to_codex", "ui_critical"}:
        override_requested_backend = "codex"
    elif intake_override == "demote_to_cursor":
        override_requested_backend = "cursor"

    override_original_backend = normalize_backend_id(route.get("backend") or "")
    override_applied = False
    if override_requested_backend:
        may_apply_override = intake_override != "demote_to_cursor" or intake_profile.get("route_pressure") == "cursor"
        if may_apply_override:
            route["backend"] = normalize_backend_id(override_requested_backend)
            route["model"] = str(route.get("model") or "auto").strip()
            pref_rationale = f"Intake override '{intake_override.replace('_', ' ')}' requested {route['backend']}"
            source_hint = "harness_preference"
            override_applied = True

    override_payload: dict[str, Any] | None = None
    rationale = pref_rationale or f"Policy route for {level} complexity"
    if visual_baseline_readiness and not visual_baseline_readiness.get("ready"):
        missing_count = int(visual_baseline_readiness.get("missing_screen_count") or 0)
        rationale = (
            f"{rationale}; visual baseline not ready"
            + (f" ({missing_count} missing reference screen{'s' if missing_count != 1 else ''})" if missing_count else "")
        )
        if normalize_backend_id(route.get("backend") or "") == "cursor" and intake_override != "demote_to_cursor":
            route["backend"] = "codex"
            route["model"] = str(route.get("model") or "auto").strip()
            source_hint = "harness_preference"

    if allow_orchestrator_override:
        learned = build_learned_rules_context(board_id)
        visual_taste = build_visual_taste_context(board_id=board_id) if board_id else ""
        if visual_taste:
            learned = (learned + "\n\n" + visual_taste).strip() if learned else visual_taste
        advisory = _call_orchestrator_llm(
            ticket=ticket,
            complexity=level,
            baseline=baseline,
            policy=policy,
            learned_context=learned,
            recent_outcomes=_recent_harness_outcomes(
                project_id=getattr(project, "id", None),
                board_id=board_id,
            ),
            backend_status=_backend_status_summary(),
        )
        if advisory:
            suggested_backend = normalize_backend_id(str(advisory.get("backend") or "").strip())
            suggested_model = str(advisory.get("model") or route.get("model") or "auto").strip()
            advisory_skills = filter_known_skill_ids(
                [str(s).strip() for s in (advisory.get("skills") or []) if str(s).strip()]
            )
            if not override_applied and suggested_backend and (
                suggested_backend != route.get("backend")
                or suggested_model != route.get("model")
            ):
                override_payload = {
                    "backend": suggested_backend,
                    "model": suggested_model,
                    "rationale": str(advisory.get("rationale") or "").strip(),
                    "confidence": advisory.get("confidence"),
                }
                route["backend"] = suggested_backend
                route["model"] = suggested_model
                rationale = override_payload.get("rationale") or rationale
                source_hint = "orchestrator_override"
                skills.extend(advisory_skills)

    backend_id = normalize_backend_id(route.get("backend") or "pi")
    model = str(route.get("model") or "").strip()
    try:
        if not get_backend(backend_id).setup_status().ready:
            fallback = resolve_ticket_cli_route(project, level, board=None)
            backend_id = normalize_backend_id(fallback.get("backend") or "pi")
            model = str(fallback.get("model") or "").strip()
            rationale = f"{rationale} (fallback: chosen backend unavailable)"
            source_hint = "fallback"
    except Exception:
        backend_id = "pi"
        model = ""
        source_hint = "fallback"
        rationale = f"{rationale} (fallback: backend check failed)"

    source = source_hint if source_hint != "policy" else _determine_source(baseline, route, board)
    requires_approval = bool(
        override_payload
        and source == "orchestrator_override"
        and bool(policy.get("require_approval_for_override", True))
    )
    if requires_approval:
        route = dict(baseline)
        backend_id = normalize_backend_id(route.get("backend") or "pi")
        model = str(route.get("model") or "").strip()
        source = _determine_source(baseline, baseline, board)
        rationale = (
            f"Route override pending approval: "
            f"{override_payload.get('rationale') if override_payload else ''}"
        ).strip()

    decision = RouteDecision(
        backend_id=backend_id,
        model=model,
        complexity=level,
        source=source,
        rationale=rationale,
        requires_approval=requires_approval,
        codex_reasoning_effort=str(route.get("codex_reasoning_effort") or "").strip(),
        codex_service_tier=str(route.get("codex_service_tier") or "").strip(),
        policy_route=dict(baseline),
        override_route=override_payload,
        skills=filter_known_skill_ids(skills),
        task_intent=_task_intent(ticket_text, intake_profile),
        risk_flags=list(intake_profile.get("risk_flags") or []),
        ui_heavy=bool(intake_profile.get("ui_heavy")),
    )

    if override_requested_backend:
        try:
            record_routing_override(
                override=intake_override,
                requested_backend=override_requested_backend,
                original_backend=override_original_backend,
                final_backend=backend_id,
                applied=backend_id == normalize_backend_id(override_requested_backend),
                workflow_id=workflow_id,
                run_id=run_id,
                step_id=step_id,
                ticket_id=getattr(ticket, "id", None) if ticket else None,
                board_id=board_id,
                project_id=getattr(project, "id", None) if project else None,
                reasons=list(intake_profile.get("reasons") or []),
            )
        except Exception:
            logger.debug("Could not record routing override", exc_info=True)

    if emit_event:
        try:
            orchestrator_emit(
                source="orchestrator",
                event_type="route_decided",
                status="pending_approval" if requires_approval else "ready",
                workflow_id=workflow_id,
                run_id=run_id,
                step_id=step_id,
                ticket_id=getattr(ticket, "id", None) if ticket else None,
                board_id=board_id,
                project_id=getattr(project, "id", None) if project else None,
                summary=(
                    f"Selected {backend_id}"
                    + (f" / {model}" if model else " / auto")
                    + f" for {level}-complexity work. {rationale}"
                ),
                payload={
                    "decision": decision.to_route_dict(),
                    "policy_route": baseline,
                    "override": override_payload,
                    "intake_profile": intake_profile,
                    "visual_baseline_readiness": visual_baseline_readiness,
                },
            )
        except Exception:
            logger.debug("Could not emit route_decided event", exc_info=True)

    return decision
