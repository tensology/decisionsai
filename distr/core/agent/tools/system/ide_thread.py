"""Unified Codex/Cursor thread list, read, status, prompt, and amend tool."""

from __future__ import annotations

from langchain.tools import BaseTool
from pydantic import BaseModel, Field


class IdeThreadInput(BaseModel):
    action: str = Field(
        description=(
            "One of: list, read, status, prompt, amend. "
            "list=enumerate threads; read=load transcript/events; status=current phase; "
            "prompt=send a new instruction; amend=continue an existing thread with a follow-up."
        )
    )
    surface: str = Field(
        default="auto",
        description="codex, cursor, or auto (infer from project/query).",
    )
    instruction: str = Field(
        default="",
        description="Prompt text for action=prompt.",
    )
    amendment: str = Field(
        default="",
        description="Follow-up text for action=amend (steer/continue existing thread).",
    )
    thread_id: str = Field(
        default="",
        description="External Codex thread id or Cursor chat id to target.",
    )
    session_id: int = Field(
        default=0,
        description="Decisions IDE bridge session id (mainly for Cursor read/status/amend).",
    )
    query: str = Field(
        default="",
        description="Hint to match a Codex thread when thread_id is unknown.",
    )
    project: str = Field(
        default="",
        description="Project name hint for scoping list/read/prompt.",
    )
    project_id: int = Field(
        default=0,
        description="Decisions project id when known.",
    )
    cwd: str = Field(
        default="",
        description="Project folder path when known.",
    )
    limit: int = Field(default=12, ge=1, le=30)
    limit_messages: int = Field(default=12, ge=1, le=30)
    model: str = Field(default="", description="Optional model override for prompt/amend.")
    new_thread: bool = Field(
        default=False,
        description="When true, start a fresh thread instead of resuming.",
    )


class IdeThreadTool(BaseTool):
    name: str = "ide_thread"
    description: str = (
        "List, read, check status, prompt, or amend Codex and Cursor IDE threads. "
        "Use for questions like what Codex/Cursor is doing, read the latest IDE response, "
        "send work to an existing thread, or continue with a follow-up. "
        "Codex reads local rollout transcripts; Cursor reads local agent-transcripts JSONL plus Decisions IDE bridge sessions and CLI output."
    )
    args_schema: type[BaseModel] = IdeThreadInput

    def _run(
        self,
        action: str = "list",
        surface: str = "auto",
        instruction: str = "",
        amendment: str = "",
        thread_id: str = "",
        session_id: int = 0,
        query: str = "",
        project: str = "",
        project_id: int = 0,
        cwd: str = "",
        limit: int = 12,
        limit_messages: int = 12,
        model: str = "",
        new_thread: bool = False,
        **kwargs,
    ) -> str:
        from distr.core.ide_threads import format_ide_thread_result, ide_thread_action

        result = ide_thread_action(
            action=action,
            surface=surface,
            instruction=instruction,
            amendment=amendment,
            thread_id=thread_id,
            session_id=session_id or None,
            query=query,
            project=project,
            project_id=project_id or None,
            cwd=cwd,
            limit=limit,
            limit_messages=limit_messages,
            model=model,
            new_thread=new_thread,
        )
        return format_ide_thread_result(result)

    async def _arun(self, **kwargs) -> str:
        return self._run(**kwargs)
