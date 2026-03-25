"""
Create Step Runner Tool for LangChain.

Creates a Step Runner session by breaking down an instruction into ordered steps.
Supports both one-time and scheduled (recurring) sessions.
The session appears in Settings > Step Runner where the user can view and run steps.
"""

import logging
from typing import Any, Optional

from langchain.tools import BaseTool
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class CreateStepRunnerInput(BaseModel):
    """Input schema for create_step_runner tool."""
    instruction: str = Field(
        description="The task or workflow to break down into steps (e.g. 'Check my calendar every morning and summarize meetings')"
    )
    schedule: Optional[str] = Field(
        default=None,
        description=(
            "Schedule preset or cron expression to make this a recurring session. "
            "Presets: 'hourly', 'daily', 'weekly'. "
            "Cron: e.g. '0 9 * * *' for 9 AM daily. "
            "Omit for a one-time session."
        ),
    )
    schedule_time: Optional[str] = Field(
        default=None,
        description="Time of day in HH:MM 24-hour format (e.g. '07:00' for 7 AM). Used with daily/weekly schedules.",
    )
    schedule_days: Optional[str] = Field(
        default=None,
        description=(
            "Comma-separated day numbers for weekly schedule. "
            "0=Sun, 1=Mon, 2=Tue, 3=Wed, 4=Thu, 5=Fri, 6=Sat. "
            "E.g. '1,3,5' for Mon/Wed/Fri."
        ),
    )
    timezone: Optional[str] = Field(
        default=None,
        description="Timezone for the schedule, e.g. 'America/New_York', 'Europe/London'. Defaults to system timezone if omitted.",
    )


class CreateStepRunnerTool(BaseTool):
    """Tool for creating a Step Runner session with ordered steps."""

    name: str = "create_step_runner"
    description: str = """Create a Step Runner session that breaks down a task into ordered steps.
Supports both one-time and scheduled (recurring) sessions.

Use when the user asks to:
- Create steps for the step runner
- Break down a task into steps
- Schedule a recurring task (e.g. 'check my mail every morning at 7')
- Set up a daily/weekly/hourly automated workflow
- Create a multi-step plan for [task]

For scheduled sessions, provide a schedule parameter:
- schedule='daily', schedule_time='07:00' → runs daily at 7 AM
- schedule='weekly', schedule_time='09:00', schedule_days='1,3,5' → Mon/Wed/Fri at 9 AM
- schedule='hourly' → runs every hour
- schedule='0 */2 * * *' → custom cron, every 2 hours

The session will appear in Settings > Step Runner. The user can view, edit, and run the steps there.
Scheduled sessions run automatically when the desktop app is running.
"""
    args_schema: type[BaseModel] = CreateStepRunnerInput
    chat_manager: Any = Field(default=None, exclude=True)

    def __init__(self, chat_manager=None, **data):
        super().__init__(**data)
        if chat_manager:
            self.chat_manager = chat_manager

    def _run(
        self,
        instruction: str = "",
        schedule: Optional[str] = None,
        schedule_time: Optional[str] = None,
        schedule_days: Optional[str] = None,
        timezone: Optional[str] = None,
        **kwargs,
    ) -> str:
        """Create a Step Runner session from the instruction, optionally with a schedule."""
        try:
            from distr.core.step_runner.service import (
                plan_session,
                create_scheduled_session,
                get_session_with_steps,
            )

            inst = (instruction or kwargs.get("instruction") or "").strip()
            if not inst:
                return "Please provide an instruction to break down into steps (e.g. 'Check my calendar and summarize today's meetings')."

            chat_id = None
            if self.chat_manager and hasattr(self.chat_manager, "current_chat_id"):
                chat_id = getattr(self.chat_manager, "current_chat_id", None)

            # Pick kwargs from explicit args or fallback to **kwargs
            sched = schedule or kwargs.get("schedule")
            sched_time = schedule_time or kwargs.get("schedule_time")
            sched_days = schedule_days or kwargs.get("schedule_days")
            tz = timezone or kwargs.get("timezone")

            if sched:
                session_id = create_scheduled_session(
                    instruction=inst,
                    schedule=sched,
                    chat_id=chat_id,
                    schedule_time=sched_time,
                    timezone=tz,
                    schedule_days=sched_days,
                )
            else:
                session_id = plan_session(inst, chat_id=chat_id)

            if not session_id:
                return "Failed to create the step runner session. Please try again."

            data = get_session_with_steps(int(session_id))
            steps = data.get("steps", [])
            count = len(steps)
            session_type = data.get("session_type", "instruction")

            summary = f"Created a Step Runner session (ID {session_id}) with {count} step(s). "
            if count > 0:
                summary += "Steps: " + "; ".join(
                    f"{i+1}. {s.get('title', '')}" for i, s in enumerate(steps[:5])
                )
                if count > 5:
                    summary += f" ... and {count - 5} more."

            if session_type == "scheduled":
                sched_desc = data.get("schedule", sched) or "custom"
                time_desc = data.get("schedule_time", sched_time) or ""
                next_run = data.get("next_run_at", "")
                summary += f"\nSchedule: {sched_desc}"
                if time_desc:
                    summary += f" at {time_desc}"
                if tz:
                    summary += f" ({tz})"
                if next_run:
                    summary += f"\nNext run: {next_run}"
                summary += "\nThe desktop app must be running for scheduled sessions to execute."
            else:
                summary += "\nOpen Settings > Step Runner to view and run them."

            return summary
        except Exception as e:
            logger.error("Error creating step runner session: %s", e, exc_info=True)
            return f"Error creating step runner session: {str(e)}"

    async def _arun(self, instruction: str = "", **kwargs) -> str:
        return self._run(instruction=instruction, **kwargs)
