"""Laya submission tools — let the orchestrator agent hand free text to Laya.

``laya_submit`` calls :func:`distr.core.laya_submissions.submit` in-process:
Laya routes the text to a project, selects or creates a Chat thread, dispatches
the agent turn and streams ``laya.submission.*`` events (thread lists update
live). ``laya_submission_status`` reads :func:`get_submission`.
"""

from __future__ import annotations

import json
import logging
from typing import Any, List, Optional

from langchain.tools import BaseTool
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

_MAX_CHARS = 4000


def _public(sub: Any) -> dict[str, Any]:
    data = sub.to_dict() if hasattr(sub, "to_dict") else dict(sub)
    text = str(data.get("text") or "")
    if len(text) > 300:
        data["text"] = text[:300] + "…"
    return data


def _dump(payload: dict[str, Any]) -> str:
    out = json.dumps(payload, default=str, ensure_ascii=False)
    return out if len(out) <= _MAX_CHARS else out[: _MAX_CHARS - 1] + "…"


class LayaSubmitInput(BaseModel):
    text: str = Field(description="The full request text to hand to Laya.")
    project: Optional[str] = Field(default=None, description="Optional project name or id hint; Laya routes when omitted.")
    thread_id: Optional[int] = Field(default=None, description="Optional existing Chat thread id to continue.")
    new_thread: bool = Field(default=False, description="Force a new thread instead of reusing a recent one.")


class LayaSubmitTool(BaseTool):
    """Submit text to Laya: route to a project/thread and dispatch an agent turn."""

    name: str = "laya_submit"
    description: str = (
        "Hand a request to Laya. Laya picks the project and thread (or creates a new thread), "
        "dispatches the agent turn and streams progress to the UI. Returns the submission id, "
        "project, thread_id and status. Use laya_submission_status with the id to follow up. "
        "Do not use it for questions you can answer directly; do not call it on the current thread."
    )
    args_schema: type[BaseModel] = LayaSubmitInput

    def get_triggers(self) -> List[str]:
        return ["laya", "submit to laya", "hand to laya", "new thread for", "route to project"]

    def _run(
        self,
        text: str = "",
        project: Optional[str] = None,
        thread_id: Optional[int] = None,
        new_thread: bool = False,
        **kwargs: Any,
    ) -> str:
        from distr.core import laya_submissions

        try:
            sub = laya_submissions.submit(
                text, source="orchestrator", project=project or None,
                thread_id=int(thread_id) if thread_id not in (None, "") else None,
                new_thread=bool(new_thread),
            )
        except ValueError as exc:
            return _dump({"ok": False, "error": str(exc)})
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("laya_submit failed: %s", exc)
            return _dump({"ok": False, "error": str(exc)[:300]})
        return _dump({"ok": sub.status != "failed", "submission": _public(sub)})

    async def _arun(self, *args: Any, **kwargs: Any) -> str:
        return self._run(*args, **kwargs)


class LayaSubmissionStatusInput(BaseModel):
    submission_id: str = Field(description="Submission id returned by laya_submit.")


class LayaSubmissionStatusTool(BaseTool):
    """Read the state of a Laya submission (in-memory registry)."""

    name: str = "laya_submission_status"
    description: str = (
        "Get the status of a Laya submission by id: accepted, routed, dispatched, done or failed, "
        "with project, thread_id, progress and the response when done."
    )
    args_schema: type[BaseModel] = LayaSubmissionStatusInput

    def get_triggers(self) -> List[str]:
        return ["laya status", "submission status", "laya submission"]

    def _run(self, submission_id: str = "", **kwargs: Any) -> str:
        from distr.core import laya_submissions

        sub = laya_submissions.get_submission(str(submission_id or "").strip())
        if sub is None:
            return _dump({"ok": False, "error": "submission not found (registry is in-memory; resets on restart)"})
        return _dump({"ok": True, "submission": _public(sub)})

    async def _arun(self, *args: Any, **kwargs: Any) -> str:
        return self._run(*args, **kwargs)
