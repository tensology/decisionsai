"""
Workflow tools for the agent — CRUD and execution for AutoWorkflow definitions.
"""

import json
import logging
from typing import Any, Optional, Type

from distr.core.agent.tool_voice_format import voice_then_reference
from langchain.tools import BaseTool
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# Lightweight in-process workflow context memory for ambiguous follow-ups
# like "continue", "that workflow", or "what's running".
_last_workflow_context: dict[str, Optional[int]] = {
    "workflow_id": None,
    "run_id": None,
}


def _remember_workflow_context(
    workflow_id: Optional[int] = None,
    run_id: Optional[int] = None,
) -> None:
    if workflow_id is not None:
        _last_workflow_context["workflow_id"] = int(workflow_id)
    if run_id is not None:
        _last_workflow_context["run_id"] = int(run_id)


def _get_remembered_workflow_id() -> Optional[int]:
    return _last_workflow_context.get("workflow_id")


def _get_remembered_run_id() -> Optional[int]:
    return _last_workflow_context.get("run_id")


# Spoken-first summaries for TTS: avoid brackets, snake_case action types, and digit-heavy lists.

_CARDINAL_WORDS = (
    "zero",
    "one",
    "two",
    "three",
    "four",
    "five",
    "six",
    "seven",
    "eight",
    "nine",
    "ten",
    "eleven",
    "twelve",
    "thirteen",
    "fourteen",
    "fifteen",
    "sixteen",
    "seventeen",
    "eighteen",
    "nineteen",
    "twenty",
)


def _cardinal_word(n: int) -> str:
    """Small integers as words for friendlier tool text (aligns with TTS guidance)."""
    if n < 0:
        return "several"
    if n < len(_CARDINAL_WORDS):
        return _CARDINAL_WORDS[n]
    return str(n)


def _humanize_action_phrase(action_type: str) -> str:
    return {
        "agent_instruction": "hands-on agent work",
        "playwright": "browser automation",
        "execute_code": "scripted code",
        "run_command": "a shell command",
        "send_to_project_cli": "your project terminal",
        "http_request": "a web request",
        "play_recording": "replaying a recording",
        "set_variable": "updating workflow variables",
    }.get((action_type or "").strip().lower(), "automation")


