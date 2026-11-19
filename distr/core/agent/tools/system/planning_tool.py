"""Read-only access to a project's Plan from chat.

The chat agent previously only saw planning through a trimmed prompt summary.
This tool returns the persisted plan inventory and the Interfaces / Data /
Documents sections for an explicit project, or the project the current chat
or Development thread is scoped to.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Optional

from langchain.tools import BaseTool
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class PlanningInput(BaseModel):
    action: str = Field(default="summary", description="summary (plan inventory) or sections (Interfaces, Data, Documents).")
    project_id: Optional[int] = Field(default=None, description="Project id. Defaults to the chat/thread project.")
    project_name: str = Field(default="", description="Project name (case-insensitive) when the id is unknown.")
    max_chars: int = Field(default=4000, ge=500, le=12000)


class PlanningTool(BaseTool):
    name: str = "planning"
    description: str = (
        "Read a project's Plan (Development > Planning): the persisted plan inventory, or its "
        "Interfaces, Data and Documents sections. Read-only. Use for 'what's in the plan', "
        "'what does the Snuza plan say', 'plan status', or before turning a plan into tickets."
    )
    args_schema: type[BaseModel] = PlanningInput

    def _resolve_project_id(self, project_id: Optional[int], project_name: str, chat_id: Optional[int]) -> tuple[Optional[int], str]:
        from distr.core.db import get_session
        from distr.core.db.projects import Project

        with get_session() as session:
            if project_id:
                project = session.get(Project, int(project_id))
                return (int(project.id), project.name or "") if project else (None, "")
            if project_name:
                project = (
                    session.query(Project)
                    .filter(Project.deleted_at.is_(None))
                    .filter(Project.name.ilike(f"%{project_name.strip()}%"))
                    .first()
                )
                return (int(project.id), project.name or "") if project else (None, "")
        if chat_id:
            # Same scope rule as the chat's Development context: the thread's
            # orchestrator scope first, then the chat (or parent chat) project.
            from distr.core.developer_context import DeveloperContextAssembler

            scope = DeveloperContextAssembler()._fetch_active_thread_scope(int(chat_id))
            scoped = scope.get("project_id") if isinstance(scope, dict) else None
            if scoped:
                with get_session() as session:
                    project = session.get(Project, int(scoped))
                    if project is not None:
                        return int(project.id), project.name or ""
        return None, ""

    def _run(
        self,
        action: str = "summary",
        project_id: Optional[int] = None,
        project_name: str = "",
        max_chars: int = 4000,
        chat_id: Optional[int] = None,
        **_: Any,
    ) -> str:
        if chat_id is None:
            try:
                from distr.core.chat import ChatService

                chat_id = ChatService.get_current_chat_id()
            except Exception:
                chat_id = None
        resolved_id, name = self._resolve_project_id(project_id, project_name, chat_id)
        if resolved_id is None:
            return json.dumps({
                "ok": False,
                "error": "No project given and this chat is not linked to one. Pass project_id or project_name.",
            })
        normalized = str(action or "summary").strip().lower()
        if normalized == "sections":
            from distr.core.planning.sections import plan_sections, plan_sections_prompt_text

            text = plan_sections_prompt_text(plan_sections(resolved_id))
        elif normalized == "summary":
            from distr.core.planning.context import planning_prompt_text, planning_snapshot

            text = planning_prompt_text(planning_snapshot(resolved_id), max_chars=int(max_chars))
        else:
            return json.dumps({"ok": False, "error": "action must be summary or sections"})
        return json.dumps({
            "ok": True,
            "project_id": resolved_id,
            "project": name,
            "action": normalized,
            "plan": (text or "No persisted plan for this project yet.")[: int(max_chars)],
        }, ensure_ascii=False)

    async def _arun(self, **kwargs: Any) -> str:
        import asyncio

        return await asyncio.to_thread(self._run, **kwargs)
