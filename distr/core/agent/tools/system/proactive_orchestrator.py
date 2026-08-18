"""Tool for scanning important work signals and dispatching approved project work."""

from __future__ import annotations

import json
from typing import Any, Optional

from langchain.tools import BaseTool
from pydantic import BaseModel, Field


class ProactiveOrchestratorInput(BaseModel):
    action: str = Field(
        default="scan",
        description=(
            "scan to find important work; daily_plan to build today's plan; "
            "dispatch to send an approved candidate to the project backend; "
            "enable_jira_intake to turn on the Jira morning intake automation by voice; "
            "run_jira_intake to run one Jira email intake batch now."
        ),
    )
    candidate_id: Optional[int] = Field(
        default=None,
        description="Candidate event id to dispatch after user approval.",
    )
    limit: int = Field(
        default=12,
        ge=1,
        le=50,
        description="Maximum work candidates to return for scan.",
    )
    source: str = Field(
        default="",
        description="Optional source filter such as gmail, slack, whatsapp, trello, jira, telegram, or board.",
    )
    project_id: Optional[int] = Field(
        default=None,
        description="Optional project id to narrow the scan.",
    )
    board_id: Optional[int] = Field(
        default=None,
        description="Optional board id for scan or Jira intake staging.",
    )
    approved_by: str = Field(
        default="user",
        description="Who approved dispatch, for audit context.",
    )
    backend_id: str = Field(
        default="",
        description="Optional backend override for dispatch, such as codex or cursor.",
    )
    model: str = Field(
        default="",
        description="Optional model override for dispatch.",
    )
    format: str = Field(
        default="summary",
        description="summary for voice-first text with reference details, json for structured output.",
    )


