"""Shared project handoff service for workflow/automation/proactive dispatch."""

from __future__ import annotations

from typing import Any


class ProjectHandoffService:
    """Centralize Decisions-to-project backend dispatch metadata."""

    async def dispatch(self, context: Any) -> Any:
        from distr.core.project_cli_backends.registry import run_project_task

        result = await run_project_task(
            context.project,
            context.instruction,
            run_id=context.run_id,
            workflow_id=context.workflow_id,
            step_id=context.step_id,
            origin=context.origin,
            ticket_id=context.ticket_id,
            ticket_complexity=context.ticket_complexity,
            backend_id_override=context.backend_id,
            model_override=context.model or None,
            codex_reasoning_effort_override=context.codex_reasoning_effort or None,
            codex_service_tier_override=context.codex_service_tier or None,
            on_event=context.on_event,
        )
        self._emit_handoff_event(context, result)
        return result

    def _emit_handoff_event(self, context: Any, result: Any) -> None:
        try:
            from distr.core.orchestration_events import emit_orchestration_event

            success = bool(getattr(result, "success", False))
            waits_for_human = bool(getattr(result, "waits_for_human", False))
            backend_id = getattr(result, "backend_id", None) or context.backend_id
            execution_session_id = getattr(result, "execution_session_id", None)
            emit_orchestration_event(
                source=backend_id or "project_handoff",
                event_type="project_handoff_dispatched",
                status="waiting" if waits_for_human and success else ("completed" if success else "failed"),
                workflow_id=context.workflow_id,
                run_id=context.run_id,
                step_id=context.step_id,
                ticket_id=context.ticket_id,
                board_id=context.board_id,
                project_id=getattr(context.project, "id", None),
                execution_session_id=execution_session_id,
                summary=f"Project handoff dispatched to {backend_id or 'worker'}.",
                payload={
                    "surface": backend_id or "project_handoff",
                    "subtype": "project_handoff_dispatched",
                    "backend_id": backend_id,
                    "origin": context.origin,
                    "is_workflow_attached": bool(context.workflow_id or context.run_id or context.step_id),
                },
                evidence={
                    "output": (getattr(result, "output", "") or "")[:4000],
                    "error": (getattr(result, "error", "") or "")[:1000],
                },
            )
        except Exception:
            return


async def dispatch_project_handoff(context: Any) -> Any:
    return await ProjectHandoffService().dispatch(context)