def _tts_workflow_summary(wf: dict) -> str:
    """Plain-language paragraph(s) safe to read aloud — no technical markup."""
    name = (wf.get("name") or "Workflow").strip()
    steps = wf.get("steps") or []
    n = len(steps)
    if n == 0:
        return (
            f"I opened {name}. There are no steps yet — you can add them anytime "
            "from the Workflows page."
        )

    parts = [f"I opened {name}. It has {_cardinal_word(n)} steps."]
    if n <= 4:
        flow_bits = []
        for s in steps:
            nm = (s.get("name") or "step").strip()
            kind = _humanize_action_phrase(str(s.get("action_type") or ""))
            flow_bits.append(f"{nm}, which uses {kind}")
        parts.append("In order, those are: " + "; ".join(flow_bits) + ".")
    else:
        first = (steps[0].get("name") or "the first step").strip()
        mid = (steps[n // 2].get("name") or "a middle step").strip()
        last = (steps[-1].get("name") or "the last step").strip()
        parts.append(
            f"They start with {first}, include work such as {mid}, and finish with {last}. "
            "If you want every title read out, say so and I will walk through them slowly."
        )
    parts.append("It is all saved — open Workflows whenever you want to fine-tune wording.")
    return " ".join(parts)


def _reference_workflow_block(wf: dict, *, header: str = "") -> str:
    """Technical dump after REFERENCE — model must not read aloud (system prompt)."""
    lines = []
    if header:
        lines.append(header)
    lines.append(f"Workflow: {wf.get('name', 'Untitled')} (ID {wf.get('id', '?')})")
    if wf.get("description"):
        lines.append(f"Description: {wf['description']}")
    steps = wf.get("steps", [])
    if steps:
        lines.append(f"Steps ({len(steps)}):")
        for s in steps:
            action = s.get("action_type", "agent_instruction")
            status = s.get("status", "pending")
            nm = s.get("name", "Unnamed")
            lines.append(f"  {int(s.get('position', 0)) + 1}. {nm} [{action}] — {status}")
            ins = s.get("instruction")
            if ins:
                lines.append(f"     Instruction: {ins[:180]}...")
    runs = wf.get("runs", [])
    if runs:
        lines.append(f"Recent runs ({len(runs)}):")
        for r in runs[:5]:
            lines.append(f"  Run {r['id']}: {r['status']} (started {r.get('started_at', '?')})")
    return "\n".join(lines)


def _tts_list_workflows_summary(workflows: list) -> str:
    n = len(workflows)
    if n == 0:
        return "You do not have any workflows saved yet."
    if n == 1:
        w = workflows[0]
        return (
            f"You have one workflow saved: {w.get('name', 'Untitled')}. "
            "Say if you want me to walk through its steps."
        )
    names = [str(w.get("name") or "Untitled").strip() for w in workflows[:5]]
    tail = ""
    if n > 5:
        tail = f" There are {_cardinal_word(n)} total; showing the first five names."
    joined = ", ".join(names[:-1]) + f", and {names[-1]}" if len(names) > 1 else names[0]
    return f"You have {_cardinal_word(n)} workflows: {joined}.{tail}"


def _reference_list_block(workflows: list) -> str:
    lines = ["Workflows:"]
    for w in workflows:
        sched = ""
        if w.get("schedule_enabled"):
            sched = f" [scheduled: {w.get('schedule_preset', '?')}"
            if w.get("schedule_time"):
                sched += f" at {w['schedule_time']}"
            sched += "]"
        lines.append(f"- ID {w['id']}: {w['name']} ({w['step_count']} steps){sched}")
    return "\n".join(lines)


def _scheduled_schedule_from_workflow(wf: Any) -> dict[str, Any]:
    preset = (getattr(wf, "schedule_preset", "") or "").strip().lower()
    days = (getattr(wf, "schedule_days", "") or "").strip()
    if preset == "once":
        return {
            "kind": "once",
            "run_at": getattr(wf, "schedule_time", "") or "",
            "timezone": getattr(wf, "schedule_timezone", "") or "",
        }
    if preset == "daily":
        return {
            "kind": "daily",
            "time": getattr(wf, "schedule_time", "") or "",
            "timezone": getattr(wf, "schedule_timezone", "") or "",
        }
    if preset == "weekly" and days == "1,2,3,4,5":
        return {
            "kind": "weekdays",
            "time": getattr(wf, "schedule_time", "") or "",
            "timezone": getattr(wf, "schedule_timezone", "") or "",
        }
    return {
        "kind": "weekly",
        "weekday": days or "1",
        "time": getattr(wf, "schedule_time", "") or "",
        "timezone": getattr(wf, "schedule_timezone", "") or "",
    }


def _scheduled_action_from_step(step: Any) -> dict[str, Any]:
    if not step:
        return {"type": "keypress", "key": "enter"}
    if getattr(step, "action_type", "") == "play_recording":
        return {"type": "play_recording", "recording_name": getattr(step, "recording_filename", "") or ""}
    config = {}
    raw_config = getattr(step, "config", None)
    if isinstance(raw_config, dict):
        config = raw_config
    elif raw_config:
        try:
            config = json.loads(raw_config)
        except Exception:
            config = {}
    action = config.get("scheduled_action")
    if isinstance(action, dict) and action.get("type"):
        return action
    return {"type": "type_text", "text": getattr(step, "instruction", "") or ""}


def _next_run_for_scheduled_workflow(workflow_data: dict[str, Any]) -> Any:
    from distr.core.workflow.scheduler import _next_run_from_cron, schedule_to_cron

    cron = schedule_to_cron(
        workflow_data.get("schedule_preset"),
        workflow_data.get("schedule_time"),
        workflow_data.get("schedule_timezone"),
        workflow_data.get("schedule_days"),
    )
    return (
        _next_run_from_cron(
            cron,
            timezone=workflow_data.get("schedule_timezone"),
            allow_current_minute=True,
        )
        if cron
        else None
    )


def _scheduled_action_reference(items: list[dict[str, Any]]) -> str:
    lines = ["Scheduled actions:"]
    for item in items:
        status = "enabled" if item.get("enabled") else "disabled"
        schedule = item.get("schedule") or {}
        if schedule.get("kind") == "once":
            when = f"once at {schedule.get('run_at') or '?'}"
        elif schedule.get("kind") == "weekdays":
            when = f"weekdays at {schedule.get('time') or '?'}"
        elif schedule.get("kind") == "daily":
            when = f"daily at {schedule.get('time') or '?'}"
        else:
            when = f"{schedule.get('weekday') or 'weekly'} at {schedule.get('time') or '?'}"
        lines.append(
            f"- ID {item.get('workflow_id')}: {item.get('title')} — {status}, {when}, "
            f"next_run_at={item.get('next_run_at') or 'none'}"
        )
    return "\n".join(lines)


# --- List workflows ---
class ListWorkflowsInput(BaseModel):
    limit: int = Field(default=20, description="Max workflows to return")
    search: Optional[str] = Field(default=None, description="Search by workflow name")


class ListWorkflowsTool(BaseTool):
    name: str = "list_workflows"
    description: str = (
        "List all workflows from the workflow automation engine. "
        "Use when user asks 'what workflows do I have', 'show my workflows', "
        "'list workflows', 'what workflows are available'. "
        "Returns workflow definitions (templates with steps), NOT run history."
    )
    args_schema: Type[BaseModel] = ListWorkflowsInput

    def _run(self, limit: int = 20, search: Optional[str] = None, **kwargs) -> str:
        try:
            from distr.core.workflow.service import list_workflows
            workflows = list_workflows(limit=limit, search=search)
            if not workflows:
                return voice_then_reference(
                    "You do not have any workflows saved yet.",
                    "No workflows found.",
                )
            spoken = _tts_list_workflows_summary(workflows)
            return voice_then_reference(spoken, _reference_list_block(workflows))
        except Exception as e:
            logger.error("list_workflows failed: %s", e, exc_info=True)
            return f"Error: {str(e)}"

    async def _arun(self, **kwargs) -> str:
        return self._run(**kwargs)



# --- Get workflow details ---
class GetWorkflowInput(BaseModel):
    workflow_id: Optional[int] = Field(default=None, description="Workflow ID to retrieve")
    workflow_name: Optional[str] = Field(default=None, description="Workflow name (fuzzy match) if workflow_id omitted")


class GetWorkflowTool(BaseTool):
    name: str = "get_workflow"
    description: str = (
        "Get details of a specific workflow including all its steps, variables, and recent run history. "
        "Use when user asks 'show me workflow X', 'what steps does Development have', "
        "'show the Development workflow', 'what's in workflow 3'."
    )
    args_schema: Type[BaseModel] = GetWorkflowInput

    def _run(self, workflow_id: Optional[int] = None, workflow_name: Optional[str] = None, **kwargs) -> str:
        try:
            from distr.core.workflow.workflow_resolve import resolve_workflow_id

            resolved_id, err = resolve_workflow_id(workflow_id=workflow_id, workflow_name=workflow_name)
            if err or resolved_id is None:
                return voice_then_reference(
                    "I could not find that workflow.",
                    err or "Workflow not found.",
                )
            workflow_id = resolved_id
            from distr.core.workflow.service import get_workflow
            wf = get_workflow(workflow_id)
            if not wf:
                return voice_then_reference(
                    "I could not find a workflow with that ID.",
                    f"Workflow {workflow_id} not found.",
                )
            _remember_workflow_context(workflow_id=workflow_id)
            spoken = _tts_workflow_summary(wf)
            ref = _reference_workflow_block(wf)
            return voice_then_reference(spoken, ref)
        except Exception as e:
            logger.error("get_workflow failed: %s", e, exc_info=True)
            return f"Error: {str(e)}"

    async def _arun(self, **kwargs) -> str:
        return self._run(**kwargs)


# --- Run a workflow ---
class RunWorkflowInput(BaseModel):
    workflow_id: Optional[int] = Field(default=None, description="Workflow ID to run")
    workflow_name: Optional[str] = Field(default=None, description="Workflow name if workflow_id omitted")
    context: Optional[str] = Field(default=None, description="Optional context to prepend to the first step instruction")


class RunWorkflowTool(BaseTool):
    name: str = "run_workflow"
    description: str = (
        "Start a workflow run. Executes all steps in sequence using the workflow engine. "
        "Use when user asks 'run the Development workflow', 'start workflow X', "
        "'execute that workflow', 'run it'."
    )
    args_schema: Type[BaseModel] = RunWorkflowInput

    def _run(self, workflow_id: Optional[int] = None, workflow_name: Optional[str] = None, context: Optional[str] = None, **kwargs) -> str:
        try:
            from distr.core.workflow.workflow_resolve import resolve_workflow_id

            resolved_id, err = resolve_workflow_id(workflow_id=workflow_id, workflow_name=workflow_name)
            if err or resolved_id is None:
                return f"Failed to start workflow: {err or 'not found'}"
            workflow_id = resolved_id
            from distr.core.workflow.service import start_workflow_run
            result = start_workflow_run(workflow_id, context=context)
            if "error" in result:
                return f"Failed to start workflow: {result['error']}"
            run_id = result.get("run_id")
            _remember_workflow_context(workflow_id=workflow_id, run_id=run_id)
            return f"Workflow run started (run ID {run_id}). Steps are executing in sequence."
        except Exception as e:
            logger.error("run_workflow failed: %s", e, exc_info=True)
            return f"Error: {str(e)}"

    async def _arun(self, **kwargs) -> str:
        return self._run(**kwargs)


# --- Cancel a workflow run ---
class CancelWorkflowRunInput(BaseModel):
    run_id: int = Field(description="Workflow run ID to cancel")


class CancelWorkflowRunTool(BaseTool):
    name: str = "cancel_workflow_run"
    description: str = (
        "Cancel an active workflow run. Stops the current step and shuts down the agent. "
        "Use when user asks 'stop that workflow', 'cancel the run', 'stop the Development workflow'."
    )
    args_schema: Type[BaseModel] = CancelWorkflowRunInput

    def _run(self, run_id: int, **kwargs) -> str:
        try:
            from distr.core.workflow.service import cancel_run
            success = cancel_run(run_id)
            if success:
                _remember_workflow_context(run_id=run_id)
                return f"Workflow run {run_id} cancelled."
            return f"Could not cancel run {run_id} — it may not exist or already finished."
        except Exception as e:
            logger.error("cancel_workflow_run failed: %s", e, exc_info=True)
            return f"Error: {str(e)}"

    async def _arun(self, **kwargs) -> str:
        return self._run(**kwargs)



# --- Get project status (meshes workflow runs + CLI results + ticket board) ---
class GetProjectStatusInput(BaseModel):
    project_name: str = Field(description="Project name to get status for")


class GetProjectStatusTool(BaseTool):
    name: str = "get_project_status"
    description: str = (
        "Get a comprehensive status update on a project by pulling together: "
        "actively running tasks, latest workflow run results, recent pi agent output, "
        "and ticket board status. Shows what is IN PROGRESS right now first. "
        "Use when user asks 'what's the status on X', 'update me on the project', "
        "'what's happening with Tensology', 'how's the project going', "
        "'what's the latest on X', 'is anything running on X'."
    )
    args_schema: Type[BaseModel] = GetProjectStatusInput

    def _run(self, project_name: str, **kwargs) -> str:
        try:
            from distr.core.db import get_session
            from distr.core.db.projects import Project
            from distr.core.db.kanban import KanbanBoard
            from distr.core.db.workflow import (
                AutoWorkflow, AutoWorkflowRun, AutoWorkflowStep,
                AutoWorkflowStepResult,
            )

            sections = []

            with get_session() as db:
                # 1. Find the project
                requested_project = (project_name or "").strip()
                if requested_project:
                    project = db.query(Project).filter(
                        Project.name.ilike(f"%{requested_project}%")
                    ).first()
                else:
                    project = (
                        db.query(Project)
                        .filter(Project.in_use == True)  # noqa: E712 - SQLAlchemy comparison
                        .order_by(Project.modified_date.desc())
                        .first()
                    )
                if not project:
                    return voice_then_reference(
                        "I could not find a project with that name.",
                        f"Project '{project_name}' not found.",
                    )

                project_display_name = project.name or "the active project"
                sections.append(f"Project: {project_display_name}")
                if project.description:
                    sections.append(f"Description: {project.description}")

                # ── ACTIVE RIGHT NOW ──────────────────────────────

                # 2a. CLI tasks currently in progress for this project
                active_cli = (
                    db.query(AutoWorkflow)
                    .filter(
                        AutoWorkflow.workflow_type == "pi_agent",
                        AutoWorkflow.status == "in_progress",
                        AutoWorkflow.name.ilike(f"%{project_display_name}%"),
                    )
                    .order_by(AutoWorkflow.modified_date.desc())
                    .all()
                )

                # 2b. Workflow runs currently running for workflows linked to this project
                active_workflow_runs = []
                # Check ticket boards linked to this project for their default workflow
                boards = (
                    db.query(KanbanBoard)
                    .filter(KanbanBoard.default_project_id == project.id)
                    .all()
                )
                board_wf_ids = set()
                for board in boards:
                    if board.default_workflow_id:
                        board_wf_ids.add(board.default_workflow_id)
                # Also check workflow steps linked to this project
                linked_steps = (
                    db.query(AutoWorkflowStep)
                    .filter(AutoWorkflowStep.linked_project_id == project.id)
                    .all()
                )
                for s in linked_steps:
                    board_wf_ids.add(s.workflow_id)
                # Query running/waiting runs for those workflows
                if board_wf_ids:
                    active_workflow_runs = (
                        db.query(AutoWorkflowRun)
                        .filter(
                            AutoWorkflowRun.workflow_id.in_(board_wf_ids),
                            AutoWorkflowRun.status.in_(["running", "waiting"]),
                        )
                        .order_by(AutoWorkflowRun.started_at.desc())
                        .all()
                    )

                if active_cli or active_workflow_runs:
                    sections.append("\nCURRENTLY RUNNING:")
                    for cs in active_cli:
                        instr = (cs.name or "")[:120]
                        sections.append(f"  • CLI: {instr}")
                        for step in sorted(cs.steps, key=lambda s: s.position):
                            if step.status == "running":
                                sections.append(f"    Step '{step.name}' is executing now")
                    for run in active_workflow_runs:
                        wf = db.query(AutoWorkflow).filter(AutoWorkflow.id == run.workflow_id).first()
                        wf_name = wf.name if wf else f"Workflow {run.workflow_id}"
                        current_step = None
                        if run.current_step_id:
                            current_step = db.query(AutoWorkflowStep).filter(
                                AutoWorkflowStep.id == run.current_step_id
                            ).first()
                        step_info = f" → currently on '{current_step.name}'" if current_step else ""
                        status_label = "WAITING for input" if run.status == "waiting" else "RUNNING"
                        sections.append(f"  • Workflow '{wf_name}': {status_label}{step_info} (run {run.id})")
                else:
                    sections.append("\nNothing actively running right now.")

                # ── LATEST CLI RESULTS ────────────────────────────

                cli_sessions = (
                    db.query(AutoWorkflow)
                    .filter(
                        AutoWorkflow.workflow_type == "pi_agent",
                        AutoWorkflow.name.ilike(f"%{project_display_name}%"),
                        AutoWorkflow.status != "in_progress",
                    )
                    .order_by(AutoWorkflow.modified_date.desc())
                    .limit(3)
                    .all()
                )
                if cli_sessions:
                    sections.append("\n--- Recent CLI Activity ---")
                    for cs in cli_sessions:
                        instr = (cs.name or "")[:120]
                        ts = cs.modified_date.strftime("%Y-%m-%d %H:%M") if cs.modified_date else "?"
                        sections.append(f"  • {instr} [{cs.status}, {ts}]")
                        for step in sorted(cs.steps, key=lambda s: s.position):
                            if step.result:
                                sections.append(f"    Output: {step.result[:300]}")

                # ── LATEST WORKFLOW RUNS ──────────────────────────

                if board_wf_ids:
                    for wf_id in board_wf_ids:
                        wf = db.query(AutoWorkflow).filter(AutoWorkflow.id == wf_id).first()
                        if not wf:
                            continue
                        latest_run = (
                            db.query(AutoWorkflowRun)
                            .filter(
                                AutoWorkflowRun.workflow_id == wf.id,
                                AutoWorkflowRun.status.notin_(["running", "waiting"]),
                            )
                            .order_by(AutoWorkflowRun.started_at.desc())
                            .first()
                        )
                        if latest_run:
                            ts = latest_run.started_at.strftime("%Y-%m-%d %H:%M") if latest_run.started_at else "?"
                            sections.append(f"\n--- Workflow: {wf.name} (last run: {latest_run.status}, {ts}) ---")
                            step_results = (
                                db.query(AutoWorkflowStepResult)
                                .filter(AutoWorkflowStepResult.run_id == latest_run.id)
                                .order_by(AutoWorkflowStepResult.created_at)
                                .all()
                            )
                            if step_results:
                                for sr in step_results:
                                    step_name = sr.step.name if sr.step else f"Step {sr.step_id}"
                                    response_preview = (sr.agent_response or "")[:200]
                                    sections.append(f"  {step_name}: {sr.status}")
                                    if response_preview:
                                        sections.append(f"    Result: {response_preview}")
                            else:
                                for step in sorted(wf.steps, key=lambda s: s.position):
                                    result_preview = (step.result or "")[:200]
                                    sections.append(f"  {step.name}: {step.status}")
                                    if result_preview:
                                        sections.append(f"    Result: {result_preview}")

                # ── KANBAN TICKETS ────────────────────────────────

                has_boards = bool(boards)
                if boards:
                    for board in boards:
                        sections.append(f"\n--- Ticket Board: {board.name} ---")
                        for lane in sorted(board.lanes, key=lambda l: l.position):
                            tickets = sorted(lane.tickets, key=lambda t: t.position)
                            if tickets:
                                sections.append(f"  Lane '{lane.name}': {len(tickets)} ticket(s)")
                                for t in tickets[:5]:
                                    sections.append(f"    - {t.title} [{t.priority}]")

            if len(sections) <= 2:
                sections.append("\nNo recent workflow runs, CLI activity, or ticket board entries found for this project.")

            body = "\n".join(sections)
            running_now = bool(active_cli or active_workflow_runs)
            spoken = (
                f"I pulled status for {project_display_name}. "
                + (
                    "Something is running right now, either on the project terminal or in a workflow."
                    if running_now
                    else "Nothing is actively running at the moment."
                )
            )
            if has_boards:
                spoken += " Ticket boards linked to this project are summarized below."
            return voice_then_reference(spoken.strip(), body)
        except Exception as e:
            logger.error("get_project_status failed: %s", e, exc_info=True)
            return f"Error: {str(e)}"

    async def _arun(self, **kwargs) -> str:
        return self._run(**kwargs)



# --- Add step to a workflow ---
class AddWorkflowStepInput(BaseModel):
    workflow_id: int = Field(description="Workflow ID to add the step to")
    name: str = Field(description="Step name")
    action_type: str = Field(
        default="agent_instruction",
        description=(
            "Step type. Valid values: "
            "agent_instruction (LLM agent executes an instruction), "
            "computer_use (local vision-action loop for mechanical GUI tasks), "
            "execute_code (run Python code), "
            "playwright (browser automation script — takes screenshots and returns results), "
            "play_recording (replay a recorded mouse/keyboard action), "
            "set_variable (set a workflow variable)"
        ),
    )
    instruction: Optional[str] = Field(default=None, description="Instruction text for agent_instruction steps")
    code: Optional[str] = Field(default=None, description="Python/Playwright code for execute_code or playwright steps")
    validation_type: Optional[str] = Field(
        default=None,
        description="Validation after step runs: none, text_match, rule_based, llm_judgment, screenshot_compare, playwright",
    )
    validation_prompt: Optional[str] = Field(default=None, description="What passes validation (prompt for the validator)")
    on_pass_goto_position: Optional[int] = Field(default=None, description="Position of next step on pass (null = end workflow)")
    on_fail_goto_position: Optional[int] = Field(default=None, description="Position of next step on fail (null = end workflow)")
    wait_for_continue: bool = Field(default=False, description="If true, step pauses after execution and waits for user input")


class AddWorkflowStepTool(BaseTool):
    name: str = "add_workflow_step"
    description: str = (
        "Add a step to a workflow. Supports all step types: "
        "agent_instruction (LLM does a task), computer_use (local vision-action GUI loop), execute_code (run Python), "
        "playwright (browser automation with screenshots), play_recording (replay recorded actions), "
        "set_variable. For playwright steps, write the code with screenshots baked in — "
        "use page.screenshot() to capture results for the workflow agent to review. "
        "Use when user says 'add a step to the Development workflow', "
        "'I want a playwright step that checks the homepage', "
        "'add a validation step'."
    )
    args_schema: Type[BaseModel] = AddWorkflowStepInput

    def _run(self, workflow_id: int, name: str, action_type: str = "agent_instruction",
             instruction: Optional[str] = None, code: Optional[str] = None,
             validation_type: Optional[str] = None, validation_prompt: Optional[str] = None,
             on_pass_goto_position: Optional[int] = None, on_fail_goto_position: Optional[int] = None,
             wait_for_continue: bool = False, **kwargs) -> str:
        try:
            from distr.core.workflow.service import add_step, update_step, get_workflow

            step_id = add_step(workflow_id, name=name, action_type=action_type)
            if not step_id:
                return f"Workflow {workflow_id} not found."

            # Configure the step with additional fields
            updates = {}
            if instruction:
                updates["instruction"] = instruction
            if code:
                updates["code"] = code
            if validation_type:
                updates["validation_type"] = validation_type
            if validation_prompt:
                updates["validation_prompt"] = validation_prompt
            if wait_for_continue:
                updates["wait_for_continue"] = True

            # Resolve position-based goto to step IDs
            if on_pass_goto_position is not None or on_fail_goto_position is not None:
                wf = get_workflow(workflow_id)
                if wf:
                    steps = sorted(wf.get("steps", []), key=lambda s: s["position"])
                    pos_to_id = {s["position"]: s["id"] for s in steps}
                    if on_pass_goto_position is not None:
                        updates["on_pass_goto"] = pos_to_id.get(on_pass_goto_position)
                    if on_fail_goto_position is not None:
                        updates["on_fail_goto"] = pos_to_id.get(on_fail_goto_position)

            if updates:
                update_step(step_id, **updates)

            return f"Added step '{name}' (ID {step_id}, type: {action_type}) to workflow {workflow_id}."
        except Exception as e:
            logger.error("add_workflow_step failed: %s", e, exc_info=True)
            return f"Error: {str(e)}"

    async def _arun(self, **kwargs) -> str:
        return self._run(**kwargs)


# --- Update a workflow step ---
class UpdateWorkflowStepInput(BaseModel):
    step_id: int = Field(description="Step ID to update")
    name: Optional[str] = Field(default=None, description="New step name")
    action_type: Optional[str] = Field(default=None, description="New action type")
    instruction: Optional[str] = Field(default=None, description="New instruction text")
    code: Optional[str] = Field(default=None, description="New code for execute_code/playwright steps")
    validation_type: Optional[str] = Field(default=None, description="New validation type")
    validation_prompt: Optional[str] = Field(default=None, description="New validation prompt")
    status: Optional[str] = Field(default=None, description="New status: pending, running, passed, failed, cancelled, waiting")
    wait_for_continue: Optional[bool] = Field(default=None, description="Whether to pause after execution")


class UpdateWorkflowStepTool(BaseTool):
    name: str = "update_workflow_step"
    description: str = (
        "Update a step in a workflow. Can change the name, type, instruction, code, "
        "validation, or status. Use when user says 'change step 2 to playwright', "
        "'update the instruction on the Build step', 'mark the Validate step as passed'."
    )
    args_schema: Type[BaseModel] = UpdateWorkflowStepInput

    def _run(self, step_id: int, **kwargs) -> str:
        try:
            from distr.core.workflow.service import update_step
            updates = {k: v for k, v in kwargs.items() if v is not None}
            if not updates:
                return "No updates provided."
            if not update_step(step_id, **updates):
                return f"Step {step_id} not found."
            return f"Updated step {step_id}: {', '.join(f'{k}={v!r}' for k, v in updates.items())}"
        except Exception as e:
            logger.error("update_workflow_step failed: %s", e, exc_info=True)
            return f"Error: {str(e)}"

    async def _arun(self, **kwargs) -> str:
        return self._run(**kwargs)


# --- Generate a workflow from description ---
class GenerateWorkflowInput(BaseModel):
    description: str = Field(description="Natural language description of the workflow to generate")


class GenerateWorkflowTool(BaseTool):
    name: str = "generate_workflow"
    description: str = (
        "Generate a complete workflow from a natural language description. "
        "The LLM will create the workflow with appropriate steps, action types, "
        "and routing. Use when user says 'create a workflow that does X', "
        "'build me a workflow for Y', 'I want a workflow with the following steps'. "
        "Valid step types: agent_instruction, computer_use, execute_code, playwright, "
        "play_recording, set_variable."
    )
    args_schema: Type[BaseModel] = GenerateWorkflowInput

    def _run(self, description: str, **kwargs) -> str:
        try:
            import json
            import re
            from distr.core.workflow_engine.code_generator import CodeGeneratorService
            from distr.core.workflow.service import import_workflow, get_workflow

            prompt = (
                "You are a workflow generator. Given the user's description, produce a JSON object "
                "representing a workflow compatible with the following schema:\n"
                "{\n"
                '  "name": "Workflow Name",\n'
                '  "description": "...",\n'
                '  "steps": [\n'
                "    {\n"
                '      "position": 0,\n'
                '      "name": "Step 1",\n'
                '      "action_type": "agent_instruction",\n'
                '      "instruction": "...",\n'
                '      "validation_type": "none",\n'
                '      "validation_prompt": "",\n'
                '      "routing_mode": "static",\n'
                '      "on_pass_goto_position": 1,\n'
                '      "on_fail_goto_position": null,\n'
                '      "wait_for_continue": false\n'
                "    }\n"
                "  ],\n"
                '  "context_rules": ""\n'
                "}\n\n"
                "Valid action_type values: agent_instruction, computer_use, execute_code, playwright, "
                "play_recording, set_variable.\n\n"
                "For playwright steps, include complete browser automation code in a 'code' field "
                "that uses page.screenshot() to capture visual results.\n\n"
                "The last step's on_pass_goto_position should be null (end workflow).\n"
                "Return ONLY valid JSON, no markdown fences or explanations.\n\n"
                f"User description:\n{description}"
            )

            svc = CodeGeneratorService()
            raw = svc._call_coding_llm(prompt)

            cleaned = raw.strip()
            if cleaned.startswith("```"):
                cleaned = re.sub(r"^```[\w]*\s*\n?", "", cleaned)
                cleaned = re.sub(r"\n?```\s*$", "", cleaned)
                cleaned = cleaned.strip()

            workflow_data = json.loads(cleaned)
            wf_id = import_workflow(workflow_data)
            wf = get_workflow(wf_id)
            if not wf:
                return "The workflow was generated but could not be reloaded."
            _remember_workflow_context(workflow_id=wf_id)
            spoken = (
                _tts_workflow_summary(wf)
                + " This one was just created from your description."
            )
            ref = _reference_workflow_block(
                wf,
                header=f"New workflow ID {wf_id} (imported from generate_workflow).",
            )
            return voice_then_reference(spoken, ref)
        except json.JSONDecodeError as je:
            return f"Failed to parse generated workflow: {je}"
        except Exception as e:
            logger.error("generate_workflow failed: %s", e, exc_info=True)
            return f"Error: {str(e)}"

    async def _arun(self, **kwargs) -> str:
        return self._run(**kwargs)


class SpawnTicketWorkflowInput(BaseModel):
    ticket_id: int = Field(description="Kanban ticket ID to spawn a workflow for")
    preset_slug: Optional[str] = Field(
        default=None,
        description="Loop preset slug (e.g. decisionsai-dogfood-ticket). Inferred from ticket when omitted.",
    )
    start_run: bool = Field(default=True, description="Start the workflow run immediately after spawning")
    force: bool = Field(default=False, description="Replace an existing linked workflow when true")


class SpawnTicketWorkflowTool(BaseTool):
    name: str = "spawn_ticket_workflow"
    description: str = (
        "Create a workflow for a ticket that does not have one yet (or force a fresh one), "
        "apply a loop preset or reuse steps, link the ticket, and start the run. "
        "Use when a ticket needs execution but no workflow is linked — do this silently; "
        "tell the user about progress on the feature, not about workflow IDs."
    )
    args_schema: Type[BaseModel] = SpawnTicketWorkflowInput

    def _run(
        self,
        ticket_id: int,
        preset_slug: Optional[str] = None,
        start_run: bool = True,
        force: bool = False,
        **kwargs,
    ) -> str:
        try:
            from distr.core.workflow.spawn_workflow import spawn_workflow_for_ticket
            from distr.core.db import get_session
            from distr.core.db.kanban import KanbanTicket

            title = ""
            with get_session() as db:
                ticket = db.query(KanbanTicket).filter(KanbanTicket.id == int(ticket_id)).first()
                if ticket:
                    title = (ticket.title or "").strip()

            result = spawn_workflow_for_ticket(
                int(ticket_id),
                preset_slug=preset_slug,
                start_run=bool(start_run),
                skip_human_checkpoints=True,
                force=bool(force),
                dispatch_async=True,
            )
            if not result.get("success"):
                return f"Could not start work on the ticket: {result.get('error') or 'unknown error'}"

            wf_id = int(result["workflow_id"])
            _remember_workflow_context(workflow_id=wf_id, run_id=result.get("run_id"))
            subject = title or f"ticket {ticket_id}"
            if result.get("reused"):
                spoken = f"I picked up {subject} on the existing workflow and started the run."
            else:
                spoken = f"I set up the work loop for {subject} and started on it."
            ref = (
                f"Spawned workflow #{wf_id} for ticket #{ticket_id} "
                f"(preset={result.get('preset_slug')}, steps={result.get('step_count')}, "
                f"run_id={result.get('run_id')}, reused={result.get('reused')})."
            )
            return voice_then_reference(spoken, ref)
        except Exception as e:
            logger.error("spawn_ticket_workflow failed: %s", e, exc_info=True)
            return f"Error: {str(e)}"

    async def _arun(self, **kwargs) -> str:
        return self._run(**kwargs)


class CreateStepRunnerInput(BaseModel):
    instruction: str = Field(description="Natural language task or automation to turn into a workflow")


class CreateStepRunnerTool(BaseTool):
    name: str = "create_step_runner"
    description: str = (
        "Create a workflow/step runner automation from a natural language instruction. "
        "Use when the user says 'create a step runner', 'create an automation', "
        "'build a workflow', or asks to break a task into executable workflow steps. "
        "This is a compatibility alias for generate_workflow."
    )
    args_schema: Type[BaseModel] = CreateStepRunnerInput

    def _run(self, instruction: str = "", **kwargs) -> str:
        description = instruction or kwargs.get("description") or kwargs.get("text") or ""
        if not description:
            return "Error: No automation instruction provided."
        return GenerateWorkflowTool()._run(description=description)

    async def _arun(self, **kwargs) -> str:
        return self._run(**kwargs)


class ScheduledActionInput(BaseModel):
    action: str = Field(
        default="list",
        description="Operation: preview, create, list, cancel, disable, enable, or reschedule.",
    )
    workflow_id: Optional[int] = Field(
        default=None,
        description="Scheduled action workflow ID for cancel, disable, enable, or reschedule.",
    )
    title: Optional[str] = Field(default=None, description="Scheduled action title for create or rename.")
    schedule: Optional[dict[str, Any]] = Field(
        default=None,
        description=(
            "Schedule object. Examples: {'kind':'once','run_at':'2026-06-02T13:05:00+02:00'}, "
            "{'kind':'daily','time':'09:00'}, {'kind':'weekdays','time':'08:30'}, "
            "{'kind':'weekly','weekday':'monday','time':'10:15'}."
        ),
    )
    desktop_action: Optional[dict[str, Any]] = Field(
        default=None,
        description=(
            "Desktop action object. Examples: {'type':'keypress','key':'enter'}, "
            "{'type':'type_text','text':'hello','press_enter':true}, "
            "{'type':'open_app','app_name':'Chrome'}, {'type':'play_recording','recording_name':'login-flow'}."
        ),
    )
    target_context: Optional[dict[str, Any]] = Field(
        default=None,
        description="Optional target context: {'app_name':'Chrome','window_title_hint':'Inbox'}.",
    )
    safety: Optional[dict[str, Any]] = Field(
        default=None,
        description="Optional safety flags: {'require_app_in_foreground':true,'bring_app_to_front':true}.",
    )
    limit: int = Field(default=20, description="Maximum scheduled actions to list.")


class ScheduledActionTool(BaseTool):
    name: str = "scheduled_action"
    description: str = (
        "Create, preview, list, cancel, disable, enable, and reschedule simple scheduled desktop actions. "
        "Use for voice commands like 'schedule Chrome to open every weekday at eight thirty', "
        "'list my scheduled actions', 'cancel scheduled action 12', 'disable action 12', "
        "or 'reschedule action 12 for weekdays at ten fifteen'. "
        "Supported desktop actions: keypress, type_text, open_app, and play_recording."
    )
    args_schema: Type[BaseModel] = ScheduledActionInput

    def _list_payload(self, limit: int = 20) -> list[dict[str, Any]]:
        from distr.core.db.workflow import AutoWorkflow
        from distr.core.workflow import service as workflow_service

        with workflow_service.get_session() as db:
            rows = (
                db.query(AutoWorkflow)
                .filter(AutoWorkflow.workflow_type == "scheduled")
                .order_by(AutoWorkflow.modified_date.desc())
                .limit(max(1, min(int(limit or 20), 100)))
                .all()
            )
            payload = []
            for wf in rows:
                step = sorted(list(wf.steps or []), key=lambda s: s.position or 0)[0] if wf.steps else None
                payload.append({
                    "workflow_id": wf.id,
                    "title": wf.name or "Scheduled action",
                    "description": wf.description or "",
                    "enabled": bool(wf.schedule_enabled),
                    "schedule": _scheduled_schedule_from_workflow(wf),
                    "action": _scheduled_action_from_step(step),
                    "next_run_at": wf.next_run_at.isoformat() if wf.next_run_at else None,
                    "last_run_at": wf.last_run_at.isoformat() if wf.last_run_at else None,
                })
            return payload

    def _create(self, spec: dict[str, Any]) -> tuple[int, str]:
        from distr.core.db.workflow import AutoWorkflow, AutoWorkflowStep
        from distr.core.harness.scheduled_actions import compile_scheduled_action_workflow
        from distr.core.workflow import service as workflow_service

        compiled = compile_scheduled_action_workflow(spec)
        workflow_data = compiled["workflow"]
        step_data = compiled["steps"][0]
        next_run_at = _next_run_for_scheduled_workflow(workflow_data)
        with workflow_service.get_session() as db:
            wf = AutoWorkflow(
                name=workflow_data["name"],
                description=workflow_data.get("description", ""),
                status=workflow_data.get("status", "active"),
                workflow_type=workflow_data.get("workflow_type", "scheduled"),
                schedule_enabled=bool(workflow_data.get("schedule_enabled", True)),
                schedule_preset=workflow_data.get("schedule_preset"),
                schedule_time=workflow_data.get("schedule_time"),
                schedule_days=workflow_data.get("schedule_days"),
                schedule_timezone=workflow_data.get("schedule_timezone"),
                next_run_at=next_run_at,
            )
            db.add(wf)
            db.flush()
            db.add(AutoWorkflowStep(
                workflow_id=wf.id,
                position=int(step_data.get("position") or 0),
                name=step_data.get("name") or workflow_data["name"],
                action_type=step_data.get("action_type") or "computer_use",
                step_type=step_data.get("step_type") or step_data.get("action_type") or "computer_use",
                instruction=step_data.get("instruction") or "",
                config=json.dumps(step_data.get("config") or {}),
                validation_type=step_data.get("validation_type") or "none",
                recording_filename=step_data.get("recording_filename"),
            ))
            db.commit()
            workflow_id = int(wf.id)
        return workflow_id, compiled["preview"]

    def _resolve_workflow_id(self, workflow_id: Optional[int] = None, title: Optional[str] = None) -> Optional[dict[str, Any]]:
        from distr.core.db.workflow import AutoWorkflow
        from distr.core.workflow import service as workflow_service

        title_query = (title or "").strip()
        remembered_id = _get_remembered_workflow_id()
        with workflow_service.get_session() as db:
            query = db.query(AutoWorkflow).filter(AutoWorkflow.workflow_type == "scheduled")
            wf = None
            if workflow_id is not None:
                wf = query.filter(AutoWorkflow.id == int(workflow_id)).first()
            elif title_query:
                wf = (
                    query.filter(AutoWorkflow.name.ilike(f"%{title_query}%"))
                    .order_by(AutoWorkflow.modified_date.desc(), AutoWorkflow.id.desc())
                    .first()
                )
            elif remembered_id is not None:
                wf = query.filter(AutoWorkflow.id == int(remembered_id)).first()
            if not wf:
                return None
            return {"workflow_id": int(wf.id), "title": wf.name or "Scheduled action"}

    def _update_existing(
        self,
        workflow_id: int,
        *,
        title: Optional[str] = None,
        schedule: Optional[dict[str, Any]] = None,
        desktop_action: Optional[dict[str, Any]] = None,
        target_context: Optional[dict[str, Any]] = None,
        safety: Optional[dict[str, Any]] = None,
        enabled: Optional[bool] = None,
    ) -> Optional[dict[str, Any]]:
        from distr.core.db.workflow import AutoWorkflow
        from distr.core.harness.scheduled_actions import compile_scheduled_action_workflow
        from distr.core.workflow import service as workflow_service

        with workflow_service.get_session() as db:
            wf = db.query(AutoWorkflow).filter(AutoWorkflow.id == workflow_id).first()
            if not wf or wf.workflow_type != "scheduled":
                return None
            step = sorted(list(wf.steps or []), key=lambda s: s.position or 0)[0] if wf.steps else None
            spec = {
                "title": title if title is not None else (wf.name or "Scheduled action"),
                "schedule": schedule if schedule is not None else _scheduled_schedule_from_workflow(wf),
                "action": desktop_action if desktop_action is not None else _scheduled_action_from_step(step),
                "target_context": target_context or {},
                "safety": safety or {},
            }
            compiled = compile_scheduled_action_workflow(spec)
            workflow_data = compiled["workflow"]
            step_data = compiled["steps"][0]
            wf.name = workflow_data["name"]
            wf.description = workflow_data.get("description", "")
            wf.status = workflow_data.get("status", wf.status or "active")
            if enabled is not None:
                wf.schedule_enabled = bool(enabled)
            else:
                wf.schedule_enabled = bool(workflow_data.get("schedule_enabled", True))
            wf.schedule_preset = workflow_data.get("schedule_preset")
            wf.schedule_time = workflow_data.get("schedule_time")
            wf.schedule_days = workflow_data.get("schedule_days")
            wf.schedule_timezone = workflow_data.get("schedule_timezone")
            wf.next_run_at = _next_run_for_scheduled_workflow(workflow_data) if wf.schedule_enabled else None
            if step:
                step.name = step_data.get("name") or wf.name
                step.action_type = step_data.get("action_type") or "computer_use"
                step.step_type = step_data.get("step_type") or step.action_type
                step.instruction = step_data.get("instruction") or ""
                step.config = json.dumps(step_data.get("config") or {})
                step.validation_type = step_data.get("validation_type") or "none"
                step.recording_filename = step_data.get("recording_filename")
            db.commit()
            db.refresh(wf)
            return {
                "workflow_id": wf.id,
                "title": wf.name or "Scheduled action",
                "enabled": bool(wf.schedule_enabled),
                "schedule": _scheduled_schedule_from_workflow(wf),
                "next_run_at": wf.next_run_at.isoformat() if wf.next_run_at else None,
                "preview": compiled["preview"],
            }

    def _run(
        self,
        action: str = "list",
        workflow_id: Optional[int] = None,
        title: Optional[str] = None,
        schedule: Optional[dict[str, Any]] = None,
        desktop_action: Optional[dict[str, Any]] = None,
        target_context: Optional[dict[str, Any]] = None,
        safety: Optional[dict[str, Any]] = None,
        limit: int = 20,
        **kwargs,
    ) -> str:
        try:
            operation = (action or "list").strip().lower()
            if operation in {"preview", "create"}:
                spec = {
                    "title": title or "Scheduled action",
                    "schedule": schedule or {},
                    "action": desktop_action or kwargs.get("desktop_action") or kwargs.get("scheduled_action") or {},
                    "target_context": target_context or {},
                    "safety": safety or {},
                }
                if operation == "preview":
                    from distr.core.harness.scheduled_actions import preview_scheduled_action
                    preview = preview_scheduled_action(spec)
                    return voice_then_reference(
                        f"Preview: {preview}",
                        f"Scheduled action preview:\n{json.dumps(spec, indent=2, sort_keys=True)}",
                    )
                new_id, preview = self._create(spec)
                _remember_workflow_context(workflow_id=new_id)
                return voice_then_reference(
                    f"Scheduled action {title or 'Scheduled action'} is saved. {preview}",
                    f"Created scheduled action workflow ID {new_id}.\nPreview: {preview}",
                )

            if operation == "list":
                items = self._list_payload(limit=limit)
                if not items:
                    return voice_then_reference(
                        "You do not have any scheduled desktop actions saved yet.",
                        "No scheduled actions found.",
                    )
                names = [str(item.get("title") or "Scheduled action") for item in items[:5]]
                joined = ", ".join(names[:-1]) + f", and {names[-1]}" if len(names) > 1 else names[0]
                return voice_then_reference(
                    f"You have {_cardinal_word(len(items))} scheduled desktop actions: {joined}.",
                    _scheduled_action_reference(items),
                )

            if operation == "cancel":
                resolved = self._resolve_workflow_id(workflow_id=workflow_id, title=title)
                if not resolved:
                    return "Error: I could not find that scheduled action to cancel."
                from distr.core.workflow import service as workflow_service
                resolved_id = int(resolved["workflow_id"])
                if workflow_service.delete_workflow(resolved_id):
                    return f"Scheduled action {resolved.get('title') or resolved_id} cancelled."
                return f"Scheduled action {resolved.get('title') or resolved_id} was not found."

            if operation in {"disable", "enable", "reschedule"}:
                resolved = self._resolve_workflow_id(workflow_id=workflow_id, title=title)
                if not resolved:
                    return f"Error: I could not find that scheduled action to {operation}."
                resolved_id = int(resolved["workflow_id"])
                enabled = False if operation == "disable" else True if operation == "enable" else None
                updated = self._update_existing(
                    resolved_id,
                    title=title,
                    schedule=schedule,
                    desktop_action=desktop_action,
                    target_context=target_context,
                    safety=safety,
                    enabled=enabled,
                )
                if not updated:
                    return f"Scheduled action {resolved.get('title') or resolved_id} was not found."
                verb = "rescheduled" if operation == "reschedule" else ("enabled" if updated["enabled"] else "disabled")
                return voice_then_reference(
                    f"Scheduled action {updated.get('title') or resolved.get('title') or resolved_id} {verb}.",
                    f"Scheduled action {resolved_id} {verb}.\n{json.dumps(updated, indent=2, sort_keys=True)}",
                )

            return "Error: action must be preview, create, list, cancel, disable, enable, or reschedule."
        except Exception as e:
            logger.error("scheduled_action failed: %s", e, exc_info=True)
            return f"Error: {str(e)}"

    async def _arun(self, **kwargs) -> str:
        return self._run(**kwargs)


class VisualBaselineInput(BaseModel):
    action: str = Field(default="list", description="Operation: create, list, get, or readiness.")
    name: Optional[str] = Field(default=None, description="Baseline set name for create or get.")
    baseline_id: Optional[int] = Field(default=None, description="Baseline set ID for get.")
    board_id: Optional[int] = Field(default=None, description="Optional board scope.")
    project_id: Optional[int] = Field(default=None, description="Optional project scope.")
    description: str = Field(default="", description="Optional baseline description.")
    version: str = Field(default="v1", description="Baseline version label.")
    screens: Optional[list[dict[str, Any]]] = Field(
        default=None,
        description=(
            "Reference screens for create. Each requires screen_name and screenshot_path; "
            "optional flow_name, notes, and metadata are preserved."
        ),
    )
    copy_screenshots: bool = Field(
        default=False,
        description="When true, copy existing screenshot files into Hermes-owned visual baseline storage.",
    )
    storage_dir: Optional[str] = Field(
        default=None,
        description="Optional storage directory for copied baseline screenshot files.",
    )
    limit: int = Field(default=20, description="Maximum baselines to list.")


def _reference_visual_baseline_block(baselines: list[dict[str, Any]]) -> str:
    lines = ["Visual baselines:"]
    for baseline in baselines:
        screens = baseline.get("screens") or []
        lines.append(
            f"- ID {baseline.get('id')}: {baseline.get('name')} "
            f"({baseline.get('scope') or 'global'}:{baseline.get('scope_id') or 'all'}, "
            f"{len(screens)} screen(s), {baseline.get('version') or 'v1'})"
        )
        for screen in screens:
            detail = f"  - {screen.get('screen_name')}: {screen.get('screenshot_path')}"
            if screen.get("flow_name"):
                detail += f" [{screen.get('flow_name')}]"
            if screen.get("notes"):
                detail += f" — {screen.get('notes')}"
            lines.append(detail)
    return "\n".join(lines)


def _reference_visual_baseline_readiness_block(readiness: dict[str, Any]) -> str:
    lines = [
        "Visual baseline readiness:",
        f"- Verdict: {readiness.get('verdict')}",
        f"- Baselines checked: {readiness.get('baseline_count', 0)}",
        f"- Screens checked: {readiness.get('screen_count', 0)}",
        f"- Existing screenshot files: {readiness.get('existing_screen_count', 0)}",
        f"- Missing screenshot files: {readiness.get('missing_screen_count', 0)}",
    ]
    missing = readiness.get("missing") or []
    if missing:
        lines.append("Missing screens:")
        for item in missing:
            lines.append(
                f"- {item.get('baseline_name') or 'Visual baseline'} / "
                f"{item.get('screen_name') or 'screen'}: {item.get('screenshot_path') or '(blank path)'}"
            )
    return "\n".join(lines)


class VisualBaselineTool(BaseTool):
    name: str = "visual_baseline"
    description: str = (
        "Create, list, retrieve, or readiness-check Orchestrator visual baseline sets used by UI quality validation. "
        "Use when the user says to save a screenshot as a gold standard, create a visual baseline, "
        "list visual baselines, inspect a baseline's reference screens, or check whether baselines are usable."
    )
    args_schema: Type[BaseModel] = VisualBaselineInput

    def _run(
        self,
        action: str = "list",
        name: Optional[str] = None,
        baseline_id: Optional[int] = None,
        board_id: Optional[int] = None,
        project_id: Optional[int] = None,
        description: str = "",
        version: str = "v1",
        screens: Optional[list[dict[str, Any]]] = None,
        copy_screenshots: bool = False,
        storage_dir: Optional[str] = None,
        limit: int = 20,
        **kwargs,
    ) -> str:
        try:
            operation = (action or "list").strip().lower()
            if operation == "create":
                if not name:
                    return "Error: name is required to create a visual baseline."
                if not screens:
                    return "Error: at least one reference screen is required."
                from distr.core.orchestrator import create_visual_baseline_set, get_visual_baseline_set

                baseline_id = create_visual_baseline_set(
                    name=name,
                    screens=screens,
                    board_id=board_id,
                    project_id=project_id,
                    description=description,
                    version=version,
                    copy_screenshots=copy_screenshots,
                    storage_dir=storage_dir,
                )
                baseline = get_visual_baseline_set(baseline_set_id=baseline_id)
                count = len((baseline or {}).get("screens") or [])
                return voice_then_reference(
                    f"Visual baseline {name} saved with {_cardinal_word(count)} reference screen{'s' if count != 1 else ''}.",
                    _reference_visual_baseline_block([baseline or {"id": baseline_id, "name": name, "screens": screens or []}]),
                )

            if operation == "get":
                from distr.core.orchestrator import get_visual_baseline_set

                baseline = get_visual_baseline_set(
                    baseline_set_id=baseline_id,
                    name=name,
                    board_id=board_id,
                    project_id=project_id,
                )
                if not baseline:
                    return "I could not find that visual baseline."
                screens_count = len(baseline.get("screens") or [])
                return voice_then_reference(
                    f"Visual baseline {baseline.get('name')} has {_cardinal_word(screens_count)} reference screen{'s' if screens_count != 1 else ''}.",
                    _reference_visual_baseline_block([baseline]),
                )

            if operation == "list":
                from distr.core.orchestrator import list_visual_baseline_sets

                baselines = list_visual_baseline_sets(
                    board_id=board_id,
                    project_id=project_id,
                    include_global=True,
                    limit=limit,
                )
                if not baselines:
                    return voice_then_reference(
                        "There are no visual baselines saved yet.",
                        "No visual baselines found.",
                    )
                names = [str(item.get("name") or "Visual baseline") for item in baselines[:5]]
                joined = ", ".join(names[:-1]) + f", and {names[-1]}" if len(names) > 1 else names[0]
                return voice_then_reference(
                    f"You have {_cardinal_word(len(baselines))} visual baseline sets: {joined}.",
                    _reference_visual_baseline_block(baselines),
                )

            if operation in {"readiness", "ready", "audit", "check"}:
                from distr.core.orchestrator import inspect_visual_baseline_readiness

                readiness = inspect_visual_baseline_readiness(
                    baseline_set_id=baseline_id,
                    name=name,
                    board_id=board_id,
                    project_id=project_id,
                    include_global=True,
                    limit=limit,
                )
                if readiness.get("ready"):
                    return voice_then_reference(
                        "Your visual baselines are ready. Every referenced screenshot file exists.",
                        _reference_visual_baseline_readiness_block(readiness),
                    )
                missing_count = int(readiness.get("missing_screen_count") or 0)
                if int(readiness.get("baseline_count") or 0) == 0:
                    voice = "No visual baselines are saved yet, so the UI quality harness is not ready for baseline comparison."
                else:
                    voice = (
                        "Your visual baselines are not ready. "
                        f"{_cardinal_word(missing_count)} referenced screenshot file"
                        f"{' is' if missing_count == 1 else 's are'} missing."
                    )
                return voice_then_reference(voice, _reference_visual_baseline_readiness_block(readiness))

            return "Error: action must be create, list, get, or readiness."
        except Exception as e:
            logger.error("visual_baseline failed: %s", e, exc_info=True)
            return f"Error: {str(e)}"

    async def _arun(self, **kwargs) -> str:
        return self._run(**kwargs)



# --- Reset / stop a workflow ---
class ResetWorkflowInput(BaseModel):
    workflow_id: int = Field(description="Workflow ID to reset")


class ResetWorkflowTool(BaseTool):
    name: str = "reset_workflow"
    description: str = (
        "Stop and reset a workflow. Cancels any active runs, shuts down agents, "
        "and resets ALL step statuses back to pending. "
        "ONLY use when user explicitly refers to a workflow by name or context. "
        "Use when user says 'stop the Development workflow', 'reset that workflow', "
        "'stop all tasks in workflow X', 'cancel everything on that workflow', "
        "'reset the workflow'. Do NOT use for 'stop' without workflow context "
        "(that's media control)."
    )
    args_schema: Type[BaseModel] = ResetWorkflowInput

    def _run(self, workflow_id: int, **kwargs) -> str:
        try:
            from distr.core.workflow.service import reset_workflow_steps
            result = reset_workflow_steps(workflow_id)
            if "error" in result:
                return f"Failed: {result['error']}"
            cancelled = result.get("cancelled_runs", 0)
            reset = result.get("steps_reset", 0)
            parts = [f"Workflow reset. {reset} step(s) set back to pending."]
            if cancelled:
                parts.append(f"{cancelled} active run(s) cancelled.")
            return " ".join(parts)
        except Exception as e:
            logger.error("reset_workflow failed: %s", e, exc_info=True)
            return f"Error: {str(e)}"

    async def _arun(self, **kwargs) -> str:
        return self._run(**kwargs)



# --- Clear workflow history ---
class ClearWorkflowHistoryInput(BaseModel):
    workflow_id: int = Field(description="Workflow ID to clear history for")


class ClearWorkflowHistoryTool(BaseTool):
    name: str = "clear_workflow_history"
    description: str = (
        "Clear all run history and step results for a workflow. "
        "Deletes all past runs, step results, and resets steps to pending. "
        "Use when user says 'clear the history on that workflow', "
        "'clear workflow history', 'wipe the run history', "
        "'delete all runs for the Development workflow'."
    )
    args_schema: Type[BaseModel] = ClearWorkflowHistoryInput

    def _run(self, workflow_id: int, **kwargs) -> str:
        try:
            from distr.core.workflow.service import clear_workflow_history
            result = clear_workflow_history(workflow_id)
            if "error" in result:
                return f"Failed: {result['error']}"
            runs = result.get("deleted_runs", 0)
            results = result.get("deleted_results", 0)
            return f"Cleared workflow history. Deleted {runs} run(s) and {results} step result(s). All steps reset to pending."
        except Exception as e:
            logger.error("clear_workflow_history failed: %s", e, exc_info=True)
            return f"Error: {str(e)}"

    async def _arun(self, **kwargs) -> str:
        return self._run(**kwargs)



# --- Continue a waiting workflow ---
class ContinueWorkflowInput(BaseModel):
    run_id: Optional[int] = Field(default=None, description="Workflow run ID that is in waiting state. If omitted, auto-resolve latest waiting run.")
    workflow_id: Optional[int] = Field(default=None, description="Optional workflow ID to narrow auto-resolution when run_id is omitted.")
    workflow_name: Optional[str] = Field(default=None, description="Workflow name if workflow_id omitted")
    user_input: str = Field(default="", description="Optional user input/feedback to pass to the next step")


class ContinueWorkflowTool(BaseTool):
    name: str = "continue_workflow"
    description: str = (
        "Resume a workflow that is waiting for you. "
        "Use when the user confirms starting a run, wants to continue after a step, "
        "steers the work with new direction, or says to stop. "
        "Pass their exact words as user_input when they steer or refine the plan."
    )
    args_schema: Type[BaseModel] = ContinueWorkflowInput

    def _resolve_run_id(self, run_id: Optional[int], workflow_id: Optional[int], workflow_name: Optional[str] = None) -> tuple[Optional[int], Optional[str]]:
        """Resolve a run_id for continue operations when not provided explicitly."""
        if run_id is not None:
            return int(run_id), None
        try:
            from distr.core.workflow.workflow_resolve import resolve_workflow_id
            from distr.core.workflow.service import get_active_runs

            effective_workflow_id = workflow_id
            if effective_workflow_id is None and workflow_name:
                resolved, err = resolve_workflow_id(workflow_name=workflow_name)
                if err:
                    return None, err
                effective_workflow_id = resolved
            if effective_workflow_id is None:
                effective_workflow_id = _get_remembered_workflow_id()
            candidates = get_active_runs(limit=50, workflow_id=effective_workflow_id)
            waiting_runs = [r for r in candidates if str(r.get("status")) == "waiting"]
            if waiting_runs:
                selected = waiting_runs[0]
                _remember_workflow_context(
                    workflow_id=selected.get("workflow_id"),
                    run_id=selected.get("id"),
                )
                return int(selected["id"]), None
            running_runs = [r for r in candidates if str(r.get("status")) == "running"]
            if running_runs:
                return None, (
                    "I found active workflow runs, but none are waiting for input yet. "
                    "Wait until a step pauses, then continue."
                )
            remembered_run = _get_remembered_run_id()
            if remembered_run is not None:
                active_ids = {int(r["id"]) for r in candidates if r.get("id") is not None}
                if remembered_run in active_ids:
                    return remembered_run, None
                return None, (
                    f"Remembered run #{remembered_run} is not active (running/waiting). "
                    "Pass an explicit run_id or start a new run."
                )
            return None, "No active workflow runs found to continue."
        except Exception as e:
            logger.error("continue_workflow auto-resolve failed: %s", e, exc_info=True)
            return None, f"Failed to resolve an active run automatically: {str(e)}"

    def _run(self, run_id: Optional[int] = None, workflow_id: Optional[int] = None, workflow_name: Optional[str] = None, user_input: str = "", **kwargs) -> str:
        try:
            from distr.core.workflow.dispatcher import continue_waiting_step
            resolved_run_id, resolve_error = self._resolve_run_id(run_id, workflow_id, workflow_name)
            if resolve_error:
                return f"Failed: {resolve_error}"
            result = continue_waiting_step(int(resolved_run_id), user_input)
            if "error" in result:
                return f"Failed: {result['error']}"
            _remember_workflow_context(workflow_id=workflow_id, run_id=resolved_run_id)
            return f"Workflow run {resolved_run_id} resumed with your input. Execution continuing."
        except Exception as e:
            logger.error("continue_workflow failed: %s", e, exc_info=True)
            return f"Error: {str(e)}"

    async def _arun(self, **kwargs) -> str:
        return self._run(**kwargs)


# --- Active workflow run context ---
class GetActiveWorkflowRunsInput(BaseModel):
    workflow_id: Optional[int] = Field(default=None, description="Optional workflow ID filter")
    status: Optional[str] = Field(default=None, description="Optional status filter: running or waiting")
    limit: int = Field(default=20, description="Max active runs to return")


class GetActiveWorkflowRunsTool(BaseTool):
    name: str = "get_active_workflow_runs"
    description: str = (
        "List currently active workflow runs with run IDs, status, and current step context. "
        "Use this first when user asks 'what workflow is running', 'what is waiting', "
        "'which run should we continue', or 'what step is active right now'."
    )
    args_schema: Type[BaseModel] = GetActiveWorkflowRunsInput

    def _run(
        self,
        workflow_id: Optional[int] = None,
        status: Optional[str] = None,
        limit: int = 20,
        **kwargs,
    ) -> str:
        try:
            from distr.core.workflow.service import get_active_runs
            runs = get_active_runs(limit=limit, workflow_id=workflow_id)
            if status:
                status_l = str(status).strip().lower()
                runs = [r for r in runs if str(r.get("status", "")).lower() == status_l]
            if not runs:
                return "No active workflow runs found."
            # Sort waiting first so "continue" follow-ups naturally target the
            # run that needs user input before pure running states.
            runs = sorted(
                runs,
                key=lambda r: (0 if str(r.get("status", "")).lower() == "waiting" else 1, -(int(r.get("id") or 0))),
            )

            lines = [f"Active workflow run count: {len(runs)}"]
            for r in runs:
                wf_name = r.get("workflow_name") or f"Workflow {r.get('workflow_id')}"
                step_name = r.get("current_step_name") or "unknown step"
                run_status = r.get("status") or "unknown"
                run_id = r.get("id")
                board_name = r.get("board_name") or (f"Board {r.get('board_id')}" if r.get("board_id") else None)
                ticket_title = r.get("ticket_title") or (f"Ticket {r.get('ticket_id')}" if r.get("ticket_id") else None)
                project_name = r.get("project_name") or (f"Project {r.get('project_id')}" if r.get("project_id") else None)
                elapsed = int(r.get("elapsed_seconds") or 0)

                line = (
                    f"- run_id={run_id}; workflow='{wf_name}'; status={run_status}; "
                    f"current_step='{step_name}'; elapsed_seconds={elapsed}"
                )
                if board_name:
                    line += f"; board='{board_name}'"
                if ticket_title:
                    line += f"; ticket='{ticket_title}'"
                if project_name:
                    line += f"; project='{project_name}'"
                lines.append(line)

            waiting_run = next((r for r in runs if str(r.get("status", "")).lower() == "waiting"), None)
            if waiting_run:
                lines.append(f"Recommended continue target: run_id={waiting_run.get('id')}")
            # Store latest visible run context to improve follow-up disambiguation.
            top = runs[0]
            _remember_workflow_context(
                workflow_id=top.get("workflow_id"),
                run_id=top.get("id"),
            )
            return "\n".join(lines)
        except Exception as e:
            logger.error("get_active_workflow_runs failed: %s", e, exc_info=True)
            return f"Error: {str(e)}"

    async def _arun(self, **kwargs) -> str:
        return self._run(**kwargs)