class ProactiveOrchestratorTool(BaseTool):
    name: str = "proactive_orchestrator"
    description: str = (
        "Scan connected work sources (Gmail, WhatsApp, Jira, Trello, boards) for real work coming in, "
        "build daily plans, match items to projects and Codex/Cursor context, "
        "stage Jira/email intake in batches, dispatch approved work to workflow or project CLI, "
        "and after work finishes prepare a humanized client message for Telegram approval "
        "(send / revise by voice / leave). Do not wait for a named automation phrase — "
        "if work is in the inbox or boards, act on it."
    )
    args_schema: type[BaseModel] = ProactiveOrchestratorInput

    def _run(
        self,
        action: str = "scan",
        candidate_id: Optional[int] = None,
        limit: int = 12,
        source: str = "",
        project_id: Optional[int] = None,
        board_id: Optional[int] = None,
        approved_by: str = "user",
        backend_id: str = "",
        model: str = "",
        format: str = "summary",
        **kwargs,
    ) -> str:
        from distr.core.agent.tool_voice_format import voice_then_reference
        from distr.core import orchestrator_proactive

        action_name = (action or "scan").strip().lower().replace("-", "_").replace(" ", "_")
        from_automation_run = bool(kwargs.get("from_automation_run"))
        if action_name in {
            "enable_jira_intake",
            "enable_jira_morning_intake",
            "turn_on_jira_intake",
            "jira_morning_intake",
            "enable_jira_email_intake",
        }:
            result = self._enable_jira_intake()
        elif action_name in {"run_jira_intake", "jira_intake_now", "run_jira_morning_intake"}:
            result = self._run_jira_intake_now(board_id=board_id)
        elif action_name in {"daily_plan", "daily_plan", "plan", "day_plan", "morning_brief", "today"}:
            result = self._resolve_daily_plan_result(format=format, from_automation_run=from_automation_run)
        elif action_name in {"dispatch", "send", "approve"}:
            if not candidate_id:
                return "Tell me which work candidate to dispatch first."
            result = orchestrator_proactive.dispatch_proactive_candidate(
                int(candidate_id),
                approved_by=approved_by or "user",
                backend_id=backend_id,
                model=model,
            )
        else:
            result = orchestrator_proactive.run_proactive_check(
                limit=limit,
                source_filter=source or None,
                project_id=project_id,
                board_id=board_id,
            )

        if (format or "").strip().lower() == "json":
            return json.dumps(result, ensure_ascii=False, indent=2)

        spoken = str(result.get("spoken_summary") or "").strip()
        if not spoken:
            spoken = "I checked the work queue and have the details ready."
        reference = json.dumps(result, ensure_ascii=False, indent=2, default=str)
        return voice_then_reference(spoken, reference)

    def _enable_jira_intake(self) -> dict[str, Any]:
        from distr.core.kanban.jira_intake import enable_jira_morning_intake

        return enable_jira_morning_intake(enable_email_scan=True)

    def _run_jira_intake_now(self, *, board_id: Optional[int] = None) -> dict[str, Any]:
        from distr.core.kanban.work_ops import work_intake

        return work_intake(board_id=board_id or None, notify=True)

    def _resolve_daily_plan_result(self, *, format: str = "summary", from_automation_run: bool = False) -> dict[str, Any]:
        """Run the user's Daily plan automation when configured; else build inline."""
        if not from_automation_run:
            from distr.core.automation_orchestrator import dispatch_automation_to_current_chat
            from distr.core.automation_resolver import find_automation_for_daily_plan

            automation = find_automation_for_daily_plan(active_only=True)
            if automation:
                dispatch = dispatch_automation_to_current_chat(automation, manual=True, speak=False)
                if dispatch.get("status") in {"completed", "dispatched", "running"}:
                    return {
                        "success": True,
                        "action": "daily_plan",
                        "spoken_summary": str(dispatch.get("summary") or "I ran your Daily plan automation."),
                        "automation_id": automation.get("id"),
                        "execution_mode": "automation_preset",
                        "dispatch": dispatch,
                    }
                if dispatch.get("status") == "skipped":
                    return {
                        "success": False,
                        "action": "daily_plan",
                        "spoken_summary": str(dispatch.get("summary") or "Daily plan was skipped."),
                        "automation_id": automation.get("id"),
                        "execution_mode": "automation_preset",
                        "dispatch": dispatch,
                    }
        return self._build_daily_plan_result(format=format)

    def _build_daily_plan_result(self, *, format: str = "summary") -> dict[str, Any]:
        from distr.core.initiative.context import ContextAssembler
        from distr.core.initiative.planners import (
            build_planner_orchestration_actions,
            generate_planner_markdown,
            tts_excerpt_from_markdown,
        )

        try:
            from distr.core.settings import load_settings_from_db

            settings = load_settings_from_db()
        except Exception:
            settings = {}

        bundle = ContextAssembler().build(settings)
        instruction = (
            "Build today's practical work plan from all connected Decisions intelligence. "
            "Use email and Gmail signals when available, WhatsApp and Telegram intake, "
            "ticket boards, Jira/Trello/local boards, active projects, Codex/Cursor developer "
            "context, workflows, automations, orchestrator triage, and long-term memory. "
            "Prioritize what actually needs attention today, separate blocked items, and "
            "mention which source each important item came from."
        )
        markdown, date_info = generate_planner_markdown("day", settings, bundle, instruction)
        orchestration_actions = build_planner_orchestration_actions(bundle, scope="day")
        orchestration_results = self._execute_orchestration_actions(
            orchestration_actions,
            settings=settings,
        )
        spoken = tts_excerpt_from_markdown(markdown, max_len=650) or "I built your plan for today."
        return {
            "success": True,
            "action": "daily_plan",
            "spoken_summary": spoken,
            "markdown": markdown,
            "orchestration_actions": orchestration_actions,
            "orchestration_results": orchestration_results,
            "date_info": date_info,
            "source_summary": {
                "chat_messages": len(getattr(bundle, "chat_history", []) or []),
                "boards": len(getattr(bundle, "kanban_summary", []) or []),
                "scheduled_sessions": len(getattr(bundle, "scheduled_sessions", []) or []),
                "unfinished_workflows": len(getattr(bundle, "unfinished_workflows", []) or []),
                "skills": len(getattr(bundle, "skills", []) or []),
                "has_developer_context": bool(getattr(bundle, "developer_context", {}) or {}),
                "work_scan_sources": sorted(
                    (getattr(bundle, "work_scan", {}) or {}).keys()
                    if isinstance(getattr(bundle, "work_scan", {}), dict)
                    else []
                ),
                "has_memory": bool(
                    (getattr(bundle, "memory_user", "") or "").strip()
                    or (getattr(bundle, "memory_long_term", "") or "").strip()
                ),
            },
        }

    def _execute_orchestration_actions(self, actions: list[dict[str, Any]], *, settings: dict[str, Any]) -> list[dict[str, Any]]:
        if not actions:
            return []
        from distr.core.initiative.action_handlers import execute_initiative_action

        results: list[dict[str, Any]] = []
        for action in actions:
            action_type = str(action.get("action_type") or "").strip()
            payload = action.get("payload") if isinstance(action.get("payload"), dict) else {}
            if action_type == "ticket_lane_move" and not settings.get("initiative_allow_ticket_lane_moves", False):
                results.append({
                    "action_type": action_type,
                    "success": False,
                    "message": "Queued by the planner but not executed because ticket lane moves need approval in Initiative settings.",
                    "payload": payload,
                })
                continue
            try:
                result = execute_initiative_action(
                    action_type=action_type,
                    description=str(action.get("description") or ""),
                    payload=payload,
                    draft=str(action.get("draft") or ""),
                    settings=settings,
                )
                if isinstance(result, dict):
                    results.append({"action_type": action_type, **result})
                else:
                    results.append({"action_type": action_type, "success": True, "message": str(result)})
            except Exception as exc:
                results.append({
                    "action_type": action_type,
                    "success": False,
                    "message": str(exc),
                    "payload": payload,
                })
        return results

    async def _arun(
        self,
        action: str = "scan",
        candidate_id: Optional[int] = None,
        limit: int = 12,
        source: str = "",
        project_id: Optional[int] = None,
        board_id: Optional[int] = None,
        approved_by: str = "user",
        backend_id: str = "",
        model: str = "",
        format: str = "summary",
        **kwargs,
    ) -> str:
        return self._run(
            action=action,
            candidate_id=candidate_id,
            limit=limit,
            source=source,
            project_id=project_id,
            board_id=board_id,
            approved_by=approved_by,
            backend_id=backend_id,
            model=model,
            format=format,
            **kwargs,
        )
