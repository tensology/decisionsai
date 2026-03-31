"""
Workflow tools for the agent — CRUD and execution for AutoWorkflow definitions.

These tools operate on the workflow builder (Step Runner UI) workflows,
NOT the old StepRunnerSession model.
"""

import logging
from typing import Any, Optional, Type

from langchain.tools import BaseTool
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


# --- List workflows ---
class ListWorkflowsInput(BaseModel):
    limit: int = Field(default=20, description="Max workflows to return")
    search: Optional[str] = Field(default=None, description="Search by workflow name")
    status: Optional[str] = Field(default=None, description="Filter by status: draft, active, paused, archived")


class ListWorkflowsTool(BaseTool):
    name: str = "list_workflows"
    description: str = (
        "List all workflows from the Step Runner / Workflow Builder. "
        "Use when user asks 'what workflows do I have', 'show my workflows', "
        "'list workflows', 'what's in the step runner', 'show step runner workflows'. "
        "Returns workflow definitions (templates with steps), NOT run history."
    )
    args_schema: Type[BaseModel] = ListWorkflowsInput

    def _run(self, limit: int = 20, search: Optional[str] = None, status: Optional[str] = None, **kwargs) -> str:
        try:
            from distr.core.workflow.service import list_workflows
            workflows = list_workflows(limit=limit, search=search, status=status)
            if not workflows:
                return "No workflows found."
            lines = []
            for w in workflows:
                sched = ""
                if w.get("schedule_enabled"):
                    sched = f" [scheduled: {w.get('schedule_preset', '?')}"
                    if w.get("schedule_time"):
                        sched += f" at {w['schedule_time']}"
                    sched += "]"
                lines.append(
                    f"- ID {w['id']}: {w['name']} ({w['status']}, {w['step_count']} steps){sched}"
                )
            return "Workflows:\n" + "\n".join(lines)
        except Exception as e:
            logger.error("list_workflows failed: %s", e, exc_info=True)
            return f"Error: {str(e)}"

    async def _arun(self, **kwargs) -> str:
        return self._run(**kwargs)



# --- Get workflow details ---
class GetWorkflowInput(BaseModel):
    workflow_id: int = Field(description="Workflow ID to retrieve")


class GetWorkflowTool(BaseTool):
    name: str = "get_workflow"
    description: str = (
        "Get details of a specific workflow including all its steps, variables, and recent run history. "
        "Use when user asks 'show me workflow X', 'what steps does Development have', "
        "'show the Development workflow', 'what's in workflow 3'."
    )
    args_schema: Type[BaseModel] = GetWorkflowInput

    def _run(self, workflow_id: int, **kwargs) -> str:
        try:
            from distr.core.workflow.service import get_workflow
            wf = get_workflow(workflow_id)
            if not wf:
                return f"Workflow {workflow_id} not found."
            lines = [f"Workflow: {wf['name']} (ID {wf['id']}, {wf['status']})"]
            if wf.get("description"):
                lines.append(f"Description: {wf['description']}")
            steps = wf.get("steps", [])
            if steps:
                lines.append(f"\nSteps ({len(steps)}):")
                for s in steps:
                    action = s.get("action_type", "agent_instruction")
                    status = s.get("status", "pending")
                    name = s.get("name", "Unnamed")
                    lines.append(f"  {s.get('position', 0)+1}. {name} [{action}] — {status}")
                    if s.get("instruction"):
                        lines.append(f"     Instruction: {s['instruction'][:100]}...")
            runs = wf.get("runs", [])
            if runs:
                lines.append(f"\nRecent runs ({len(runs)}):")
                for r in runs[:5]:
                    lines.append(f"  Run {r['id']}: {r['status']} (started {r.get('started_at', '?')})")
            return "\n".join(lines)
        except Exception as e:
            logger.error("get_workflow failed: %s", e, exc_info=True)
            return f"Error: {str(e)}"

    async def _arun(self, **kwargs) -> str:
        return self._run(**kwargs)


# --- Run a workflow ---
class RunWorkflowInput(BaseModel):
    workflow_id: int = Field(description="Workflow ID to run")
    context: Optional[str] = Field(default=None, description="Optional context to prepend to the first step instruction")


class RunWorkflowTool(BaseTool):
    name: str = "run_workflow"
    description: str = (
        "Start a workflow run. Executes all steps in sequence using the workflow engine. "
        "Use when user asks 'run the Development workflow', 'start workflow X', "
        "'execute that workflow', 'run it'."
    )
    args_schema: Type[BaseModel] = RunWorkflowInput

    def _run(self, workflow_id: int, context: Optional[str] = None, **kwargs) -> str:
        try:
            from distr.core.workflow.service import start_workflow_run
            result = start_workflow_run(workflow_id, context=context)
            if "error" in result:
                return f"Failed to start workflow: {result['error']}"
            run_id = result.get("run_id")
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
                return f"Workflow run {run_id} cancelled."
            return f"Could not cancel run {run_id} — it may not exist or already finished."
        except Exception as e:
            logger.error("cancel_workflow_run failed: %s", e, exc_info=True)
            return f"Error: {str(e)}"

    async def _arun(self, **kwargs) -> str:
        return self._run(**kwargs)



