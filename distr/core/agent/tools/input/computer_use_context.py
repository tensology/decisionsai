"""
Computer-use context tool.

Provides explicit read/clear operations for shared computer-use memory so
agent and subagent flows can coordinate step-by-step UI actions.
"""

import json

from langchain_core.tools import BaseTool

from distr.core.agent.services.computer_use_context import (
    clear_context,
    get_context_snapshot,
)


class ComputerUseContextTool(BaseTool):
    name: str = "computer_use_context"
    description: str = (
        "Read or clear shared computer-use context from recent screen observations, "
        "located targets, and executed actions. "
        "Use action='get' (default) to inspect latest state, or action='clear' to reset it."
    )

    def _run(self, action: str = "get", **kwargs) -> str:
        normalized = (action or "get").strip().lower()
        if normalized == "clear":
            clear_context()
            return "Computer-use context cleared."
        snapshot = get_context_snapshot()
        return json.dumps(snapshot, ensure_ascii=True)

    async def _arun(self, action: str = "get", **kwargs) -> str:
        return self._run(action=action, **kwargs)
