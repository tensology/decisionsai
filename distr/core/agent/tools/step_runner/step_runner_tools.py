"""
Step Runner tools for the agent.

Full CRUD and execution: list, get, delete, update step, add step, remove step, run step, run all.
"""

import json
import logging
from typing import Any, Optional, Type

from langchain.tools import BaseTool
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


# --- List sessions ---
class ListStepRunnerSessionsInput(BaseModel):
    limit: int = Field(default=20, description="Max sessions to return")
    session_type: Optional[str] = Field(default=None, description="Filter: 'instruction', 'scheduled', or 'audit'")
    search: Optional[str] = Field(default=None, description="Search in instruction text")


class ListStepRunnerSessionsTool(BaseTool):
    name: str = "list_step_runner_sessions"
    description: str = (
        "List Step Runner sessions (one-time and scheduled). "
        "Use when user asks 'show my step runner', 'what sessions do I have', "
        "'list my workflows', 'what's scheduled', 'show my scheduled tasks'."
    )
    args_schema: Type[BaseModel] = ListStepRunnerSessionsInput

    def _run(self, limit: int = 20, session_type: Optional[str] = None, search: Optional[str] = None, **kwargs) -> str:
        try:
            from distr.core.step_runner.service import list_sessions
            sessions = list_sessions(limit=limit, session_type=session_type, search=search)
            if not sessions:
                return "No Step Runner sessions found."
            lines = []
            for s in sessions:
                stype = s.get("session_type") or "instruction"
                enabled = s.get("enabled", True)
                info = f"- ID {s['id']}: {s.get('instruction', '')[:60]}... ({stype}, {s.get('status', '')})"
                if stype == "scheduled":
                    sched = s.get("schedule") or "?"
                    sched_time = s.get("schedule_time") or ""
                    next_run = s.get("next_run_at") or ""
                    state = "enabled" if enabled else "disabled"
                    info += f" [schedule: {sched}"
                    if sched_time:
                        info += f" at {sched_time}"
                    info += f", {state}"
                    if next_run:
                        info += f", next: {next_run}"
                    info += "]"
                lines.append(info)
            return "Step Runner sessions:\n" + "\n".join(lines)
        except Exception as e:
            logger.error("list_step_runner_sessions failed: %s", e, exc_info=True)
            return f"Error: {str(e)}"

    async def _arun(self, **kwargs) -> str:
        return self._run(**kwargs)


# --- Get session ---
class GetStepRunnerSessionInput(BaseModel):
    session_id: int = Field(description="Session ID to fetch")


class GetStepRunnerSessionTool(BaseTool):
    name: str = "get_step_runner_session"
    description: str = "Get a Step Runner session with its steps. Use when user asks for details of a specific session."
    args_schema: Type[BaseModel] = GetStepRunnerSessionInput

    def _run(self, session_id: int, **kwargs) -> str:
        try:
            from distr.core.step_runner.service import get_session_with_steps
            data = get_session_with_steps(session_id)
            if not data:
                return f"Session {session_id} not found."
            steps = data.get("steps", [])
            stype = data.get("session_type") or "instruction"
            lines = [
                f"Session {session_id}: {data.get('instruction', '')[:80]}",
                f"Type: {stype} | Status: {data.get('status', '')}",
            ]
            if stype == "scheduled":
                enabled = data.get("enabled", True)
                lines.append(f"Schedule: {data.get('schedule', '?')}")
                if data.get("schedule_time"):
                    lines.append(f"Time: {data['schedule_time']}")
                if data.get("schedule_days"):
                    lines.append(f"Days: {data['schedule_days']}")
                if data.get("timezone"):
                    lines.append(f"Timezone: {data['timezone']}")
                lines.append(f"Enabled: {enabled}")
                if data.get("next_run_at"):
                    lines.append(f"Next run: {data['next_run_at']}")
                if data.get("last_run_at"):
                    lines.append(f"Last run: {data['last_run_at']}")
                runs = data.get("runs", [])
                if runs:
                    lines.append(f"Recent runs: {len(runs)}")
                    for r in runs[:3]:
                        lines.append(f"  - {r.get('started_at', '?')} ({r.get('status', '?')})")
            for i, s in enumerate(steps):
                lines.append(f"  {i+1}. {s.get('title', '')}: {s.get('status', '')} - {s.get('instruction', '')[:50]}...")
            return "\n".join(lines)
        except Exception as e:
            logger.error("get_step_runner_session failed: %s", e, exc_info=True)
            return f"Error: {str(e)}"

    async def _arun(self, **kwargs) -> str:
        return self._run(**kwargs)