# --- Get project status (meshes workflow runs + CLI results + kanban) ---
class GetProjectStatusInput(BaseModel):
    project_name: str = Field(description="Project name to get status for")


class GetProjectStatusTool(BaseTool):
    name: str = "get_project_status"
    description: str = (
        "Get a comprehensive status update on a project by pulling together: "
        "actively running tasks, latest workflow run results, recent Kiro CLI output, "
        "and kanban ticket status. Shows what is IN PROGRESS right now first. "
        "Use when user asks 'what's the status on X', 'update me on the project', "
        "'what's happening with Tensology', 'how's the project going', "
        "'what's the latest on X', 'is anything running on X'."
    )
    args_schema: Type[BaseModel] = GetProjectStatusInput

    def _run(self, project_name: str, **kwargs) -> str:
        try:
            from distr.core.db import get_session
            from distr.core.db.projects import Project
            from distr.core.db.kanban import KanbanBoard, KanbanLane, KanbanTicket
            from distr.core.db.workflow import (
                AutoWorkflow, AutoWorkflowRun, AutoWorkflowStep,
                AutoWorkflowStepResult,
            )
            from distr.core.db.step_runner import StepRunnerSession, StepRunnerStep
            from distr.core.workflow.service import _active_runs, _runs_lock

            sections = []

            with get_session() as db:
                # 1. Find the project
                project = db.query(Project).filter(
                    Project.name.ilike(f"%{project_name}%")
                ).first()
                if not project:
                    return f"Project '{project_name}' not found."

                sections.append(f"Project: {project.name}")
                if project.description:
                    sections.append(f"Description: {project.description}")

                # ── ACTIVE RIGHT NOW ──────────────────────────────

                # 2a. CLI tasks currently in progress for this project
                active_cli = (
                    db.query(StepRunnerSession)
                    .filter(
                        StepRunnerSession.session_type == "kiro_cli",
                        StepRunnerSession.status == "in_progress",
                        StepRunnerSession.instruction.ilike(f"%{project.name}%"),
                    )
                    .order_by(StepRunnerSession.modified_date.desc())
                    .all()
                )

                # 2b. Workflow runs currently running for workflows linked to this project
                active_workflow_runs = []
                # Check kanban boards linked to this project for their default workflow
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
                    sections.append("\n🔴 CURRENTLY RUNNING:")
                    for cs in active_cli:
                        instr = (cs.instruction or "")[:120]
                        sections.append(f"  • CLI: {instr}")
                        for step in sorted(cs.steps, key=lambda s: s.position):
                            if step.status == "running":
                                sections.append(f"    Step '{step.title}' is executing now")
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
                    sections.append("\n✅ Nothing actively running right now.")

                # ── LATEST CLI RESULTS ────────────────────────────

                cli_sessions = (
                    db.query(StepRunnerSession)
                    .filter(
                        StepRunnerSession.session_type == "kiro_cli",
                        StepRunnerSession.instruction.ilike(f"%{project.name}%"),
                        StepRunnerSession.status != "in_progress",
                    )
                    .order_by(StepRunnerSession.modified_date.desc())
                    .limit(3)
                    .all()
                )
                if cli_sessions:
                    sections.append("\n--- Recent CLI Activity ---")
                    for cs in cli_sessions:
                        instr = (cs.instruction or "")[:120]
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

                if boards:
                    for board in boards:
                        sections.append(f"\n--- Kanban: {board.name} ---")
                        for lane in sorted(board.lanes, key=lambda l: l.position):
                            tickets = sorted(lane.tickets, key=lambda t: t.position)
                            if tickets:
                                sections.append(f"  Lane '{lane.name}': {len(tickets)} ticket(s)")
                                for t in tickets[:5]:
                                    sections.append(f"    - {t.title} [{t.priority}]")

            if len(sections) <= 2:
                sections.append("\nNo recent workflow runs, CLI activity, or kanban tickets found for this project.")

            return "\n".join(sections)
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
        "Add a step to a workflow in the Step Runner. Supports all step types: "
        "agent_instruction (LLM does a task), execute_code (run Python), "
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
        "Valid step types: agent_instruction, execute_code, playwright, "
        "play_recording, set_variable."
    )
    args_schema: Type[BaseModel] = GenerateWorkflowInput

    def _run(self, description: str, **kwargs) -> str:
        try:
            import json
            import re
            from distr.core.step_runner.code_generator import CodeGeneratorService
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
                '  "variables": []\n'
                "}\n\n"
                "Valid action_type values: agent_instruction, execute_code, playwright, "
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
            step_count = len(wf.get("steps", [])) if wf else 0
            wf_name = wf.get("name", "Workflow") if wf else "Workflow"
            return f"Generated workflow '{wf_name}' (ID {wf_id}) with {step_count} steps."
        except json.JSONDecodeError as je:
            return f"Failed to parse generated workflow: {je}"
        except Exception as e:
            logger.error("generate_workflow failed: %s", e, exc_info=True)
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
