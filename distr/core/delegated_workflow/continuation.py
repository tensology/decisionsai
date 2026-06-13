"""Continuation handling for blocked delegated Hermes runs."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import logging
import re
from typing import Any

from .events import record_delegated_run_report
from .models import DelegatedPlan, DelegatedStep
from .roadblocks import format_run_report_for_telegram

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DelegatedContinuationIntent:
    action: str
    preferred_route: str = ""
    freeform: str = ""

    def to_safe_dict(self) -> dict[str, Any]:
        try:
            from distr.core.orchestrator import redact_handoff_payload

            return redact_handoff_payload(asdict(self))
        except Exception:
            return asdict(self)


_ACTION_PATTERNS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("retry", ("retry", "try again", "rerun", "run it again")),
    ("continue", ("continue", "keep going", "proceed", "carry on", "yes continue")),
    ("skip", ("skip", "skip that step")),
    ("cancel", ("cancel", "stop", "abort")),
)

_ROUTE_PATTERNS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("browser_automation", ("browser fallback", "browser automation", "browseruse", "browser use", "playwright")),
    ("desktop_accessibility", ("accessibility", "accessibility tree", "desktop fallback")),
    ("sidecar", ("sidecar", "keyboard", "mouse", "clipboard")),
    ("google_workspace", ("gmail", "google workspace", "email api")),
    ("project_cli_backend", ("codex", "cursor", "project backend")),
)


def parse_delegated_continuation_intent(text: str) -> DelegatedContinuationIntent | None:
    raw = (text or "").strip()
    if not raw:
        return None
    lowered = re.sub(r"\s+", " ", raw.lower()).strip()

    action = ""
    for candidate, phrases in _ACTION_PATTERNS:
        if any(_contains_phrase(lowered, phrase) for phrase in phrases):
            action = candidate
            break
    if not action:
        return None

    route = ""
    for candidate, phrases in _ROUTE_PATTERNS:
        if any(_contains_phrase(lowered, phrase) for phrase in phrases):
            route = candidate
            break

    return DelegatedContinuationIntent(action=action, preferred_route=route, freeform=raw)


def _contains_phrase(text: str, phrase: str) -> bool:
    return re.search(rf"(?<!\w){re.escape(phrase)}(?!\w)", text) is not None


def find_latest_delegated_run_report(
    *,
    project_id: int | None = None,
    limit: int = 30,
) -> dict[str, Any] | None:
    from distr.core.orchestrator import list_events

    filters: dict[str, Any] = {"limit": limit}
    if project_id is not None:
        filters["project_id"] = project_id
    try:
        events = list_events(**filters)
    except Exception as exc:
        logger.warning("Could not list Hermes delegated run reports: %s", exc, exc_info=True)
        return None
    for event in events:
        if event.get("event_type") == "delegated_run_report":
            return event
    return None


def record_delegated_continuation(
    intent: DelegatedContinuationIntent,
    latest_run_event: dict[str, Any],
) -> int | None:
    from distr.core.orchestrator import emit_event

    payload = intent.to_safe_dict()
    payload["target_event_id"] = latest_run_event.get("id")
    payload["target_status"] = latest_run_event.get("status") or ""
    payload["target_kind"] = _event_kind(latest_run_event)
    return emit_event(
        source="telegram",
        event_type="delegated_continuation_requested",
        status="requested",
        workflow_id=_int_or_none(latest_run_event.get("workflow_id")),
        run_id=_int_or_none(latest_run_event.get("run_id")),
        step_id=_int_or_none(latest_run_event.get("step_id")),
        ticket_id=_int_or_none(latest_run_event.get("ticket_id")),
        board_id=_int_or_none(latest_run_event.get("board_id")),
        project_id=_int_or_none(latest_run_event.get("project_id")),
        execution_session_id=_int_or_none(latest_run_event.get("execution_session_id")),
        parent_event_id=_int_or_none(latest_run_event.get("id")),
        summary=f"Delegated continuation requested: {intent.action}.",
        payload=payload,
        evidence={
            "action": intent.action,
            "preferred_route": intent.preferred_route,
            "target_event_id": latest_run_event.get("id"),
            "target_status": latest_run_event.get("status") or "",
        },
    )


def handle_delegated_continuation_message(
    manager: Any,
    text: str,
    *,
    project_id: int | None = None,
    runner: Any = None,
) -> bool:
    intent = parse_delegated_continuation_intent(text)
    if not intent:
        return False

    latest = find_latest_delegated_run_report(project_id=project_id)
    if not latest:
        return False

    event_id = record_delegated_continuation(intent, latest)
    run_id = latest.get("id") or "latest"
    route = f" using {intent.preferred_route}" if intent.preferred_route else ""
    suffix = f" Event #{event_id} recorded." if event_id else ""
    manager.send_to_telegram(
        f"{intent.action.capitalize()} requested for delegated run {run_id}{route}.{suffix}"
    )
    if intent.action in {"retry", "continue"}:
        result = execute_delegated_continuation(intent, latest, runner=runner)
        if result.get("telegram_report"):
            manager.send_to_telegram(result["telegram_report"])
    return True


def execute_delegated_continuation(
    intent: DelegatedContinuationIntent,
    latest_run_event: dict[str, Any],
    *,
    runner: Any = None,
) -> dict[str, Any]:
    if intent.action not in {"retry", "continue"}:
        return {"executed": False, "reason": f"Action '{intent.action}' does not execute a retry."}
    plan = _plan_from_event(latest_run_event)
    if plan is None:
        return {"executed": False, "reason": "The delegated run event does not contain a runnable plan."}

    runner = runner or _default_runner()
    context = _context_from_event(latest_run_event)
    context["continuation_action"] = intent.action
    if intent.preferred_route:
        context["preferred_route"] = intent.preferred_route
    report = runner.run(plan, context=context)
    run_event_id = record_delegated_run_report(
        report,
        workflow_id=context.get("workflow_id"),
        run_id=context.get("run_id"),
        step_id=context.get("step_id"),
        ticket_id=context.get("ticket_id"),
        board_id=context.get("board_id"),
        project_id=context.get("project_id"),
    )
    return {
        "executed": True,
        "run_event_id": run_event_id,
        "report": report,
        "telegram_report": format_run_report_for_telegram(report, run_id=run_event_id),
    }


def _event_kind(event: dict[str, Any]) -> str:
    payload = event.get("payload") if isinstance(event, dict) else {}
    if not isinstance(payload, dict):
        return ""
    plan = payload.get("plan")
    if isinstance(plan, dict):
        return str(plan.get("kind") or "")
    return ""


def _plan_from_event(event: dict[str, Any]) -> DelegatedPlan | None:
    payload = event.get("payload") if isinstance(event, dict) else {}
    if not isinstance(payload, dict):
        return None
    plan_payload = payload.get("plan")
    if not isinstance(plan_payload, dict):
        return None
    steps = []
    for item in plan_payload.get("steps") or []:
        if not isinstance(item, dict):
            continue
        steps.append(
            DelegatedStep(
                action=str(item.get("action") or ""),
                preferred_route=str(item.get("preferred_route") or ""),
                fallback_routes=[str(value) for value in item.get("fallback_routes") or []],
                description=str(item.get("description") or ""),
                params=dict(item.get("params") or {}),
                verifies=[str(value) for value in item.get("verifies") or []],
            )
        )
    return DelegatedPlan(
        kind=str(plan_payload.get("kind") or "general_delegated_request"),
        source_surface=str(plan_payload.get("source_surface") or "telegram"),
        original_instruction=str(plan_payload.get("original_instruction") or ""),
        steps=steps,
        requires_approval_before=[str(value) for value in plan_payload.get("requires_approval_before") or []],
        target_backend=str(plan_payload.get("target_backend") or ""),
        confidence=_float_or_default(plan_payload.get("confidence"), 0.7),
    )


def _context_from_event(event: dict[str, Any]) -> dict[str, Any]:
    return {
        "workflow_id": _int_or_none(event.get("workflow_id")),
        "run_id": _int_or_none(event.get("run_id")),
        "step_id": _int_or_none(event.get("step_id")),
        "ticket_id": _int_or_none(event.get("ticket_id")),
        "board_id": _int_or_none(event.get("board_id")),
        "project_id": _int_or_none(event.get("project_id")),
        "execution_session_id": _int_or_none(event.get("execution_session_id")),
        "source_surface": "telegram",
        "continued_from_event_id": _int_or_none(event.get("id")),
    }


def _default_runner() -> Any:
    from distr.core.agent.tools.integrations.delegated_workflow import _default_runner as build_default_runner

    return build_default_runner()


def _float_or_default(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _int_or_none(value: Any) -> int | None:
    try:
        if value is None or value == "":
            return None
        return int(value)
    except (TypeError, ValueError):
        return None