# --- Delete session ---
class DeleteStepRunnerSessionInput(BaseModel):
    session_id: int = Field(description="Session ID to delete")


class DeleteStepRunnerSessionTool(BaseTool):
    name: str = "delete_step_runner_session"
    description: str = "Delete a Step Runner session. Use when user says 'delete that session', 'remove session X'."
    args_schema: Type[BaseModel] = DeleteStepRunnerSessionInput
    event_queue: Any = Field(default=None, exclude=True)

    def __init__(self, event_queue=None, **data):
        super().__init__(**data)
        self.event_queue = event_queue

    def _run(self, session_id: int, **kwargs) -> str:
        try:
            from distr.core.step_runner.service import delete_session
            from distr.gui.web.step_runner_events import increment_step_runner_updated
            ok = delete_session(session_id)
            if not ok:
                return f"Session {session_id} not found."
            increment_step_runner_updated()
            if self.event_queue:
                try:
                    self.event_queue.put(("step_runner_updated", {}), block=False)
                except Exception:
                    pass
            return f"Deleted session {session_id}."
        except Exception as e:
            logger.error("delete_step_runner_session failed: %s", e, exc_info=True)
            return f"Error: {str(e)}"

    async def _arun(self, **kwargs) -> str:
        return self._run(**kwargs)


# --- Update step ---
class UpdateStepRunnerStepInput(BaseModel):
    step_id: int = Field(description="Step ID to update")
    status: Optional[str] = Field(default=None, description="New status: pending, approved, running, completed, failed, skipped")
    result: Optional[str] = Field(default=None, description="Result text")
    title: Optional[str] = Field(default=None, description="Step title")
    instruction: Optional[str] = Field(default=None, description="Step instruction")


class UpdateStepRunnerStepTool(BaseTool):
    name: str = "update_step_runner_step"
    description: str = "Update a step (status, result, title, instruction). Use for 'approve step 3', 'mark step 2 completed'."
    args_schema: Type[BaseModel] = UpdateStepRunnerStepInput
    event_queue: Any = Field(default=None, exclude=True)

    def __init__(self, event_queue=None, **data):
        super().__init__(**data)
        self.event_queue = event_queue

    def _run(self, step_id: int, status: Optional[str] = None, result: Optional[str] = None,
             title: Optional[str] = None, instruction: Optional[str] = None, **kwargs) -> str:
        try:
            from distr.core.step_runner.service import update_step_status
            from distr.gui.web.step_runner_events import increment_step_runner_updated
            ok = update_step_status(step_id, status=status, result=result, title=title, instruction=instruction)
            if not ok:
                return f"Step {step_id} not found."
            increment_step_runner_updated()
            if self.event_queue:
                try:
                    self.event_queue.put(("step_runner_updated", {}), block=False)
                except Exception:
                    pass
            return f"Updated step {step_id}."
        except Exception as e:
            logger.error("update_step_runner_step failed: %s", e, exc_info=True)
            return f"Error: {str(e)}"

    async def _arun(self, **kwargs) -> str:
        return self._run(**kwargs)


# --- Add step ---
class AddStepRunnerStepInput(BaseModel):
    session_id: int = Field(description="Session ID to add step to")
    title: str = Field(description="Step title")
    instruction: str = Field(description="What to do for this step")


