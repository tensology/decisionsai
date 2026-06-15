"""Tool for inspecting the shared developer workflow context."""

from __future__ import annotations

from langchain.tools import BaseTool
from pydantic import BaseModel, Field


class DeveloperContextInput(BaseModel):
    user_request: str = Field(
        default="",
        description="Optional current user request to include skill recommendations.",
    )
    format: str = Field(
        default="summary",
        description="summary for compact prompt-style text, json for structured context.",
    )


class DeveloperContextTool(BaseTool):
    name: str = "developer_context"
    description: str = (
        "Inspect the current developer workflow context: active project, active board, "
        "current tickets, ticket board notes, workflow runs, and recommended skills. Use before creating "
        "tickets, starting workflows, delegating project work, or diagnosing why the "
        "agent chose the wrong board/project/workflow."
    )
    args_schema: type[BaseModel] = DeveloperContextInput

    def _run(self, user_request: str = "", format: str = "summary", **kwargs) -> str:
        from distr.core.developer_context import build_developer_context

        context = build_developer_context(user_request=user_request)
        if (format or "").strip().lower() == "json":
            import json

            return json.dumps(context.to_dict(), ensure_ascii=False, indent=2)
        return context.to_prompt_text(max_chars=4000)

    async def _arun(self, user_request: str = "", format: str = "summary", **kwargs) -> str:
        return self._run(user_request=user_request, format=format, **kwargs)
