"""Agent tool: thin work operator over intake / CLI / client Telegram dance."""

from __future__ import annotations

import json
import re
from typing import Any, Optional

from langchain.tools import BaseTool
from pydantic import BaseModel, Field


class WorkOpsInput(BaseModel):
    action: str = Field(
        default="status",
        description=(
            "intake (batch Jira/email onto board + Telegram digest), "
            "run (dispatch ticket(s) to project CLI or workflow), "
            "status (lifecycle status), "
            "draft (show pending client draft), "
            "send (approve pending client send). "
            "Prefer these over inventing a chat-stream CLI."
        ),
    )
    ticket_id: int = Field(default=0, description="Ticket id for run/status/draft/send")
    ticket_ids: list[int] = Field(default_factory=list, description="Optional list for run")
    board_id: int = Field(default=0, description="Optional board for intake")
    text: str = Field(default="", description="Optional free text; may contain ticket numbers")


class WorkOpsTool(BaseTool):
    name: str = "work_ops"
    description: str = (
        "Operate the work loop without a chat-stream CLI: intake connected Jira/email work, "
        "run tickets on the project CLI or workflow, check status, show client drafts, "
        "and approve sending a client message. Telegram remains the approval surface for "
        "Send / Revise / Leave. Use when work is coming in from Gmail, Jira, Trello, or WhatsApp."
    )
    args_schema: type[BaseModel] = WorkOpsInput

    def _run(
        self,
        action: str = "status",
        ticket_id: int = 0,
        ticket_ids: Optional[list[int]] = None,
        board_id: int = 0,
        text: str = "",
        **kwargs: Any,
    ) -> str:
        from distr.core.agent.tool_voice_format import voice_then_reference
        from distr.core.kanban import work_ops

        action_name = (action or "status").strip().lower().replace("-", "_").replace(" ", "_")
        ids = list(ticket_ids or [])
        if ticket_id:
            ids.append(int(ticket_id))
        if not ids and text:
            ids.extend(int(m) for m in re.findall(r"#?(\d{1,7})", text))

        if action_name in {"intake", "scan_intake", "jira_intake", "check_intake"}:
            result = work_ops.work_intake(board_id=board_id or None)
        elif action_name in {"run", "start", "execute"}:
            result = work_ops.work_run(ids)
        elif action_name in {"draft", "show_draft", "client_draft"}:
            if not ids:
                return "Tell me which ticket draft to show."
            result = work_ops.work_draft(ids[0])
        elif action_name in {"send", "send_to_client", "approve_send"}:
            if not ids:
                return "Tell me which ticket to send to the client."
            result = work_ops.work_send(ids[0])
        else:
            result = work_ops.work_status(ids[0] if ids else None)

        spoken = str(result.get("spoken_summary") or "Done.")
        return voice_then_reference(spoken, json.dumps(result, ensure_ascii=False, indent=2, default=str))

    async def _arun(self, **kwargs: Any) -> str:
        return self._run(**kwargs)