class AddStepRunnerStepTool(BaseTool):
    name: str = "add_step_runner_step"
    description: str = "Add a step to an existing session. Use when user says 'add a step to session X'."
    args_schema: Type[BaseModel] = AddStepRunnerStepInput
    event_queue: Any = Field(default=None, exclude=True)

    def __init__(self, event_queue=None, **data):
        super().__init__(**data)
        self.event_queue = event_queue

    def _run(self, session_id: int, title: str, instruction: str, **kwargs) -> str:
        try:
            from distr.core.step_runner.service import add_step_to_session
            from distr.gui.web.step_runner_events import increment_step_runner_updated
            step = add_step_to_session(session_id, title=title, instruction=instruction)
            if not step:
                return f"Session {session_id} not found."
            increment_step_runner_updated()
            if self.event_queue:
                try:
                    self.event_queue.put(("step_runner_updated", {}), block=False)
                except Exception:
                    pass
            return f"Added step '{title}' to session {session_id}."
        except Exception as e:
            logger.error("add_step_runner_step failed: %s", e, exc_info=True)
            return f"Error: {str(e)}"

    async def _arun(self, **kwargs) -> str:
        return self._run(**kwargs)


# --- Remove step ---
class RemoveStepRunnerStepInput(BaseModel):
    session_id: int = Field(description="Session ID")
    step_id: int = Field(description="Step ID to remove")


class RemoveStepRunnerStepTool(BaseTool):
    name: str = "remove_step_runner_step"
    description: str = "Remove a step from a session. Use when user says 'remove step X from session Y'."
    args_schema: Type[BaseModel] = RemoveStepRunnerStepInput
    event_queue: Any = Field(default=None, exclude=True)

    def __init__(self, event_queue=None, **data):
        super().__init__(**data)
        self.event_queue = event_queue

    def _run(self, session_id: int, step_id: int, **kwargs) -> str:
        try:
            from distr.core.step_runner.service import remove_step
            from distr.gui.web.step_runner_events import increment_step_runner_updated
            ok = remove_step(session_id, step_id)
            if not ok:
                return f"Step {step_id} not found in session {session_id}."
            increment_step_runner_updated()
            if self.event_queue:
                try:
                    self.event_queue.put(("step_runner_updated", {}), block=False)
                except Exception:
                    pass
            return f"Removed step {step_id} from session {session_id}."
        except Exception as e:
            logger.error("remove_step_runner_step failed: %s", e, exc_info=True)
            return f"Error: {str(e)}"

    async def _arun(self, **kwargs) -> str:
        return self._run(**kwargs)


# --- Run all (requests main app to start orchestration) ---
class RunStepRunnerAllInput(BaseModel):
    session_id: int = Field(description="Session ID to run all steps for")


class RunStepRunnerAllTool(BaseTool):
    name: str = "run_step_runner_all"
    description: str = "Run all steps in a session in sequence. The agent will execute each step. Use when user says 'run all steps', 'execute that session'."
    args_schema: Type[BaseModel] = RunStepRunnerAllInput
    event_queue: Any = Field(default=None, exclude=True)

    def __init__(self, event_queue=None, **data):
        super().__init__(**data)
        self.event_queue = event_queue

    def _run(self, session_id: int, **kwargs) -> str:
        try:
            from distr.core.step_runner.service import get_session_with_steps
            data = get_session_with_steps(session_id)
            if not data:
                return f"Session {session_id} not found."
            steps = data.get("steps", [])
            if not steps:
                return f"Session {session_id} has no steps."
            if not self.event_queue:
                return "Cannot run steps: event queue not available. Use the Step Runner UI to run all."
            steps_data = [
                {
                    "id": s["id"],
                    "title": s.get("title", ""),
                    "instruction": s.get("instruction", ""),
                    "verification": s.get("verification"),
                }
                for s in steps
            ]
            self.event_queue.put(("step_runner_run_all_requested", {
                "session_id": session_id,
                "steps_data": steps_data,
                "session_type": data.get("session_type") or "instruction",
            }), block=False)
            return f"Started running {len(steps)} steps for session {session_id}. Steps will execute in sequence."
        except Exception as e:
            logger.error("run_step_runner_all failed: %s", e, exc_info=True)
            return f"Error: {str(e)}"

    async def _arun(self, **kwargs) -> str:
        return self._run(**kwargs)


