"""Tool for scanning important work signals and dispatching approved project work."""

from __future__ import annotations

import json
from typing import Any, Optional

from langchain.tools import BaseTool
from pydantic import BaseModel, Field


class ProactiveOrchestratorInput(BaseModel):
    action: str = Field(
        default="scan",
        description="scan to find important work, daily_plan to build today's plan from connected intelligence, dispatch to send an approved candidate to the project backend.",
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
        description="Optional board id to narrow the scan.",
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
        "Scan connected work sources and project boards for important actionable work, "
        "build daily plans from email, WhatsApp, tickets, boards, projects, workflows, and IDE context, "
        "match items to projects and recent Codex/Cursor context, ask for approval before dispatch, "
        "and send approved work to the configured project backend."
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
        from distr.core import hermes_proactive

        action_name = (action or "scan").strip().lower()
        if action_name in {"daily_plan", "daily plan", "plan", "day_plan", "morning_brief", "today"}:
            result = self._build_daily_plan_result(format=format)
        elif action_name in {"dispatch", "send", "approve"}:
            if not candidate_id:
                return "Tell me which work candidate to dispatch first."
            result = hermes_proactive.dispatch_proactive_candidate(
                int(candidate_id),
                approved_by=approved_by or "user",
                backend_id=backend_id,
                model=model,
            )
        else:
            result = hermes_proactive.run_proactive_check(
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

    def _build_daily_plan_result(self, *, format: str = "summary") -> dict[str, Any]:
        from distr.core.initiative.context import ContextAssembler
        from distr.core.initiative.planners import generate_planner_markdown, tts_excerpt_from_markdown

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
            "context, workflows, automations, Hermes triage, and long-term memory. "
            "Prioritize what actually needs attention today, separate blocked items, and "
            "mention which source each important item came from."
        )
        markdown, date_info = generate_planner_markdown("day", settings, bundle, instruction)
        spoken = tts_excerpt_from_markdown(markdown, max_len=650) or "I built your plan for today."
        return {
            "success": True,
            "action": "daily_plan",
            "spoken_summary": spoken,
            "markdown": markdown,
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
