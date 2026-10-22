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
    development_thread_id: int = Field(
        default=0,
        description=(
            "Decisions Development thread id (numeric chat id, e.g. the 'current Development "
            "thread' in context). With prompt/amend, the message is posted to that thread's harness."
        ),
    )


class IdeThreadTool(BaseTool):
    name: str = "ide_thread"
    description: str = (
        "List, read, check status, prompt, or amend Codex and Cursor IDE threads for a project. "
        "Use when the user wants a Cursor or Codex thread created, continued, or checked. "
        "Omit thread_id to continue the locked thread for that IDE and project. "
        "Set new_thread=true only when they want a fresh thread. "
        "This lock is separate from the Development section harness. "
        "To message the user's current Decisions Development thread, use action=amend with "
        "development_thread_id (or surface=auto and no thread_id: it targets the current Development thread). "
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
        development_thread_id: int = 0,
        **kwargs,
    ) -> str:
        from distr.core.ide_threads import format_ide_thread_result, ide_thread_action

        act = str(action or "").strip().lower()
        text = (amendment or instruction or "").strip()
        if int(development_thread_id or 0) and not _is_development_thread(int(development_thread_id)):
            # refusal2 2026-10-22: the model passed the Oracle chat's own id (1789) as a thread.
            import json as _json

            return _json.dumps({
                "success": False,
                "error": (
                    f"development_thread_id={int(development_thread_id)} is not a Development thread "
                    "(it is an Oracle chat or a child turn). Use development_control list_threads to find "
                    "the project's thread_id, or create_thread with project_id."
                ),
            })
        if act in ("prompt", "amend") and text and not new_thread:
            dev_id = int(development_thread_id or 0)
            tid = str(thread_id or "").strip()
            if not dev_id and tid.isdigit() and _is_development_thread(int(tid)):
                dev_id = int(tid)
            if not dev_id and not tid and not session_id and str(surface or "auto").strip().lower() in ("", "auto"):
                dev_id = _current_development_thread_id()
            if dev_id:
                return _post_to_development_thread(dev_id, text)

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
        if (
            act in ("prompt", "amend") and text and not new_thread and not thread_id and not session_id
            and isinstance(result, dict) and not result.get("success")
            and "thread_id is required" in str(result.get("error") or "")
        ):
            dev_id = _current_development_thread_id()
            if dev_id:
                return _post_to_development_thread(dev_id, text)
        return format_ide_thread_result(result)

    async def _arun(self, **kwargs) -> str:
        return self._run(**kwargs)


def _current_development_thread_id() -> int:
    try:
        from distr.core.agent.services.llm.oracle_context import get_current_development_thread

        return int(get_current_development_thread().get("thread_id") or 0)
    except Exception:
        return 0


def _is_development_thread(chat_id: int) -> bool:
    try:
        from distr.core.db import Chat, get_session
        from distr.core.workflow.development_threads import development_thread_metadata

        with get_session() as db:
            chat = db.get(Chat, int(chat_id))
            return bool(chat is not None and chat.parent_id is None and development_thread_metadata(chat))
    except Exception:
        return False


def _post_to_development_thread(chat_id: int, message: str) -> str:
    """Post through the same route the Development UI uses (does not wait for the harness)."""
    import json
    import urllib.error
    import urllib.request

    from distr.core.agent.tools.system.project_tools import _get_internal_api_token_for_web
    from distr.core.web_runtime import get_local_web_base_url

    token = _get_internal_api_token_for_web()
    if not token:
        return "That Development thread request did not work: missing internal API token."
    req = urllib.request.Request(
        f"{get_local_web_base_url()}/api/workflows/studio/tasks/{int(chat_id)}/messages",
        data=json.dumps({"message": message}).encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/json", "X-DecisionsAI-Internal-Token": token},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = json.loads(resp.read().decode("utf-8", errors="replace") or "{}")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:300]
        return f"That Development thread request did not work (HTTP {exc.code}): {detail}"
    except Exception as exc:
        return f"That Development thread request did not work: {exc}"
    turn = body.get("turn_id") if isinstance(body, dict) else None
    return (
        f"Sent to Development thread {int(chat_id)}"
        + (f" (turn {turn})" if turn else "")
        + ". The thread's harness will reply there."
    )
