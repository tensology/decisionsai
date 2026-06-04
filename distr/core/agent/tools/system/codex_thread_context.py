"""Tool for loading local Codex conversation context."""

from __future__ import annotations

import json

from langchain.tools import BaseTool
from pydantic import BaseModel, Field


class CodexThreadContextInput(BaseModel):
    query: str = Field(
        default="",
        description="Conversation hint, user phrase, title fragment, or work topic to match against local Codex threads.",
    )
    project: str = Field(
        default="",
        description="Optional project name or workspace hint to narrow the Codex thread search.",
    )
    thread_id: str = Field(
        default="",
        description="Optional Codex thread id or id prefix when the user named a specific thread.",
    )
    limit_messages: int = Field(
        default=12,
        ge=1,
        le=30,
        description="Maximum recent user/assistant messages to include from the matching transcript.",
    )
    format: str = Field(
        default="summary",
        description="summary for voice-first text with reference details, json for structured context.",
    )


class CodexThreadContextTool(BaseTool):
    name: str = "codex_thread_context"
    description: str = (
        "Load a relevant local Codex conversation/thread transcript from recorded desktop Codex state. "
        "Use when the user asks to work with a Codex conversation, bring a Codex thread into the current chat, "
        "summarize what happened in Codex, turn Codex work into a ticket or plan, or use Codex/Cursor agent "
        "history instead of asking the user to paste it."
    )
    args_schema: type[BaseModel] = CodexThreadContextInput

    def _run(
        self,
        query: str = "",
        project: str = "",
        thread_id: str = "",
        limit_messages: int = 12,
        format: str = "summary",
        **kwargs,
    ) -> str:
        from distr.core.external_agent_context import (
            build_codex_thread_context,
            format_codex_thread_context_for_prompt,
        )

        context = build_codex_thread_context(
            query=query,
            project=project,
            thread_id=thread_id,
            limit_messages=limit_messages,
        )
        if (format or "").strip().lower() == "json":
            return json.dumps(context, ensure_ascii=False, indent=2)
        return format_codex_thread_context_for_prompt(context)

    async def _arun(
        self,
        query: str = "",
        project: str = "",
        thread_id: str = "",
        limit_messages: int = 12,
        format: str = "summary",
        **kwargs,
    ) -> str:
        return self._run(
            query=query,
            project=project,
            thread_id=thread_id,
            limit_messages=limit_messages,
            format=format,
            **kwargs,
        )
