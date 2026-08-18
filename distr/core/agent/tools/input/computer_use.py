"""First-class Chat tool for the configured autonomous computer-use loop."""

from __future__ import annotations

from typing import Any, Optional

from langchain_core.tools import BaseTool
from pydantic import BaseModel, Field


class ComputerUseInput(BaseModel):
    goal: str = Field(description="The complete visible desktop goal to accomplish")
    max_iterations: int = Field(default=15, ge=1, le=25)
    stuck_threshold: int = Field(default=3, ge=1, le=10)
    escalate_on_ambiguity: bool = Field(default=True)
    screenshot_resize_width: int = Field(default=1280, ge=320, le=4096)


class ComputerUseTool(BaseTool):
    name: str = "computer_use"
    description: str = (
        "Autonomously complete a multi-step desktop task: move/focus/snap windows, open apps, "
        "type into documents, click UI. Prefer this for any goal with more than one GUI step "
        "(e.g. bring Terminal forward and put it on the left, then type). "
        "For a single named-window op, use list_windows / focus_window / set_window_bounds / launch_app. "
        "Use screenshot_analyzer only for a single locate, click, or screen description."
    )
    args_schema: type[BaseModel] = ComputerUseInput

    def __init__(self, chat_manager: Any = None, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        object.__setattr__(self, "_chat_manager", chat_manager)

    def _run(
        self,
        goal: str,
        max_iterations: int = 15,
        stuck_threshold: int = 3,
        escalate_on_ambiguity: bool = True,
        screenshot_resize_width: int = 1280,
        **kwargs: Any,
    ) -> str:
        from distr.core.workflow.dispatcher import StepDispatcher

        config = {
            "goal": goal,
            "max_iterations": max_iterations,
            "stuck_threshold": stuck_threshold,
            "escalate_on_ambiguity": escalate_on_ambiguity,
            "screenshot_resize_width": screenshot_resize_width,
        }
        try:
            chat_id = self._chat_manager.get_current_chat() if self._chat_manager else None
            if chat_id is not None:
                config["_chat_id"] = int(chat_id)
        except Exception:
            pass
        result = StepDispatcher()._run_computer_use(
            {"id": 0, "instruction": goal, "action_type": "computer_use"},
            config,
            run_id=None,
        )
        output = str(result.get("output") or "")
        return output if result.get("passed") else f"Error: {output or 'Computer use did not complete the goal.'}"

    async def _arun(self, **kwargs: Any) -> str:
        return self._run(**kwargs)
