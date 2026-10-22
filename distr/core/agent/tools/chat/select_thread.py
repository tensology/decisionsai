"""Oracle tools: select_thread and open_thread (web UI left-panel thread selection).

Logic lives in distr.core.ui_thread_select, shared with the development agent toolset.
"""

from __future__ import annotations

from typing import Optional

from langchain.tools import BaseTool
from pydantic import BaseModel, Field

from distr.core import ui_thread_select as uts

_THREAD_DESC = "'latest' (most recently updated), a numeric thread id like 1798, or words from the thread title"
_SURFACE_DESC = "'development' (default) or 'chat'. Use chat only when the user says chat/conversation."


class ThreadTargetInput(BaseModel):
    thread: str = Field(default="latest", description=_THREAD_DESC)
    surface: Optional[str] = Field(default="development", description=_SURFACE_DESC)


SELECT_DESCRIPTION = (
    "Select and highlight a thread in the Decisions web UI left sidebar (Development threads by default, or Chat). "
    "Use for 'select the latest thread', 'select my most recent thread', 'select the newest Development thread', "
    "'select the thread about X', 'select thread 1798', 'show me the latest thread'. "
    "Pushes a live event to open web UI tabs; if no tab acknowledges within ~1.5s it opens the thread in the "
    "default browser itself. Result says path=selected or path=opened. View only: never loads the thread into "
    "the Oracle's live chat."
)
OPEN_DESCRIPTION = (
    "Open a specific Decisions thread page in the default browser (Development threads by default, or Chat). "
    "Use when the user says 'open' a thread ('open my most recent thread', 'open thread 1798', 'open the thread about X') "
    "or the web UI is not open. Reuses an already-open tab (selects + focuses) instead of opening a duplicate. "
    "When the web UI is already open and the user says 'select', use select_thread."
)


class SelectThreadTool(BaseTool):
    name: str = "select_thread"
    description: str = SELECT_DESCRIPTION
    args_schema: type[BaseModel] = ThreadTargetInput

    def _run(self, thread: str = "latest", surface: Optional[str] = "development", **_: object) -> str:
        return uts.spoken(uts.select_thread(thread, surface))

    async def _arun(self, thread: str = "latest", surface: Optional[str] = "development", **_: object) -> str:
        import asyncio
        return await asyncio.to_thread(self._run, thread, surface)


class OpenThreadTool(BaseTool):
    name: str = "open_thread"
    description: str = OPEN_DESCRIPTION
    args_schema: type[BaseModel] = ThreadTargetInput

    def _run(self, thread: str = "latest", surface: Optional[str] = "development", **_: object) -> str:
        return uts.spoken(uts.open_thread(thread, surface))

    async def _arun(self, thread: str = "latest", surface: Optional[str] = "development", **_: object) -> str:
        import asyncio
        return await asyncio.to_thread(self._run, thread, surface)