# --- Update schedule on an existing session ---
class UpdateScheduleInput(BaseModel):
    session_id: int = Field(description="Session ID to update")
    enabled: Optional[bool] = Field(default=None, description="Enable or disable the scheduled session")
    schedule: Optional[str] = Field(
        default=None,
        description="Schedule preset ('hourly', 'daily', 'weekly') or cron expression (e.g. '0 9 * * *')",
    )
    schedule_time: Optional[str] = Field(
        default=None,
        description="Time of day in HH:MM 24-hour format (e.g. '07:00'). Used with daily/weekly.",
    )
    schedule_days: Optional[str] = Field(
        default=None,
        description="Comma-separated day numbers for weekly. 0=Sun,1=Mon,...,6=Sat. E.g. '1,3,5' for Mon/Wed/Fri.",
    )
    timezone: Optional[str] = Field(
        default=None,
        description="Timezone, e.g. 'America/New_York', 'Europe/London'.",
    )


class UpdateScheduleTool(BaseTool):
    name: str = "update_step_runner_schedule"
    description: str = (
        "Update the schedule or enabled state of a scheduled Step Runner session. "
        "Use when user says 'change the schedule to weekly', 'disable that scheduled task', "
        "'move it to 8 AM', 'pause the morning check', 'enable session 5'."
    )
    args_schema: Type[BaseModel] = UpdateScheduleInput
    event_queue: Any = Field(default=None, exclude=True)

    def __init__(self, event_queue=None, **data):
        super().__init__(**data)
        self.event_queue = event_queue

    def _run(
        self,
        session_id: int,
        enabled: Optional[bool] = None,
        schedule: Optional[str] = None,
        schedule_time: Optional[str] = None,
        schedule_days: Optional[str] = None,
        timezone: Optional[str] = None,
        **kwargs,
    ) -> str:
        try:
            from distr.core.step_runner.service import update_scheduled_session, get_session_with_steps
            from distr.gui.web.step_runner_events import increment_step_runner_updated

            ok = update_scheduled_session(
                session_id,
                enabled=enabled,
                schedule=schedule,
                schedule_time=schedule_time,
                schedule_days=schedule_days,
                timezone=timezone,
            )
            if not ok:
                return f"Session {session_id} not found or is not a scheduled session."

            increment_step_runner_updated()
            if self.event_queue:
                try:
                    self.event_queue.put(("step_runner_updated", {}), block=False)
                except Exception:
                    pass

            # Return updated state
            data = get_session_with_steps(session_id)
            if data:
                parts = [f"Updated session {session_id}."]
                parts.append(f"Schedule: {data.get('schedule', '?')}")
                if data.get("schedule_time"):
                    parts.append(f"Time: {data['schedule_time']}")
                if data.get("schedule_days"):
                    parts.append(f"Days: {data['schedule_days']}")
                if data.get("timezone"):
                    parts.append(f"Timezone: {data['timezone']}")
                parts.append(f"Enabled: {data.get('enabled', True)}")
                if data.get("next_run_at"):
                    parts.append(f"Next run: {data['next_run_at']}")
                return " | ".join(parts)
            return f"Updated session {session_id}."
        except Exception as e:
            logger.error("update_step_runner_schedule failed: %s", e, exc_info=True)
            return f"Error: {str(e)}"

    async def _arun(self, **kwargs) -> str:
        return self._run(**kwargs)
