"""
Workflow Service — CRUD + execution engine for workflows and steps.
Each step is a single action with validation and routing.
"""
import asyncio
import json
import logging
import os
import re
import threading
from collections import defaultdict
from dataclasses import dataclass
from typing import List, Dict, Any, Optional
from datetime import datetime

from sqlalchemy import text, inspect as sa_inspect

from distr.core.db import get_session, Action
from distr.core.db.workflow import (
    AutoWorkflow, AutoWorkflowStep,
    AutoWorkflowVariable, AutoWorkflowRun,
    AutoWorkflowStepResult,
)
from distr.core.workflow_agent import WorkflowAgent
from distr.core.step_runner.agent_bridge import WorkflowAgentBridge

logger = logging.getLogger(__name__)


@dataclass
class _RunContext:
    """Per-run state for the WorkflowAgent lifecycle."""
    run_id: int
    workflow_agent: WorkflowAgent
    event_loop: asyncio.AbstractEventLoop
    thread: threading.Thread
    context_prefix: str = ""  # Optional ticket context for first step


_active_runs: Dict[int, _RunContext] = {}
_runs_lock = threading.Lock()


def _cleanup_run(run_id: int) -> None:
    """Clean up a workflow run's WorkflowAgent and event loop when it reaches terminal status."""
    with _runs_lock:
        ctx = _active_runs.pop(run_id, None)
    if ctx is None:
        return
    try:
        ctx.workflow_agent.shutdown()
    except Exception:
        pass
    try:
        ctx.event_loop.call_soon_threadsafe(ctx.event_loop.stop)
    except Exception:
        pass


def _finalize_terminal_run(run_id: int, workflow_id: int, status: str) -> None:
    """Clean up resources and notify the bridge when a run reaches terminal status.

    Called after the DB commit that sets the run to a terminal status
    (completed, failed, cancelled).
    """
    _cleanup_run(run_id)

    # Build steps_summary from the run's step results
    steps_summary = []
    try:
        with get_session() as db:
            step_results = (
                db.query(AutoWorkflowStepResult)
                .filter(AutoWorkflowStepResult.run_id == run_id)
                .order_by(AutoWorkflowStepResult.created_at)
                .all()
            )
            for sr in step_results:
                step_obj = sr.step
                steps_summary.append({
                    "title": step_obj.name if step_obj else f"Step {sr.step_id}",
                    "status": sr.status,
                    "result": (sr.agent_response or "")[:300],
                })
    except Exception:
        logger.debug("Could not load step results for run %d", run_id)

    run_result = {
        "session_id": workflow_id,
        "run_id": run_id,
        "success": status == "completed",
        "cancelled": status == "cancelled",
        "steps_summary": steps_summary,
    }

    try:
        WorkflowAgentBridge().on_workflow_completed(workflow_id, run_result)
    except Exception:
        logger.error("WorkflowAgentBridge notification failed for run %d", run_id, exc_info=True)


# ── Workflow type validation ──

VALID_WORKFLOW_TYPES = {"manual", "instruction", "scheduled", "audit"}


def validate_workflow_type(workflow_type: str) -> bool:
    """Return True if *workflow_type* is one of the allowed values, False otherwise."""
    return workflow_type in VALID_WORKFLOW_TYPES


def _safe_json_loads(text: Optional[str]) -> Any:
    """Parse a JSON string, returning an empty dict on None or invalid JSON."""
    if not text:
        return {}
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return {}


# ── Migration: StepRunner → AutoWorkflow ──

MIGRATION_MARKER_KEY = "step_runner_to_workflow_v1"

# Global degraded-mode flag.  When True, all AutoWorkflow write operations
# should be blocked (API returns HTTP 503).  Set by migrate_step_runner_data()
# on failure; cleared on successful migration.
_migration_degraded_mode = False


def is_migration_degraded() -> bool:
    """Return True when the app is in degraded mode due to a failed migration."""
    return _migration_degraded_mode


# ── Status mapping helpers ──

_SESSION_STATUS_MAP: Dict[str, str] = {
    "planned": "draft",
    "in_progress": "active",
    # Other statuses pass through unchanged
}

_SESSION_TYPE_MAP: Dict[str, str] = {
    "instruction": "instruction",
    "scheduled": "scheduled",
    # Fallback to 'manual' for unknown types
}

_RUN_STATUS_MAP: Dict[str, str] = {
    "in_progress": "running",
    # Other statuses pass through unchanged
}


def _check_migration_marker(session) -> bool:
    """Return True if the migration marker already exists (migration was done)."""
    try:
        inspector = sa_inspect(session.bind)
        if "_migration_markers" not in inspector.get_table_names():
            return False
        row = session.execute(
            text("SELECT 1 FROM _migration_markers WHERE marker_key = :key"),
            {"key": MIGRATION_MARKER_KEY},
        ).fetchone()
        return row is not None
    except Exception:
        return False


def _write_migration_marker(session) -> None:
    """Write the migration marker row.  Must be called inside the same session
    that committed the migration data so it can be committed together or
    separately after the main transaction succeeds."""
    # Ensure the table exists
    session.execute(text(
        "CREATE TABLE IF NOT EXISTS _migration_markers ("
        "  marker_key VARCHAR PRIMARY KEY,"
        "  migrated_at DATETIME"
        ")"
    ))
    session.execute(
        text("INSERT OR IGNORE INTO _migration_markers (marker_key, migrated_at) VALUES (:key, :ts)"),
        {"key": MIGRATION_MARKER_KEY, "ts": datetime.utcnow()},
    )


def _parse_dt(val) -> Optional[datetime]:
    """Coerce a value from raw SQL into a Python datetime (or None)."""
    if val is None:
        return None
    if isinstance(val, datetime):
        return val
    if isinstance(val, str):
        for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S"):
            try:
                return datetime.strptime(val, fmt)
            except ValueError:
                continue
    return None


def _resequence_positions(steps_by_workflow: Dict[int, list]) -> Dict[int, list]:
    """Re-sequence step positions within each workflow if duplicates are detected.

    Returns the same dict with positions updated in-place on each step dict.
    """
    for wf_id, steps in steps_by_workflow.items():
        positions = [s["position"] for s in steps]
        if len(positions) != len(set(positions)):
            # Duplicates detected — re-sequence starting from 0
            steps.sort(key=lambda s: (s["position"], s["_orig_id"]))
            for idx, s in enumerate(steps):
                s["position"] = idx
    return steps_by_workflow


def migrate_step_runner_data() -> bool:
    """One-time transactional migration from StepRunner tables to AutoWorkflow tables.

    Returns True on success (or if already migrated), False on failure.
    On failure, sets the global degraded-mode flag.
    """
    global _migration_degraded_mode

    with get_session() as session:
        # Idempotency check
        if _check_migration_marker(session):
            logger.info("StepRunner migration: marker found — skipping.")
            _migration_degraded_mode = False
            return True

        # Check if legacy tables exist at all
        inspector = sa_inspect(session.bind)
        existing_tables = inspector.get_table_names()
        if "step_runner_sessions" not in existing_tables:
            logger.info("StepRunner migration: no legacy tables found — writing marker and skipping.")
            _write_migration_marker(session)
            session.commit()
            _migration_degraded_mode = False
            return True

        try:
            # ── 1. Read all legacy sessions ──
            rows = session.execute(text("SELECT * FROM step_runner_sessions")).fetchall()
            columns = session.execute(text("SELECT * FROM step_runner_sessions LIMIT 0")).keys()
            sessions_data = [dict(zip(columns, r)) for r in rows]

            # ── 2. Read all legacy steps ──
            rows = session.execute(text("SELECT * FROM step_runner_steps")).fetchall()
            columns = session.execute(text("SELECT * FROM step_runner_steps LIMIT 0")).keys()
            steps_data = [dict(zip(columns, r)) for r in rows]

            # ── 3. Read all legacy runs ──
            rows = session.execute(text("SELECT * FROM step_runner_runs")).fetchall()
            columns = session.execute(text("SELECT * FROM step_runner_runs LIMIT 0")).keys()
            runs_data = [dict(zip(columns, r)) for r in rows]

            # Build session_id → new workflow_id mapping
            session_id_map: Dict[int, int] = {}

            # ── 4. Insert workflows ──
            for s in sessions_data:
                old_status = s.get("status", "planned")
                mapped_status = _SESSION_STATUS_MAP.get(old_status, old_status)

                old_type = s.get("session_type", "instruction")
                mapped_type = _SESSION_TYPE_MAP.get(old_type, "manual")

                # Convert schedule to schedule_preset + schedule_cron
                schedule_val = s.get("schedule")
                schedule_preset = None
                schedule_cron = None
                if schedule_val:
                    from distr.core.workflow.scheduler import schedule_to_cron
                    schedule_preset = schedule_val
                    schedule_cron = schedule_to_cron(
                        schedule_val,
                        s.get("schedule_time"),
                        s.get("timezone"),
                        s.get("schedule_days"),
                    )

                wf = AutoWorkflow(
                    name=s.get("instruction", "Untitled Workflow")[:200] or "Untitled Workflow",
                    description=s.get("instruction"),
                    status=mapped_status,
                    workflow_type=mapped_type,
                    chat_id=s.get("chat_id"),
                    context_rules=s.get("context_rules"),
                    workflow_input=s.get("workflow_input"),
                    schedule_enabled=bool(s.get("enabled", False)),
                    schedule_preset=schedule_preset,
                    schedule_cron=schedule_cron,
                    schedule_time=s.get("schedule_time"),
                    schedule_days=s.get("schedule_days"),
                    schedule_timezone=s.get("timezone"),
                    next_run_at=_parse_dt(s.get("next_run_at")),
                    last_run_at=_parse_dt(s.get("last_run_at")),
                    created_date=_parse_dt(s.get("created_date")) or datetime.utcnow(),
                    modified_date=_parse_dt(s.get("modified_date")) or datetime.utcnow(),
                )
                session.add(wf)
                session.flush()  # get wf.id
                session_id_map[s["id"]] = wf.id

            # ── 5. Group steps by session and re-sequence if needed ──
            steps_by_session: Dict[int, list] = defaultdict(list)
            for st in steps_data:
                sid = st.get("session_id")
                if sid not in session_id_map:
                    continue  # orphan step — skip
                steps_by_session[sid].append({
                    "_orig_id": st["id"],
                    "session_id": sid,
                    "position": st.get("position", 0),
                    "title": st.get("title", "New Step"),
                    "instruction": st.get("instruction"),
                    "verification": st.get("verification"),
                    "status": st.get("status", "pending"),
                    "result": st.get("result"),
                    "tool_used": st.get("tool_used"),
                    "step_type": st.get("step_type", "run_command"),
                    "config": st.get("config"),
                    "code": st.get("code"),
                    "created_date": _parse_dt(st.get("created_date")) or datetime.utcnow(),
                    "modified_date": _parse_dt(st.get("modified_date")) or datetime.utcnow(),
                })

            _resequence_positions(steps_by_session)

            old_step_id_map: Dict[int, int] = {}  # old step id → new step id
            for sid, step_list in steps_by_session.items():
                wf_id = session_id_map[sid]
                for st in step_list:
                    step = AutoWorkflowStep(
                        workflow_id=wf_id,
                        position=st["position"],
                        name=st["title"],
                        instruction=st["instruction"],
                        verification=st["verification"],
                        status=st["status"],
                        result=st["result"],
                        tool_used=st["tool_used"],
                        step_type=st["step_type"],
                        action_type=st["step_type"],  # mirror step_type into action_type
                        config=st["config"],
                        code=st["code"],
                        created_date=st["created_date"],
                        modified_date=st["modified_date"],
                    )
                    session.add(step)
                    session.flush()
                    old_step_id_map[st["_orig_id"]] = step.id

            # ── 6. Insert runs ──
            for r in runs_data:
                sid = r.get("session_id")
                if sid not in session_id_map:
                    continue  # orphan run — skip

                old_status = r.get("status", "running")
                mapped_status = _RUN_STATUS_MAP.get(old_status, old_status)

                run = AutoWorkflowRun(
                    workflow_id=session_id_map[sid],
                    started_at=_parse_dt(r.get("started_at")) or datetime.utcnow(),
                    completed_at=_parse_dt(r.get("completed_at")),
                    status=mapped_status,
                    step_results=r.get("step_results"),
                )
                session.add(run)

            # ── 7. Write marker and commit ──
            _write_migration_marker(session)
            session.commit()

            logger.info(
                "StepRunner migration: completed — %d sessions, %d steps, %d runs migrated.",
                len(sessions_data), len(steps_data), len(runs_data),
            )
            _migration_degraded_mode = False
            return True

        except Exception:
            session.rollback()
            logger.error(
                "StepRunner migration: FAILED — entering degraded mode. "
                "Migration will retry on next startup.",
                exc_info=True,
            )
            _migration_degraded_mode = True
            return False


# ── LLM Planning ──

PLAN_PROMPT = """Break down this instruction into ordered, executable sub-steps for an automation agent.

You can use capabilities like opening apps/websites, clicking UI elements, typing, taking screenshots, and checking visible state.
For browser/web tasks, the agent has a playwright_browser tool that runs headless Chrome, automatically captures screenshots + browser console logs (errors, warnings, failed network requests), and sends them to a vision LLM for analysis. Use this for navigating websites, filling forms, testing web pages, and validating visual state.

For UI tasks, follow these rules:
- Keep each step atomic (one action per step).
- Separate app launch from navigation from interaction.
- Use precise UI descriptions (button text, location, panel).
- Include verification checks after important transitions when helpful.
- For web/browser verification steps, the playwright_browser tool will provide both a screenshot analysis AND console log data — write verification criteria that reference both visual state and console output when relevant (e.g. "Page shows dashboard AND no console errors").

Instruction:
{instruction}

Respond with a JSON array of steps. Each step must have:
- "title": short label (e.g., "Open browser")
- "instruction": what to do (e.g., "Open Chrome and navigate to example.com")
- Optional "verification": what to verify after the step (e.g., "Page shows login form AND no console errors or failed requests")
- Optional "type": one of "ui", "data", or "verification"

Example format:
[
  {{"title": "Open browser", "instruction": "Open Google Chrome", "type": "ui"}},
  {{"title": "Go to site", "instruction": "Navigate to https://example.com", "verification": "Example homepage is visible AND no console errors", "type": "ui"}}
]

Return ONLY the JSON array, no markdown or explanation."""


def _is_simple_instruction(instruction: str) -> bool:
    """Return True if instruction is simple enough for single-step (no LLM breakdown).

    Only treat as simple if it's very short AND has no multi-step markers.
    Err on the side of breaking down - LLM can always return a single step.
    """
    if not instruction:
        return False
    # Only skip LLM for genuinely trivial one-liners (under 80 chars)
    if len(instruction.strip()) > 80:
        return False
    text = instruction.strip().lower()
    multi_markers = [
        " and then ", " then ", " first ", " second ", " after that ",
        " next ", " finally ", " step 1", " step 2", " 1. ", " 2. ",
        " also ", " additionally ", " afterwards ", " once ", " when done",
        " and reply ", " and send ", " and open ", " and create ", " and check ",
        " and navigate ", " and click ", " and type ", " and save ",
    ]
    return not any(m in text for m in multi_markers)


def _litellm_model(provider: str, model: str, settings: dict) -> str:
    """Map provider + model to litellm model string."""
    if provider == "ollama":
        return f"ollama/{model}" if model else "ollama/llama3.2"
    if provider == "openai":
        return model or "gpt-4o-mini"
    if provider == "anthropic":
        return model or "claude-3-5-sonnet-20241022"
    if provider == "groq":
        return model or "groq/llama-3.1-70b-versatile"
    if provider == "openrouter":
        return model or "openrouter/openai/gpt-4o-mini"
    if provider == "kilocode":
        return model or "kilocode/kilocode"
    if provider == "gemini":
        return model or "gemini/gemini-2.5-flash"
    return f"ollama/{model}" if model else "ollama/llama3.2"


def _call_llm_for_plan(instruction: str) -> Optional[List[Dict[str, str]]]:
    """Call LLM to break down instruction into steps. Returns list of {title, instruction} dicts."""
    from distr.core.settings import load_settings_from_db

    settings = load_settings_from_db()
    provider = (
        (settings.get("conversational_llm_provider") or "").strip()
        or (settings.get("agent_provider") or "").strip()
        or "Ollama"
    ).strip().lower()
    model = (
        (settings.get("conversational_llm_model") or "").strip()
        or (settings.get("agent_model") or "").strip()
        or ""
    )
    if not model and provider == "ollama":
        model = "llama3.2"  # fallback

    prompt = PLAN_PROMPT.format(instruction=instruction)
    messages = [{"role": "user", "content": prompt}]

    try:
        import litellm
        response = litellm.completion(
            model=_litellm_model(provider, model, settings),
            messages=messages,
            max_tokens=2048,
            temperature=0.3,
        )
        content = response.choices[0].message.content.strip()
        # Extract JSON array (handle markdown code blocks)
        content = re.sub(r"^```\w*\s*", "", content)
        content = re.sub(r"\s*```\s*$", "", content)
        parsed = json.loads(content)
        if not isinstance(parsed, list):
            logger.warning("LLM returned non-array: %s", type(parsed))
            return None
        steps = []
        for i, item in enumerate(parsed):
            if isinstance(item, dict):
                title = str(item.get("title") or item.get("label") or f"Step {i + 1}")
                inst = str(item.get("instruction") or item.get("text") or "")
                verification = str(item.get("verification") or "").strip() or None
                step_type = str(item.get("type") or "").strip().lower() or None
                if step_type not in {"ui", "data", "verification"}:
                    step_type = None
                if inst:
                    step = {"title": title, "instruction": inst}
                    if verification:
                        step["verification"] = verification
                    if step_type:
                        step["type"] = step_type
                    steps.append(step)
            elif isinstance(item, str):
                steps.append({"title": f"Step {i + 1}", "instruction": item})
        return steps if steps else None
    except Exception as e:
        logger.error("Workflow plan LLM call failed: %s", e, exc_info=True)
        return None


def plan_workflow(
    instruction: str,
    chat_id: Optional[int] = None,
    workflow_input: Optional[dict] = None,
) -> Optional[int]:
    """Create a Workflow by breaking down the instruction into steps.

    Uses single-step fast path for simple instructions.  Retries LLM once on
    failure.  Returns the created workflow id, or ``None`` on failure.

    If *workflow_input* is provided (a dict matching the WorkflowInput schema),
    it is serialized to JSON and stored on the workflow's ``workflow_input``
    column.
    """
    steps_data = None
    if _is_simple_instruction(instruction):
        steps_data = [{"title": "Step 1", "instruction": instruction.strip()}]
    if not steps_data:
        steps_data = _call_llm_for_plan(instruction)
        if not steps_data:
            steps_data = _call_llm_for_plan(instruction)  # Retry once
    if not steps_data:
        steps_data = [{"title": "Step 1", "instruction": instruction.strip()}]  # Fallback single-step

    # Serialize workflow_input to JSON if provided
    workflow_input_json = None
    if workflow_input is not None:
        try:
            workflow_input_json = json.dumps(workflow_input)
        except (TypeError, ValueError) as exc:
            logger.warning("Failed to serialize workflow_input: %s", exc)

    with get_session() as db:
        wf = AutoWorkflow(
            name=(instruction[:80] + "…") if len(instruction) > 80 else instruction,
            description=instruction,
            status="draft",
            workflow_type="instruction",
            chat_id=chat_id,
            workflow_input=workflow_input_json,
        )
        db.add(wf)
        db.flush()
        for i, s in enumerate(steps_data):
            step = AutoWorkflowStep(
                workflow_id=wf.id,
                position=i,
                name=s.get("title", f"Step {i + 1}"),
                instruction=s.get("instruction", ""),
                verification=s.get("verification"),
                status="pending",
                step_type=s.get("step_type", "run_command"),
                config=s.get("config"),
                code=s.get("code"),
            )
            db.add(step)
        db.commit()
        db.refresh(wf)
        return int(wf.id)


# ── Audit trail ──

def get_or_create_audit_workflow(chat_id: int) -> Optional[int]:
    """Get or create an audit workflow for a chat.

    Returns the workflow id (int) to avoid passing detached ORM objects
    across session boundaries.
    """
    with get_session() as db:
        wf = (
            db.query(AutoWorkflow)
            .filter(
                AutoWorkflow.chat_id == chat_id,
                AutoWorkflow.workflow_type == "audit",
            )
            .order_by(AutoWorkflow.modified_date.desc())
            .first()
        )
        if wf:
            return wf.id
        wf = AutoWorkflow(
            name=f"Audit log for chat {chat_id}",
            description=f"Audit log for chat {chat_id}",
            status="active",
            chat_id=chat_id,
            workflow_type="audit",
        )
        db.add(wf)
        db.commit()
        db.refresh(wf)
        return wf.id


def append_audit_step(
    chat_id: int,
    tool_name: str,
    instruction: str,
    result: str,
    status: str = "completed",
    user_text: str = None,
    routing_path: str = None,
) -> bool:
    """Append a tool execution as a step to the chat's audit workflow.

    Creates the audit workflow if it doesn't exist yet.
    Truncates *instruction* to 500 chars and *result* to 2000 chars.
    Stores *routing_path* in its own field without truncation.
    """
    try:
        workflow_id = get_or_create_audit_workflow(chat_id)
        if not workflow_id:
            return False
        with get_session() as db:
            wf = db.query(AutoWorkflow).filter(AutoWorkflow.id == workflow_id).first()
            if not wf:
                return False
            max_pos = max((st.position for st in wf.steps), default=-1)
            inst = instruction[:500] if instruction else tool_name
            truncated_result = None
            if result:
                truncated_result = (result[:2000] + "...") if len(result) > 2000 else result
            step = AutoWorkflowStep(
                workflow_id=wf.id,
                position=max_pos + 1,
                name=tool_name.replace("_", " ").title(),
                instruction=inst,
                status=status,
                result=truncated_result,
                tool_used=tool_name,
                routing_path=routing_path,
            )
            db.add(step)
            db.commit()
        return True
    except Exception as e:
        logger.warning("append_audit_step failed: %s", e)
        return False


# ── Context Assembly ──

def build_step_context_prompt(
    *,
    step_index: int,
    total_steps: int,
    workflow_description: str = "",
    step_title: str,
    step_instruction: str,
    prior_results: List[Dict[str, str]],
    context_rules: str = "",
    continuation_input: str = "",
    session_instruction: str = "",
) -> str:
    """Build a context-aware prompt for step execution.

    For single-step workflows with no prior results, no context_rules, and no
    continuation input, returns the raw instruction so fast-action detection
    (e.g. 'run action X') works correctly without wrapper text polluting regex
    group captures.

    When ``context_rules`` is non-empty, it is prepended as a ``[CONTEXT AND RULES]``
    section before the step runner header.

    When ``continuation_input`` is non-empty, it is appended as a ``[USER INPUT]``
    section after the main prompt body.

    All ``{{variable}}`` placeholders are resolved via
    ``variable_resolver.resolve_variables()`` before returning.

    ``session_instruction`` is accepted as a backward-compatible alias for
    ``workflow_description``.

    **Validates: Requirements 6.1, 6.2, 6.3, 6.4, 6.5**
    """
    from distr.core.step_runner.variable_resolver import resolve_variables

    # Backward-compatible alias: session_instruction → workflow_description
    if session_instruction and not workflow_description:
        workflow_description = session_instruction

    # Single step, no prior context, no context rules, no continuation:
    # send raw instruction so fast-action detector can cleanly extract
    # action names, commands, etc.
    if (
        total_steps == 1
        and not prior_results
        and not context_rules
        and not continuation_input
    ):
        return resolve_variables(step_instruction, prior_results)

    parts: List[str] = []

    if context_rules:
        parts.append(f"[CONTEXT AND RULES]\n{context_rules}\n")

    lines = [
        f"[STEP RUNNER] Executing step {step_index + 1} of {total_steps}.",
        f"Overall goal: {workflow_description}",
        "",
    ]
    if prior_results:
        lines.append("Previous steps:")
        for item in prior_results[-5:]:
            title = item.get("title") or "Step"
            result = item.get("result") or "Completed."
            lines.append(f"- {title}: {result}")
        lines.append("")
    lines.extend(
        [
            f"Current step: {step_title or f'Step {step_index + 1}'}",
            f"Task: {step_instruction}",
            "",
            "Execute this step. When finished, confirm exactly what you accomplished.",
        ]
    )
    parts.append("\n".join(lines))

    if continuation_input:
        parts.append(f"\n[USER INPUT]\n{continuation_input}")

    prompt = "\n".join(parts)
    return resolve_variables(prompt, prior_results)


# ── LLM step generation & delegation to shared utilities ──


def generate_steps(workflow_id: int, instruction: str) -> List[Dict[str, Any]]:
    """Generate steps for an existing workflow using the LLM planner.

    Breaks *instruction* into ordered steps via ``_call_llm_for_plan()`` (with
    retry and single-step fallback) and appends them to the workflow, replacing
    any existing steps.

    Returns the list of created step dicts, or an empty list on failure.

    **Validates: Requirements 2.2, 3.1, 3.2, 3.3, 3.4, 3.5**
    """
    steps_data = None
    if _is_simple_instruction(instruction):
        steps_data = [{"title": "Step 1", "instruction": instruction.strip()}]
    if not steps_data:
        steps_data = _call_llm_for_plan(instruction)
        if not steps_data:
            steps_data = _call_llm_for_plan(instruction)  # Retry once
    if not steps_data:
        steps_data = [{"title": "Step 1", "instruction": instruction.strip()}]  # Fallback

    created: List[Dict[str, Any]] = []
    with get_session() as db:
        wf = db.query(AutoWorkflow).filter(AutoWorkflow.id == workflow_id).first()
        if not wf:
            return []
        # Remove existing steps so the new plan replaces them
        for old_step in list(wf.steps):
            db.delete(old_step)
        db.flush()
        for i, s in enumerate(steps_data):
            step = AutoWorkflowStep(
                workflow_id=wf.id,
                position=i,
                name=s.get("title", f"Step {i + 1}"),
                instruction=s.get("instruction", ""),
                verification=s.get("verification"),
                status="pending",
                step_type=s.get("step_type", "run_command"),
                config=s.get("config"),
                code=s.get("code"),
            )
            db.add(step)
            db.flush()
            created.append({
                "id": step.id,
                "position": step.position,
                "name": step.name,
                "instruction": step.instruction,
                "verification": step.verification,
                "status": step.status,
                "step_type": step.step_type,
                "config": step.config,
                "code": step.code,
            })
        db.commit()
    return created


def generate_step_code(step_id: int, instruction: str, step_type: str) -> str:
    """Generate code for a step by delegating to ``CodeGeneratorService``.

    Converts *step_type* to a ``StepType`` enum and calls
    ``CodeGeneratorService.generate_code()``.  The generated code is also
    persisted on the step's ``code`` column.

    Raises ``ValueError`` for invalid *step_type* and ``RuntimeError`` when
    the coding LLM is unreachable.

    **Validates: Requirements 2.3**
    """
    from distr.core.step_runner.code_generator import CodeGeneratorService
    from distr.core.step_runner.step_types import StepType

    try:
        stype = StepType(step_type)
    except ValueError:
        raise ValueError(f"Invalid step type: {step_type}")

    code = CodeGeneratorService().generate_code(
        instruction=instruction,
        step_type=stype,
    )

    # Persist generated code on the step
    with get_session() as db:
        step = db.query(AutoWorkflowStep).filter(AutoWorkflowStep.id == step_id).first()
        if step:
            step.code = code
            db.commit()

    return code


def test_step_code(
    step_id: int,
    code: str,
    step_type: str,
    headless: bool = True,
) -> Dict[str, Any]:
    """Test step code by delegating to ``TestLoopService``.

    Executes *code* in an isolated subprocess with auto-fix loop.  Returns a
    dict with ``success``, ``code``, ``attempts``, and ``output`` keys.

    Raises ``ValueError`` for invalid *step_type*.

    **Validates: Requirements 2.4**
    """
    from distr.core.step_runner.test_loop import TestLoopService
    from distr.core.step_runner.step_types import StepType

    try:
        stype = StepType(step_type)
    except ValueError:
        raise ValueError(f"Invalid step type: {step_type}")

    config = {"headless": headless}
    result = TestLoopService().run_test(
        code=code,
        step_type=stype,
        config=config,
    )

    return {
        "success": result.success,
        "code": result.code,
        "attempts": result.attempts,
        "output": result.output,
    }


def validate_step_config(step_type: str, config: dict) -> List[Dict[str, str]]:
    """Validate step configuration by delegating to ``StepValidator``.

    Returns an empty list when the configuration is valid, or a list of
    ``{"field": ..., "message": ...}`` dicts describing validation errors.

    **Validates: Requirements 2.5**
    """
    from distr.core.step_runner.validation import StepValidator

    errors = StepValidator().validate(step_type, config)
    return [{"field": e.field, "message": e.message} for e in errors]


# ── Workflow CRUD ──

def create_workflow(name: str = "Untitled Workflow", description: str = "", workflow_type: str = "manual") -> int:
    if not validate_workflow_type(workflow_type):
        raise ValueError(
            f"Invalid workflow_type '{workflow_type}'. Must be one of: {', '.join(sorted(VALID_WORKFLOW_TYPES))}"
        )
    with get_session() as db:
        wf = AutoWorkflow(name=name, description=description, status="draft", workflow_type=workflow_type)
        db.add(wf)
        db.commit()
        db.refresh(wf)
        return wf.id


def get_workflow(workflow_id: int) -> Optional[Dict[str, Any]]:
    with get_session() as db:
        wf = db.query(AutoWorkflow).filter(AutoWorkflow.id == workflow_id).first()
        if not wf:
            return None
        return _serialize_workflow(wf)


def get_workflow_type(workflow_id: int) -> Optional[str]:
    """Return the workflow_type for a workflow, or None if not found."""
    with get_session() as db:
        wf = db.query(AutoWorkflow.workflow_type).filter(AutoWorkflow.id == workflow_id).first()
        return wf[0] if wf else None


def list_workflows(limit: int = 50, search: Optional[str] = None, status: Optional[str] = None, workflow_type: Optional[str] = None) -> List[Dict[str, Any]]:
    with get_session() as db:
        q = db.query(AutoWorkflow)
        if workflow_type:
            q = q.filter(AutoWorkflow.workflow_type == workflow_type)
        else:
            q = q.filter(AutoWorkflow.workflow_type != 'audit')
        if status:
            q = q.filter(AutoWorkflow.status == status)
        if search and search.strip():
            q = q.filter(AutoWorkflow.name.ilike(f"%{search.strip()}%"))
        rows = q.order_by(AutoWorkflow.modified_date.desc()).limit(limit).all()
        return [
            {
                "id": w.id, "name": w.name,
                "description": (w.description or "")[:200],
                "status": w.status,
                "schedule_enabled": w.schedule_enabled,
                "schedule_preset": w.schedule_preset,
                "schedule_time": w.schedule_time,
                "next_run_at": w.next_run_at.isoformat() if w.next_run_at else None,
                "step_count": len(w.steps),
                "created_date": w.created_date.isoformat() if w.created_date else None,
                "modified_date": w.modified_date.isoformat() if w.modified_date else None,
            }
            for w in rows
        ]


def update_workflow(workflow_id: int, **kwargs) -> bool:
    allowed = {
        "name", "description", "status", "schedule_enabled",
        "schedule_preset", "schedule_cron", "schedule_time",
        "schedule_days", "schedule_timezone", "next_run_at",
        "start_step_position", "workflow_type", "context_rules",
    }
    if "workflow_type" in kwargs and not validate_workflow_type(kwargs["workflow_type"]):
        raise ValueError(
            f"Invalid workflow_type '{kwargs['workflow_type']}'. Must be one of: {', '.join(sorted(VALID_WORKFLOW_TYPES))}"
        )
    with get_session() as db:
        wf = db.query(AutoWorkflow).filter(AutoWorkflow.id == workflow_id).first()
        if not wf:
            return False
        for k, v in kwargs.items():
            if k in allowed:
                setattr(wf, k, v)
        db.commit()
        return True


def delete_workflow(workflow_id: int) -> bool:
    with get_session() as db:
        wf = db.query(AutoWorkflow).filter(AutoWorkflow.id == workflow_id).first()
        if not wf:
            return False
        db.delete(wf)
        db.commit()
        return True


def duplicate_workflow(workflow_id: int) -> Optional[int]:
    with get_session() as db:
        orig = db.query(AutoWorkflow).filter(AutoWorkflow.id == workflow_id).first()
        if not orig:
            return None
        new_wf = AutoWorkflow(
            name=f"{orig.name} (copy)", description=orig.description,
            status="draft", start_step_position=orig.start_step_position,
        )
        db.add(new_wf)
        db.flush()
        for step in sorted(orig.steps, key=lambda s: s.position):
            db.add(AutoWorkflowStep(
                workflow_id=new_wf.id, position=step.position,
                name=step.name, description=step.description,
                action_type=step.action_type, instruction=step.instruction,
                validation_type=step.validation_type,
                validation_prompt=step.validation_prompt,
                screenshot_path=step.screenshot_path,
                routing_mode=step.routing_mode,
                routing_prompt=step.routing_prompt,
                on_pass_goto=step.on_pass_goto, on_fail_goto=step.on_fail_goto,
                wait_before_next=step.wait_before_next,
                max_retries=step.max_retries,
                timeout_seconds=step.timeout_seconds,
                require_approval=step.require_approval,
                code=step.code,
                validation_code=step.validation_code,
                linked_project_id=step.linked_project_id,
                wait_for_continue=step.wait_for_continue,
            ))
        for var in orig.variables:
            db.add(AutoWorkflowVariable(
                workflow_id=new_wf.id, name=var.name,
                default_value=var.default_value, description=var.description,
            ))
        db.commit()
        return new_wf.id


# ── Step CRUD ──

def add_step(workflow_id: int, name: str = "New Step", action_type: str = "agent_instruction",
             position: Optional[int] = None) -> Optional[int]:
    with get_session() as db:
        wf = db.query(AutoWorkflow).filter(AutoWorkflow.id == workflow_id).first()
        if not wf:
            return None
        if position is None:
            position = max((s.position for s in wf.steps), default=-1) + 1
        step = AutoWorkflowStep(workflow_id=workflow_id, position=position, name=name, action_type=action_type)
        db.add(step)
        db.commit()
        db.refresh(step)
        return step.id


def update_step(step_id: int, **kwargs) -> bool:
    allowed = {
        "name", "description", "position", "action_type", "instruction",
        "validation_type", "validation_prompt", "screenshot_path",
        "routing_mode", "routing_prompt",
        "on_pass_goto", "on_fail_goto", "wait_before_next",
        "max_retries", "timeout_seconds", "require_approval",
        "status", "result", "recording_filename", "action_id",
        "code", "validation_code", "linked_project_id", "wait_for_continue",
    }
    with get_session() as db:
        step = db.query(AutoWorkflowStep).filter(AutoWorkflowStep.id == step_id).first()
        if not step:
            return False
        for k, v in kwargs.items():
            if k in allowed:
                setattr(step, k, v)
        db.commit()
        return True


def delete_step(step_id: int) -> bool:
    with get_session() as db:
        step = db.query(AutoWorkflowStep).filter(AutoWorkflowStep.id == step_id).first()
        if not step:
            return False
        db.delete(step)
        db.commit()
        return True


def reorder_steps(workflow_id: int, step_ids: List[int]) -> bool:
    with get_session() as db:
        for pos, step_id in enumerate(step_ids):
            step = db.query(AutoWorkflowStep).filter(
                AutoWorkflowStep.id == step_id, AutoWorkflowStep.workflow_id == workflow_id,
            ).first()
            if step:
                step.position = pos
        db.commit()
        return True


# ── Variable CRUD ──

def add_variable(workflow_id: int, name: str, default_value: str = "", description: str = "") -> Optional[int]:
    with get_session() as db:
        wf = db.query(AutoWorkflow).filter(AutoWorkflow.id == workflow_id).first()
        if not wf:
            return None
        var = AutoWorkflowVariable(workflow_id=workflow_id, name=name, default_value=default_value, description=description)
        db.add(var)
        db.commit()
        db.refresh(var)
        return var.id


def update_variable(variable_id: int, **kwargs) -> bool:
    allowed = {"name", "default_value", "description"}
    with get_session() as db:
        var = db.query(AutoWorkflowVariable).filter(AutoWorkflowVariable.id == variable_id).first()
        if not var:
            return False
        for k, v in kwargs.items():
            if k in allowed:
                setattr(var, k, v)
        db.commit()
        return True


def delete_variable(variable_id: int) -> bool:
    with get_session() as db:
        var = db.query(AutoWorkflowVariable).filter(AutoWorkflowVariable.id == variable_id).first()
        if not var:
            return False
        db.delete(var)
        db.commit()
        return True


# ── Execution engine ──

def _clear_workflow_env():
    """Clear workflow run context environment variables."""
    os.environ.pop("DECISIONS_WORKFLOW_RUN_ID", None)
    os.environ.pop("DECISIONS_WORKFLOW_STEP_ID", None)


def _check_and_enter_wait(step_id: int, action_result: str, passed: bool) -> Optional[Dict[str, Any]]:
    """
    Check if a step has wait_for_continue=True and, if so, enter the waiting state.
    Returns a wait response dict if the step should wait, or None to proceed normally.
    """
    with get_session() as db:
        step = db.query(AutoWorkflowStep).filter(AutoWorkflowStep.id == step_id).first()
        if not step or not step.wait_for_continue:
            return None
        step.status = "waiting"
        step_name = step.name
        workflow_id = step.workflow_id
        # Find active run and set it to waiting too
        run = db.query(AutoWorkflowRun).filter(
            AutoWorkflowRun.workflow_id == step.workflow_id,
            AutoWorkflowRun.current_step_id == step_id,
            AutoWorkflowRun.status == "running",
        ).first()
        run_id = None
        if run:
            run.status = "waiting"
            run_id = run.id
            # Store action result in run_data for later complete_step() call
            run_data = json.loads(run.run_data or "{}")
            run_data["waiting_result"] = action_result
            run_data["waiting_passed"] = passed
            run.run_data = json.dumps(run_data)
        db.commit()

    # Notify the voice agent that the workflow is waiting for input
    # Speak the result via TTS so the user knows what happened
    try:
        from distr.core.signals import signal_manager
        # Truncate for TTS
        speak_text = action_result.strip()
        if len(speak_text) > 400:
            speak_text = speak_text[:400] + "..."
        notification = f"Step '{step_name}' is done and waiting for your input. Here's what happened: {speak_text}"
        signal_manager.speak_text_directly.emit(notification)
    except Exception as e:
        logger.debug("Could not speak wait notification: %s", e)

    # Also queue a report so the agent LLM knows about the waiting state
    try:
        bridge = WorkflowAgentBridge()
        bridge.queue_report_to_agent(workflow_id, f"Workflow step '{step_name}' completed and is now WAITING for your input. Run ID: {run_id}. Result: {action_result[:500]}")
    except Exception as e:
        logger.debug("Could not queue wait report: %s", e)

    return {"success": True, "waiting": True, "run_id": run_id, "message": "Step waiting for continue signal."}


def _dispatch_step(step_id: int, step_name: str, action_type: str,
                   instruction: str, recording_filename: str,
                   context_prefix: str = "Step Runner",
                   action_id: int = None,
                   code: str = None) -> Dict[str, Any]:
    """
    Dispatch a step based on its action_type.
    For execute_code: executes code via TestLoopService._execute_python().
    For playwright: executes code via TestLoopService._execute_playwright().
    For play_recording: emits the recording playback signal.
    For everything else: sends the instruction to the agent.
    Returns {"success": True, ...} or {"error": ...}.
    """
    if action_type in ("execute_code", "playwright"):
        exec_code = (code or "").strip()
        # If no code but instruction exists, generate code via CodeGeneratorService
        if not exec_code and (instruction or "").strip():
            try:
                from distr.core.step_runner.code_generator import CodeGeneratorService
                from distr.core.step_runner.step_types import StepType
                step_type = StepType.PLAYWRIGHT if action_type == "playwright" else StepType.EXECUTE_CODE
                exec_code = CodeGeneratorService().generate_code(instruction, step_type)
            except Exception as e:
                logger.error("Code generation failed for step %s: %s", step_id, e)
                update_step(step_id, status="failed", result=f"Code generation failed: {e}")
                return {"error": f"Code generation failed: {e}"}
        if not exec_code:
            update_step(step_id, status="failed", result="No code or instruction provided.")
            return {"error": "No code or instruction provided"}
        try:
            from distr.core.step_runner.test_loop import TestLoopService
            if action_type == "playwright":
                exec_result = TestLoopService()._execute_playwright(exec_code)
            else:
                exec_result = TestLoopService()._execute_python(exec_code)
            # Extract exit_code and output from ExecutionResult
            exit_code = exec_result.exit_code if hasattr(exec_result, 'exit_code') else exec_result.get("exit_code", 1)
            stdout = exec_result.stdout if hasattr(exec_result, 'stdout') else exec_result.get("stdout", "")
            stderr = exec_result.stderr if hasattr(exec_result, 'stderr') else exec_result.get("stderr", "")
            output = (stdout + "\n" + stderr).strip()
            passed = (exit_code == 0)
            # Check wait_for_continue before calling complete_step
            wait_result = _check_and_enter_wait(step_id, output, passed)
            if wait_result:
                return wait_result
            complete_step(step_id, output, passed)
            return {"success": True, "message": f"Code executed (exit_code={exit_code}).", "exit_code": exit_code}
        except Exception as e:
            logger.error("Code execution failed for step %s: %s", step_id, e)
            update_step(step_id, status="failed", result=str(e))
            return {"error": str(e)}
    elif action_type == "play_recording":
        # If no recording_filename on step, try the linked Action entity
        if not recording_filename and action_id:
            try:
                with get_session() as db:
                    linked = db.query(Action).filter(Action.id == action_id).first()
                    if linked and linked.recording_filename:
                        recording_filename = linked.recording_filename
            except Exception as e:
                logger.warning(f"Could not load linked action {action_id}: {e}")
        if not recording_filename:
            update_step(step_id, status="failed", result="No recording attached.")
            return {"error": "No recording attached to this step"}
        try:
            from distr.core.signals import signal_manager

            _advanced = [False]

            def _on_playback_done(result_text: str, passed: bool):
                if _advanced[0]:
                    return
                _advanced[0] = True
                try:
                    signal_manager.action_playback_finished.disconnect(_on_finished)
                except Exception:
                    pass
                try:
                    signal_manager.action_playback_stopped.disconnect(_on_stopped)
                except Exception:
                    pass
                wait_result = _check_and_enter_wait(step_id, result_text, passed)
                if wait_result:
                    return
                complete_step(step_id, result_text, passed=passed)

            def _on_finished():
                _on_playback_done("Recording completed.", True)

            def _on_stopped(reason: str):
                _on_playback_done(f"Recording stopped: {reason}" if reason else "Recording stopped.", True)

            signal_manager.action_playback_finished.connect(_on_finished)
            signal_manager.action_playback_stopped.connect(_on_stopped)
            signal_manager.play_recording_file.emit(recording_filename)
            return {"success": True, "message": "Playing recording."}
        except Exception as e:
            update_step(step_id, status="failed", result=str(e))
            return {"error": str(e)}
    else:
        if not instruction.strip():
            update_step(step_id, status="failed", result="No instruction provided.")
            return {"error": "No instruction provided"}
        prompt = f"[{context_prefix} — {step_name}]\n{instruction}"

        # Look up the active run's _RunContext for this step
        run_ctx = None
        with get_session() as db:
            step_obj = db.query(AutoWorkflowStep).filter(AutoWorkflowStep.id == step_id).first()
            if step_obj:
                run = db.query(AutoWorkflowRun).filter(
                    AutoWorkflowRun.workflow_id == step_obj.workflow_id,
                    AutoWorkflowRun.current_step_id == step_id,
                    AutoWorkflowRun.status == "running",
                ).first()
                if run:
                    with _runs_lock:
                        run_ctx = _active_runs.get(run.id)

        if run_ctx is not None:
            # Dispatch via WorkflowAgent in the run's background event loop
            try:
                future = asyncio.run_coroutine_threadsafe(
                    run_ctx.workflow_agent.execute(prompt),
                    run_ctx.event_loop,
                )

                def _on_agent_done(fut):
                    try:
                        response_text = fut.result()
                        # Check wait_for_continue before completing
                        wait_result = _check_and_enter_wait(step_id, response_text, True)
                        if wait_result:
                            return
                        complete_step(step_id, response_text, passed=True)
                    except Exception as exc:
                        error_message = str(exc)
                        logger.error("WorkflowAgent.execute() failed for step %s: %s", step_id, error_message)
                        complete_step(step_id, error_message, passed=False)

                future.add_done_callback(_on_agent_done)
                return {"success": True, "message": "Step dispatched to WorkflowAgent."}
            except Exception as e:
                logger.error("Failed to dispatch step %s to WorkflowAgent: %s", step_id, e)
                update_step(step_id, status="failed", result=str(e))
                return {"error": str(e)}
        else:
            # Fallback: isolated step execution via signal (no active run context)
            try:
                from distr.core.signals import signal_manager
                signal_manager.send_text_input.emit(prompt, False, None, None)
                return {"success": True, "message": "Step sent to agent."}
            except Exception as e:
                update_step(step_id, status="failed", result=str(e))
                return {"error": str(e)}


def execute_step(step_id: int, isolated: bool = False) -> Dict[str, Any]:
    """
    Execute a single step. Sets status to 'running' and dispatches based on action_type.
    isolated=True means run just this step without workflow context.
    """
    with get_session() as db:
        step = db.query(AutoWorkflowStep).filter(AutoWorkflowStep.id == step_id).first()
        if not step:
            return {"error": "Step not found"}
        step.status = "running"
        step.result = None
        db.commit()
        step_name = step.name
        action_type = step.action_type or "agent_instruction"
        instruction = step.instruction or ""
        recording_filename = step.recording_filename or ""
        step_action_id = step.action_id
        step_code = step.code or ""

    return _dispatch_step(step_id, step_name, action_type, instruction,
                          recording_filename, "Step Runner", step_action_id, code=step_code)


def start_workflow_run(workflow_id: int, context: Optional[str] = None, start_step_id: Optional[int] = None) -> Dict[str, Any]:
    """
    Start a full workflow run. Creates a run record, resets all step statuses,
    creates a dedicated WorkflowAgent + event loop, and kicks off the first step.

    When *start_step_id* is provided, execution begins from that step instead of
    the workflow's ``start_step_position`` field.
    """
    with get_session() as db:
        wf = db.query(AutoWorkflow).filter(AutoWorkflow.id == workflow_id).first()
        if not wf:
            return {"error": "Workflow not found"}
        if not wf.steps:
            return {"error": "Workflow has no steps"}

        # Reject concurrent runs — check atomically within the same transaction
        active_run = db.query(AutoWorkflowRun).filter(
            AutoWorkflowRun.workflow_id == workflow_id,
            AutoWorkflowRun.status.in_(["running", "waiting"]),
        ).first()
        if active_run:
            return {"error": "A run is already in progress"}

        sorted_steps = sorted(wf.steps, key=lambda s: s.position)

        # Determine start step
        first_step = None
        start_idx = 0
        if start_step_id is not None:
            for i, s in enumerate(sorted_steps):
                if s.id == int(start_step_id):
                    first_step = s
                    start_idx = i
                    break
        if first_step is None:
            first_step = sorted_steps[0]
            start_idx = 0

        # Reset steps from start_idx onwards; leave prior steps as-is
        for i, step in enumerate(sorted_steps):
            if i >= start_idx:
                step.status = "pending"
                step.result = None

        # Create run record
        run = AutoWorkflowRun(workflow_id=workflow_id, status="running")
        db.add(run)
        db.flush()

        run_id = run.id

        # Create a dedicated WorkflowAgent and background event loop for this run
        workflow_agent = WorkflowAgent()
        agent_loop = asyncio.new_event_loop()

        def _run_loop():
            asyncio.set_event_loop(agent_loop)
            agent_loop.run_forever()

        agent_thread = threading.Thread(target=_run_loop, daemon=True)
        agent_thread.start()

        with _runs_lock:
            _active_runs[run_id] = _RunContext(
                run_id=run_id,
                workflow_agent=workflow_agent,
                event_loop=agent_loop,
                thread=agent_thread,
                context_prefix=context or "",
            )

        run.current_step_id = first_step.id
        first_step.status = "running"
        first_step_id = first_step.id
        first_step_name = first_step.name
        first_action_type = first_step.action_type or "agent_instruction"
        first_instruction = first_step.instruction or ""
        first_recording = first_step.recording_filename or ""
        first_action_id = first_step.action_id
        first_code = first_step.code or ""
        db.commit()

    # Prepend context to the first agent_instruction step if context is provided
    if context and first_action_type == "agent_instruction":
        first_instruction = f"{context}\n\n{first_instruction}"

    # Set workflow run context env vars so agent tools (e.g. CreateCursorTicketTool)
    # can detect they are running inside a workflow and include metadata.
    os.environ["DECISIONS_WORKFLOW_RUN_ID"] = str(run_id)
    os.environ["DECISIONS_WORKFLOW_STEP_ID"] = str(first_step_id)

    result = _dispatch_step(first_step_id, first_step_name, first_action_type,
                            first_instruction, first_recording, "Workflow Run", first_action_id,
                            code=first_code)
    if "error" in result:
        _clear_workflow_env()
        complete_run(run_id, "failed")
        return result
    result["run_id"] = run_id
    return result


def cancel_run(run_id: int) -> bool:
    """Cancel an active workflow run."""
    with get_session() as db:
        run = db.query(AutoWorkflowRun).filter(AutoWorkflowRun.id == run_id).first()
        if not run:
            return False
        run.status = "cancelled"
        run.completed_at = datetime.utcnow()
        # Cancel the currently running step
        if run.current_step_id:
            step = db.query(AutoWorkflowStep).filter(AutoWorkflowStep.id == run.current_step_id).first()
            if step and step.status == "running":
                step.status = "cancelled"
                step.result = "Cancelled by user."
        _run_id, _wf_id = run.id, run.workflow_id
        db.commit()
    _finalize_terminal_run(_run_id, _wf_id, "cancelled")
    return True


def cancel_step(step_id: int) -> bool:
    """Cancel a running step."""
    with get_session() as db:
        step = db.query(AutoWorkflowStep).filter(AutoWorkflowStep.id == step_id).first()
        if not step:
            return False
        step.status = "cancelled"
        step.result = "Cancelled by user."
        db.commit()
        return True


def reset_workflow_steps(workflow_id: int) -> Dict[str, Any]:
    """Cancel any active run and reset all step statuses to pending.

    Use when the user wants to stop everything and start fresh.
    """
    with get_session() as db:
        wf = db.query(AutoWorkflow).filter(AutoWorkflow.id == workflow_id).first()
        if not wf:
            return {"error": "Workflow not found"}

        # Cancel any active runs
        cancelled_runs = 0
        active_runs = (
            db.query(AutoWorkflowRun)
            .filter(
                AutoWorkflowRun.workflow_id == workflow_id,
                AutoWorkflowRun.status.in_(["running", "waiting"]),
            )
            .all()
        )
        run_ids = []
        for run in active_runs:
            run.status = "cancelled"
            run.completed_at = datetime.utcnow()
            run_ids.append(run.id)
            cancelled_runs += 1
        db.commit()

    # Clean up agents outside the DB session
    for rid in run_ids:
        _cleanup_run(rid)

    # Reset all steps to pending
    with get_session() as db:
        wf = db.query(AutoWorkflow).filter(AutoWorkflow.id == workflow_id).first()
        if wf:
            for step in wf.steps:
                step.status = "pending"
                step.result = None
            db.commit()

    return {
        "success": True,
        "workflow_id": workflow_id,
        "cancelled_runs": cancelled_runs,
        "steps_reset": len(wf.steps) if wf else 0,
    }


def clear_workflow_history(workflow_id: int) -> Dict[str, Any]:
    """Delete all run history and step results for a workflow."""
    with get_session() as db:
        wf = db.query(AutoWorkflow).filter(AutoWorkflow.id == workflow_id).first()
        if not wf:
            return {"error": "Workflow not found"}

        # Cancel any active runs first
        active_runs = (
            db.query(AutoWorkflowRun)
            .filter(
                AutoWorkflowRun.workflow_id == workflow_id,
                AutoWorkflowRun.status.in_(["running", "waiting"]),
            )
            .all()
        )
        active_run_ids = []
        for run in active_runs:
            run.status = "cancelled"
            run.completed_at = datetime.utcnow()
            active_run_ids.append(run.id)
        db.commit()

    # Clean up agents outside DB session
    for rid in active_run_ids:
        _cleanup_run(rid)

    with get_session() as db:
        # Delete all step results for this workflow's runs
        run_ids = [
            r.id for r in
            db.query(AutoWorkflowRun)
            .filter(AutoWorkflowRun.workflow_id == workflow_id)
            .all()
        ]
        deleted_results = 0
        if run_ids:
            deleted_results = (
                db.query(AutoWorkflowStepResult)
                .filter(AutoWorkflowStepResult.run_id.in_(run_ids))
                .delete(synchronize_session=False)
            )
        # Delete all runs
        deleted_runs = (
            db.query(AutoWorkflowRun)
            .filter(AutoWorkflowRun.workflow_id == workflow_id)
            .delete(synchronize_session=False)
        )
        # Reset step statuses
        wf = db.query(AutoWorkflow).filter(AutoWorkflow.id == workflow_id).first()
        if wf:
            for step in wf.steps:
                step.status = "pending"
                step.result = None
        db.commit()

    return {
        "success": True,
        "workflow_id": workflow_id,
        "deleted_runs": deleted_runs,
        "deleted_results": deleted_results,
    }


def continue_waiting_step(run_id: int, optional_input: str = "") -> Dict[str, Any]:
    """Resume a workflow run that is in 'waiting' status."""
    with get_session() as db:
        run = db.query(AutoWorkflowRun).filter(AutoWorkflowRun.id == run_id).first()
        if not run:
            return {"error": "Run not found", "status_code": 404}
        if run.status != "waiting":
            return {"error": f"Run is not waiting (status: {run.status})", "status_code": 409}

        step = db.query(AutoWorkflowStep).filter(AutoWorkflowStep.id == run.current_step_id).first()
        if not step or step.status != "waiting":
            return {"error": "No waiting step found", "status_code": 409}

        # Restore stored result
        run_data = json.loads(run.run_data or "{}")
        stored_result = run_data.get("waiting_result", "")
        stored_passed = run_data.get("waiting_passed", True)

        # Append optional input if provided
        if optional_input.strip():
            stored_result = f"{stored_result}\n\n[CONTINUE INPUT]: {optional_input.strip()}"

        # Set run and step back to running
        run.status = "running"
        step.status = "running"
        db.commit()

        step_id = step.id

    # Now call complete_step with the stored result
    return complete_step(step_id, stored_result, stored_passed, _from_continue=True)


def complete_step(step_id: int, result: str, passed: bool, _from_continue: bool = False) -> Dict[str, Any]:
    """
    Mark a step as complete. Runs verification if configured, stores result in history,
    speaks the result via TTS, then advances based on routing.
    Default routing: null = END workflow (not next step).
    """
    with get_session() as db:
        step = db.query(AutoWorkflowStep).filter(AutoWorkflowStep.id == step_id).first()
        if not step:
            return {"error": "Step not found"}

        # For async action types (agent_instruction), check wait_for_continue
        # before running verification and routing. Skip if resuming from
        # continue_waiting_step (_from_continue=True) to avoid re-entering wait.
        if step.wait_for_continue and not _from_continue:
            # Enter wait state — store result for later complete_step() call
            step.status = "waiting"
            run = db.query(AutoWorkflowRun).filter(
                AutoWorkflowRun.workflow_id == step.workflow_id,
                AutoWorkflowRun.current_step_id == step_id,
                AutoWorkflowRun.status == "running",
            ).first()
            if run:
                run.status = "waiting"
                run_data_dict = json.loads(run.run_data or "{}")
                run_data_dict["waiting_result"] = result
                run_data_dict["waiting_passed"] = passed
                run.run_data = json.dumps(run_data_dict)
            db.commit()
            return {"success": True, "waiting": True, "message": "Step waiting for continue signal."}

        # Run verification engine if validation is configured
        verified_passed = _run_verification(step, result, passed)
        status = "passed" if verified_passed else "failed"
        step.status = status
        step.result = result

        # Find active run for this step (if any)
        run = db.query(AutoWorkflowRun).filter(
            AutoWorkflowRun.workflow_id == step.workflow_id,
            AutoWorkflowRun.status == "running",
            AutoWorkflowRun.current_step_id == step_id,
        ).first()

        # Store result in history
        step_result = AutoWorkflowStepResult(
            step_id=step_id,
            run_id=run.id if run else None,
            agent_response=result,
            status=status,
        )
        db.add(step_result)

        if not run:
            db.commit()
            # TTS the result
            _speak_result(result)
            return {"done": True, "status": status}

        # Determine next step based on routing
        routing_mode = (step.routing_mode or "static").strip().lower()
        wait = step.wait_before_next or 0

        if routing_mode == "agent_decision":
            # Let the agent decide which step to go to
            wf = db.query(AutoWorkflow).filter(AutoWorkflow.id == step.workflow_id).first()
            all_steps = sorted(wf.steps, key=lambda s: s.position) if wf else []
            next_step_id = _agent_route_decision(step, result, verified_passed, all_steps)
            if next_step_id is None or next_step_id == -1:
                run.status = "completed"
                run.completed_at = datetime.utcnow()
                _run_id, _wf_id = run.id, step.workflow_id
                db.commit()
                _finalize_terminal_run(_run_id, _wf_id, "completed")
                return {"done": True, "status": "completed", "run_id": _run_id}
            next_step = db.query(AutoWorkflowStep).filter(AutoWorkflowStep.id == next_step_id).first()
            if not next_step:
                run.status = "completed"
                run.completed_at = datetime.utcnow()
                _run_id, _wf_id = run.id, step.workflow_id
                db.commit()
                _finalize_terminal_run(_run_id, _wf_id, "completed")
                return {"done": True, "status": "completed", "run_id": _run_id}
            # Safety: prevent routing to self
            if next_step.id == step_id:
                logger.warning("Agent routed step %d to itself. Ending workflow.", step_id)
                run.status = "completed"
                run.completed_at = datetime.utcnow()
                _run_id, _wf_id = run.id, step.workflow_id
                db.commit()
                _finalize_terminal_run(_run_id, _wf_id, "completed")
                return {"done": True, "status": "completed", "run_id": _run_id, "warning": "Infinite loop prevented"}
        else:
            # Static routing: null = END workflow (safety: no infinite loops)
            goto = step.on_pass_goto if verified_passed else step.on_fail_goto

            if goto is None or goto == -1:
                # Default: END workflow
                run.status = "completed"
                run.completed_at = datetime.utcnow()
                _run_id, _wf_id = run.id, step.workflow_id
                db.commit()
                _finalize_terminal_run(_run_id, _wf_id, "completed")
                return {"done": True, "status": "completed", "run_id": _run_id}

            # Go to specific step by ID
            next_step = db.query(AutoWorkflowStep).filter(AutoWorkflowStep.id == goto).first()

            if not next_step:
                run.status = "completed"
                run.completed_at = datetime.utcnow()
                _run_id, _wf_id = run.id, step.workflow_id
                db.commit()
                _finalize_terminal_run(_run_id, _wf_id, "completed")
                return {"done": True, "status": "completed", "run_id": _run_id}

            # Safety: prevent routing to self (infinite loop)
            if next_step.id == step_id:
                logger.warning("Infinite loop detected: step %d routes to itself. Ending workflow.", step_id)
                run.status = "completed"
                run.completed_at = datetime.utcnow()
                _run_id, _wf_id = run.id, step.workflow_id
                db.commit()
                _finalize_terminal_run(_run_id, _wf_id, "completed")
                return {"done": True, "status": "completed", "run_id": _run_id, "warning": "Infinite loop prevented"}

        # Advance to next step
        next_step.status = "running"
        run.current_step_id = next_step.id
        next_step_name = next_step.name
        next_action_type = next_step.action_type or "agent_instruction"
        next_instruction = next_step.instruction or ""
        next_recording = next_step.recording_filename or ""
        next_action_linked_id = next_step.action_id
        next_code = next_step.code or ""
        run_id = run.id
        next_step_id = next_step.id
        db.commit()

    # Update workflow step env var for the next step
    os.environ["DECISIONS_WORKFLOW_STEP_ID"] = str(next_step_id)

    # Do NOT speak intermediate step results — only speak at workflow end.
    # The WorkflowAgentBridge handles the final summary via TTS.

    # Handle wait before next
    if wait > 0:
        import threading
        def delayed_dispatch():
            import time
            time.sleep(wait)
            _dispatch_step(next_step_id, next_step_name, next_action_type,
                           next_instruction, next_recording, "Workflow Run", next_action_linked_id,
                           code=next_code)
        threading.Thread(target=delayed_dispatch, daemon=True).start()
    else:
        dispatch_result = _dispatch_step(next_step_id, next_step_name, next_action_type,
                                         next_instruction, next_recording, "Workflow Run", next_action_linked_id,
                                         code=next_code)
        if "error" in dispatch_result:
            complete_run(run_id, "failed")
            return {"done": True, "status": "failed"}

    return {"done": False, "next_step_id": next_step_id, "wait": wait}


def complete_run(run_id: int, status: str = "completed") -> bool:
    with get_session() as db:
        run = db.query(AutoWorkflowRun).filter(AutoWorkflowRun.id == run_id).first()
        if not run:
            return False
        run.status = status
        run.completed_at = datetime.utcnow()
        workflow_id = run.workflow_id
        db.commit()
    _finalize_terminal_run(run_id, workflow_id, status)
    # Clear workflow run context env vars when run reaches terminal status
    _clear_workflow_env()
    return True


def get_active_run(workflow_id: int) -> Optional[Dict[str, Any]]:
    """Get the currently active run for a workflow, if any."""
    with get_session() as db:
        run = db.query(AutoWorkflowRun).filter(
            AutoWorkflowRun.workflow_id == workflow_id,
            AutoWorkflowRun.status == "running",
        ).first()
        if not run:
            return None
        return {
            "id": run.id,
            "current_step_id": run.current_step_id,
            "started_at": run.started_at.isoformat() if run.started_at else None,
        }


# ── Run history ──

def get_run_history(workflow_id: int, limit: int = 10) -> List[Dict[str, Any]]:
    with get_session() as db:
        rows = (
            db.query(AutoWorkflowRun)
            .filter(AutoWorkflowRun.workflow_id == workflow_id)
            .order_by(AutoWorkflowRun.started_at.desc())
            .limit(limit).all()
        )
        return [
            {
                "id": r.id,
                "started_at": r.started_at.isoformat() if r.started_at else None,
                "completed_at": r.completed_at.isoformat() if r.completed_at else None,
                "status": r.status,
                "current_step_id": r.current_step_id,
            }
            for r in rows
        ]


# ── Screenshot management ──

def save_screenshot(step_id: int, file_data: bytes, filename: str) -> Optional[str]:
    """Save a reference screenshot for screenshot_compare validation."""
    from distr.core.paths import DB_DIR
    screenshots_dir = os.path.join(DB_DIR, "workflow_screenshots")
    os.makedirs(screenshots_dir, exist_ok=True)
    ext = os.path.splitext(filename)[1] or ".png"
    save_path = os.path.join(screenshots_dir, f"step_{step_id}{ext}")
    with open(save_path, "wb") as f:
        f.write(file_data)
    update_step(step_id, screenshot_path=save_path)
    return save_path


# ── Verification engine ──

def _run_verification(step: AutoWorkflowStep, result: str, caller_passed: bool) -> bool:
    """
    Run the configured validation for a step. Returns True if passed.
    If validation_type is 'none', uses the caller's passed flag.
    """
    vtype = (step.validation_type or "none").strip().lower()
    if vtype == "none":
        return caller_passed

    prompt = (step.validation_prompt or "").strip()
    if not prompt and vtype != "playwright":
        # No validation criteria configured — trust the caller
        return caller_passed

    try:
        if vtype == "text_match":
            return _verify_text_match(result, prompt)
        elif vtype == "rule_based":
            return _verify_rule_based(result, prompt)
        elif vtype == "llm_judgment":
            return _verify_llm_judgment(result, prompt)
        elif vtype == "screenshot_compare":
            return _verify_screenshot(step, result, prompt)
        elif vtype == "playwright":
            return _verify_playwright(step, caller_passed)
        else:
            logger.warning("Unknown validation type '%s', defaulting to caller_passed", vtype)
            return caller_passed
    except Exception as e:
        logger.error("Verification failed for step %s: %s", step.id, e, exc_info=True)
        return False


def _verify_text_match(result: str, criteria: str) -> bool:
    """Check if the result contains the expected text (case-insensitive)."""
    if not result:
        return False
    result_lower = result.lower()
    # Support multiple match phrases separated by newlines
    for line in criteria.strip().splitlines():
        phrase = line.strip()
        if phrase and phrase.lower() not in result_lower:
            return False
    return True


def _verify_rule_based(result: str, rules: str) -> bool:
    """Evaluate simple rules against the result.
    Rules are line-separated. Each line is a condition:
      contains: <text>
      not_contains: <text>
      starts_with: <text>
      min_length: <number>
    """
    if not result:
        return False
    result_lower = result.lower()
    for line in rules.strip().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.lower().startswith("contains:"):
            val = line[len("contains:"):].strip()
            if val.lower() not in result_lower:
                return False
        elif line.lower().startswith("not_contains:"):
            val = line[len("not_contains:"):].strip()
            if val.lower() in result_lower:
                return False
        elif line.lower().startswith("starts_with:"):
            val = line[len("starts_with:"):].strip()
            if not result_lower.startswith(val.lower()):
                return False
        elif line.lower().startswith("min_length:"):
            try:
                min_len = int(line[len("min_length:"):].strip())
                if len(result) < min_len:
                    return False
            except ValueError:
                pass
    return True


def _verify_llm_judgment(result: str, validation_prompt: str) -> bool:
    """Send the result + validation prompt to the LLM for judgment."""
    try:
        from distr.core.signals import signal_manager
        # Build a judgment prompt
        judgment_prompt = (
            f"You are a validation judge. Evaluate whether the following result passes the validation criteria.\n\n"
            f"VALIDATION CRITERIA:\n{validation_prompt}\n\n"
            f"RESULT TO VALIDATE:\n{result}\n\n"
            f"Respond with exactly PASS or FAIL followed by a brief explanation."
        )
        # Use synchronous LLM call if available
        try:
            from distr.core.agent.services.llm.shared import get_shared_llm_response
            response = get_shared_llm_response(judgment_prompt)
            if response:
                return response.strip().upper().startswith("PASS")
        except ImportError:
            pass
        # Fallback: trust caller
        logger.warning("LLM judgment not available, defaulting to pass")
        return True
    except Exception as e:
        logger.error("LLM judgment failed: %s", e, exc_info=True)
        return False


def _verify_screenshot(step: AutoWorkflowStep, result: str, validation_prompt: str) -> bool:
    """Compare current screen state against reference screenshot using LLM vision.
    Falls back to text-based validation if vision is not available."""
    ref_path = step.screenshot_path
    if not ref_path or not os.path.exists(ref_path):
        logger.warning("No reference screenshot for step %s, using text validation", step.id)
        return _verify_text_match(result, validation_prompt) if validation_prompt else True

    # Take a current screenshot for comparison
    try:
        import subprocess
        import platform
        from distr.core.paths import DB_DIR
        current_path = os.path.join(DB_DIR, "workflow_screenshots", f"step_{step.id}_current.png")
        os.makedirs(os.path.dirname(current_path), exist_ok=True)
        system = platform.system()
        if system == "Darwin":
            subprocess.run(["screencapture", "-x", current_path], timeout=5, check=True)
        elif system == "Windows":
            from PIL import ImageGrab
            img = ImageGrab.grab()
            img.save(current_path)
        else:
            from PIL import ImageGrab
            img = ImageGrab.grab()
            img.save(current_path)
        # If we have both screenshots, try LLM vision comparison
        # For now, fall back to validation_prompt text match
        logger.info("Screenshots captured for step %s. Using validation prompt for judgment.", step.id)
        if validation_prompt:
            return _verify_llm_judgment(result + f"\n[Screenshots captured: reference={ref_path}, current={current_path}]", validation_prompt)
        return True
    except Exception as e:
        logger.error("Screenshot comparison failed: %s", e, exc_info=True)
        return True


def _verify_playwright(step: AutoWorkflowStep, caller_passed: bool) -> bool:
    """Execute a Playwright validation script. Exit code 0 = passed, non-zero = failed.
    Falls back to caller_passed if validation_code is empty."""
    validation_code = (step.validation_code or "").strip()
    if not validation_code:
        logger.info("No validation_code for step %s, falling back to caller_passed", step.id)
        return caller_passed

    try:
        from distr.core.step_runner.test_loop import TestLoopService
        result = TestLoopService()._execute_playwright(validation_code)
        exit_code = result.get("exit_code", 1) if isinstance(result, dict) else getattr(result, "exit_code", 1)
        output = result.get("output", "") if isinstance(result, dict) else getattr(result, "output", "")
        if exit_code == 0:
            logger.info("Playwright validation passed for step %s", step.id)
            return True
        else:
            logger.info("Playwright validation failed for step %s (exit_code=%s): %s", step.id, exit_code, output[:200])
            return False
    except Exception as e:
        logger.error("Playwright validation error for step %s: %s", step.id, e, exc_info=True)
        return False


# ── Agent-based routing ──

def _agent_route_decision(
    step: AutoWorkflowStep,
    result: str,
    passed: bool,
    all_steps: List[AutoWorkflowStep],
) -> Optional[int]:
    """
    Ask the LLM to decide which step to go to next based on the current step's
    result, pass/fail status, and the list of available steps.

    Returns a step ID, or None/-1 to end the workflow.
    """
    # Build the step map for the agent
    step_descriptions = []
    for s in all_steps:
        if s.id == step.id:
            continue  # Don't offer the current step as a target
        desc = f"  - Step ID {s.id}: \"{s.name}\" (position #{s.position})"
        if s.description:
            desc += f" — {s.description}"
        step_descriptions.append(desc)

    if not step_descriptions:
        # No other steps to route to
        return None

    steps_list = "\n".join(step_descriptions)
    routing_prompt = (step.routing_prompt or "").strip()
    status_word = "PASSED" if passed else "FAILED"

    prompt = (
        f"You are a workflow routing agent. A step just completed and you need to decide what happens next.\n\n"
        f"COMPLETED STEP: \"{step.name}\" (ID {step.id})\n"
        f"STATUS: {status_word}\n"
        f"RESULT:\n{result}\n\n"
    )

    if routing_prompt:
        prompt += f"ROUTING INSTRUCTIONS:\n{routing_prompt}\n\n"

    prompt += (
        f"AVAILABLE NEXT STEPS:\n{steps_list}\n\n"
        f"Respond with ONLY one of the following:\n"
        f"- A step ID number (e.g. \"42\") to go to that step\n"
        f"- \"END\" to finish the workflow\n\n"
        f"Your decision:"
    )

    try:
        try:
            from distr.core.agent.services.llm.shared import get_shared_llm_response
            response = get_shared_llm_response(prompt)
            if response:
                return _parse_routing_response(response, all_steps, step.id)
        except ImportError:
            pass

        # Fallback: send via signal and let the agent handle it asynchronously
        # For now, if no synchronous LLM is available, default to END
        logger.warning("Agent routing: no synchronous LLM available, defaulting to END")
        return None
    except Exception as e:
        logger.error("Agent routing decision failed: %s", e, exc_info=True)
        return None


def _parse_routing_response(
    response: str,
    all_steps: List[AutoWorkflowStep],
    current_step_id: int,
) -> Optional[int]:
    """Parse the LLM's routing response into a step ID or None (end)."""
    text = response.strip().upper()

    if text == "END" or text.startswith("END"):
        return None

    # Try to extract a number
    import re
    numbers = re.findall(r'\d+', text)
    if numbers:
        candidate_id = int(numbers[0])
        # Validate it's a real step and not the current step
        valid_ids = {s.id for s in all_steps if s.id != current_step_id}
        if candidate_id in valid_ids:
            return candidate_id
        # Maybe the agent gave a position instead of an ID
        for s in all_steps:
            if s.position == candidate_id and s.id != current_step_id:
                return s.id

    logger.warning("Could not parse agent routing response: '%s', defaulting to END", response)
    return None


# ── TTS helper ──

def _speak_result(result: str):
    """Speak the agent result via TTS if it's meaningful."""
    if not result or not result.strip():
        return
    # Truncate very long results for TTS
    text = result.strip()
    if len(text) > 500:
        text = text[:500] + "..."
    try:
        from distr.core.signals import signal_manager
        signal_manager.speak_text_directly.emit(text)
    except Exception as e:
        logger.debug("TTS speak failed: %s", e)


# ── Step result history ──

def get_step_results(step_id: int, limit: int = 20) -> List[Dict[str, Any]]:
    """Get execution result history for a step."""
    with get_session() as db:
        rows = (
            db.query(AutoWorkflowStepResult)
            .filter(AutoWorkflowStepResult.step_id == step_id)
            .order_by(AutoWorkflowStepResult.created_at.desc())
            .limit(limit).all()
        )
        return [
            {
                "id": r.id,
                "step_id": r.step_id,
                "run_id": r.run_id,
                "agent_response": r.agent_response or "",
                "status": r.status,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows
        ]


# ── Export / Import ──

def _get_presets_dir() -> str:
    return os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))), "steprunner", "presets")


def export_workflow(workflow_id: int) -> Optional[Dict[str, Any]]:
    """Export a workflow + steps + variables as a portable JSON dict (metadata only, no files)."""
    with get_session() as db:
        wf = db.query(AutoWorkflow).filter(AutoWorkflow.id == workflow_id).first()
        if not wf:
            return None
        steps = sorted(wf.steps, key=lambda s: s.position)

        # Collect linked actions for steps that have them
        linked_actions = {}
        for s in steps:
            if s.action_id:
                action = db.query(Action).filter(Action.id == s.action_id).first()
                if action:
                    linked_actions[s.action_id] = {
                        "title": action.title or "",
                        "description": action.description or "",
                        "additional_trigger_words": action.additional_trigger_words or "[]",
                        "is_instruction": action.is_instruction or False,
                        "instruction_text": action.instruction_text or "",
                        "recording_filename": action.recording_filename or "",
                    }

        return {
            "format_version": "2.0",
            "format": "decisionsai_workflow_v1",
            "name": wf.name,
            "description": wf.description or "",
            "workflow_type": wf.workflow_type or "manual",
            "context_rules": wf.context_rules or "",
            "start_step_position": wf.start_step_position or 0,
            "schedule_preset": wf.schedule_preset,
            "schedule_time": wf.schedule_time,
            "schedule_days": wf.schedule_days,
            "steps": [
                {
                    "position": s.position, "name": s.name,
                    "description": s.description or "",
                    "action_type": s.action_type or "agent_instruction",
                    "step_type": s.step_type or "agent_instruction",
                    "instruction": s.instruction or "",
                    "verification": s.verification or "",
                    "config": _safe_json_loads(s.config),
                    "validation_type": s.validation_type or "none",
                    "validation_prompt": s.validation_prompt or "",
                    "routing_mode": s.routing_mode or "static",
                    "routing_prompt": s.routing_prompt or "",
                    "on_pass_goto_position": _step_id_to_position(s.on_pass_goto, steps),
                    "on_fail_goto_position": _step_id_to_position(s.on_fail_goto, steps),
                    "wait_before_next": s.wait_before_next or 0,
                    "max_retries": s.max_retries or 0,
                    "timeout_seconds": s.timeout_seconds or 300,
                    "require_approval": s.require_approval or False,
                    "recording_filename": s.recording_filename or "",
                    "screenshot_filename": os.path.basename(s.screenshot_path) if s.screenshot_path else "",
                    "linked_action": linked_actions.get(s.action_id) if s.action_id else None,
                    "code": s.code or "",
                    "validation_code": s.validation_code or "",
                    "linked_project_id": s.linked_project_id,
                    "wait_for_continue": s.wait_for_continue or False,
                }
                for s in steps
            ],
            "variables": [
                {"name": v.name, "default_value": v.default_value or "", "description": v.description or ""}
                for v in wf.variables
            ],
        }


def export_workflow_bundle(workflow_id: int) -> Optional[bytes]:
    """
    Export a workflow as a .dwf bundle (ZIP with custom extension).
    Includes: workflow.json + recordings/*.json + screenshots/*
    Returns raw bytes of the ZIP archive.
    """
    import zipfile
    import io
    from distr.core.paths import RECORDINGS_DIR, DB_DIR

    data = export_workflow(workflow_id)
    if not data:
        return None

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        # Write the workflow manifest
        zf.writestr("workflow.json", json.dumps(data, indent=2))

        # Bundle recording files
        for s in data.get("steps", []):
            rec = s.get("recording_filename", "")
            if rec:
                rec_path = os.path.join(RECORDINGS_DIR, rec)
                if os.path.isfile(rec_path):
                    zf.write(rec_path, f"recordings/{rec}")

            # Bundle linked action's recording if different from step recording
            linked = s.get("linked_action")
            if linked:
                linked_rec = linked.get("recording_filename", "")
                if linked_rec and linked_rec != rec:
                    linked_rec_path = os.path.join(RECORDINGS_DIR, linked_rec)
                    if os.path.isfile(linked_rec_path):
                        zf.write(linked_rec_path, f"recordings/{linked_rec}")

            # Bundle screenshot files
            scr = s.get("screenshot_filename", "")
            if scr:
                scr_path = os.path.join(DB_DIR, "workflow_screenshots", scr)
                if os.path.isfile(scr_path):
                    zf.write(scr_path, f"screenshots/{scr}")

    return buf.getvalue()


def _convert_legacy_to_unified(data: Dict[str, Any]) -> Dict[str, Any]:
    """Convert a legacy StepRunner session export (no format_version or '1.0') to unified v2.0 format.

    Uses the migration field mapping from the design document:
    - Session: instruction → description, session_type → workflow_type, enabled → schedule_enabled, etc.
    - Steps: title → name, step_type/config/verification/code/position preserved
    """
    # Map session_type to workflow_type
    session_type = data.get("session_type", "instruction")
    type_map = {"instruction": "instruction", "scheduled": "scheduled"}
    workflow_type = type_map.get(session_type, "manual")

    # Map status: planned → draft, in_progress → active
    status_raw = data.get("status", "planned")
    status_map = {"planned": "draft", "in_progress": "active"}
    status = status_map.get(status_raw, status_raw)

    unified: Dict[str, Any] = {
        "format_version": "2.0",
        "name": data.get("name", data.get("instruction", "Imported Workflow")[:80] or "Imported Workflow"),
        "description": data.get("instruction", ""),
        "workflow_type": workflow_type,
        "context_rules": data.get("context_rules", ""),
        "workflow_input": data.get("workflow_input", ""),
        "status": status,
        "start_step_position": data.get("start_step_position", 0),
        "schedule_preset": data.get("schedule"),
        "schedule_time": data.get("schedule_time"),
        "schedule_days": data.get("schedule_days"),
        "schedule_enabled": data.get("enabled", False),
        "schedule_timezone": data.get("timezone"),
    }

    # Convert steps: title → name
    legacy_steps = data.get("steps", [])
    unified_steps = []
    for i, s in enumerate(legacy_steps):
        unified_step: Dict[str, Any] = {
            "position": s.get("position", i),
            "name": s.get("title", s.get("name", "Step")),
            "instruction": s.get("instruction", ""),
            "verification": s.get("verification", ""),
            "step_type": s.get("step_type", "run_command"),
            "config": s.get("config"),
            "code": s.get("code", ""),
            "action_type": s.get("action_type", "agent_instruction"),
            "description": s.get("description", ""),
            "validation_type": s.get("validation_type", "none"),
            "validation_prompt": s.get("validation_prompt", ""),
            "routing_mode": s.get("routing_mode", "static"),
            "routing_prompt": s.get("routing_prompt", ""),
            "on_pass_goto_position": s.get("on_pass_goto_position"),
            "on_fail_goto_position": s.get("on_fail_goto_position"),
            "wait_before_next": s.get("wait_before_next", 0),
            "max_retries": s.get("max_retries", 0),
            "timeout_seconds": s.get("timeout_seconds", 300),
            "require_approval": s.get("require_approval", False),
            "recording_filename": s.get("recording_filename", ""),
            "screenshot_filename": s.get("screenshot_filename", ""),
            "linked_action": s.get("linked_action"),
            "validation_code": s.get("validation_code", ""),
            "linked_project_id": s.get("linked_project_id"),
            "wait_for_continue": s.get("wait_for_continue", False),
        }
        unified_steps.append(unified_step)

    unified["steps"] = unified_steps
    unified["variables"] = data.get("variables", [])
    return unified


def _is_legacy_format(data: Dict[str, Any]) -> bool:
    """Return True if the import data is in legacy StepRunner format (no format_version or '1.0')."""
    fv = data.get("format_version")
    return fv is None or fv == "1.0"


def import_workflow(data: Dict[str, Any], recordings: Optional[Dict[str, bytes]] = None,
                    screenshots: Optional[Dict[str, bytes]] = None) -> int:
    """
    Import a workflow from a portable JSON dict. Optionally restores recording
    and screenshot files from provided binary data.

    Handles both unified format (format_version '2.0' or format 'decisionsai_workflow_v1')
    and legacy StepRunner session format (no format_version or '1.0') by converting
    legacy fields using the migration field mapping.

    Returns the new workflow ID.
    """
    from distr.core.paths import RECORDINGS_DIR, DB_DIR

    recordings = recordings or {}
    screenshots = screenshots or {}

    # Convert legacy format to unified before processing
    if _is_legacy_format(data):
        data = _convert_legacy_to_unified(data)

    # Validate workflow_type if present
    wf_type = data.get("workflow_type", "manual")
    if not validate_workflow_type(wf_type):
        wf_type = "manual"

    with get_session() as db:
        wf = AutoWorkflow(
            name=data.get("name", "Imported Workflow"),
            description=data.get("description", ""),
            status="draft",
            workflow_type=wf_type,
            context_rules=data.get("context_rules") or None,
            workflow_input=data.get("workflow_input") or None,
            start_step_position=data.get("start_step_position", 0),
        )
        db.add(wf)
        db.flush()

        position_to_step = {}
        pass_refs = {}
        fail_refs = {}
        for s_data in data.get("steps", []):
            step = AutoWorkflowStep(
                workflow_id=wf.id,
                position=s_data.get("position", 0),
                name=s_data.get("name", "Step"),
                description=s_data.get("description", ""),
                action_type=s_data.get("action_type", "agent_instruction"),
                instruction=s_data.get("instruction", ""),
                step_type=s_data.get("step_type", "agent_instruction"),
                verification=s_data.get("verification") or None,
                config=json.dumps(s_data["config"]) if isinstance(s_data.get("config"), dict) else (s_data.get("config") or None),
                tool_used=s_data.get("tool_used") or None,
                validation_type=s_data.get("validation_type", "none"),
                validation_prompt=s_data.get("validation_prompt", ""),
                routing_mode=s_data.get("routing_mode", "static"),
                routing_prompt=s_data.get("routing_prompt", ""),
                wait_before_next=s_data.get("wait_before_next", 0),
                max_retries=s_data.get("max_retries", 0),
                timeout_seconds=s_data.get("timeout_seconds", 300),
                require_approval=s_data.get("require_approval", False),
                code=s_data.get("code", ""),
                validation_code=s_data.get("validation_code", ""),
                linked_project_id=s_data.get("linked_project_id"),
                wait_for_continue=s_data.get("wait_for_continue", False),
            )
            db.add(step)
            db.flush()
            position_to_step[step.position] = step
            pass_refs[step.id] = s_data.get("on_pass_goto_position")
            fail_refs[step.id] = s_data.get("on_fail_goto_position")

            # Restore recording file
            rec_name = s_data.get("recording_filename", "")
            if rec_name and rec_name in recordings:
                os.makedirs(RECORDINGS_DIR, exist_ok=True)
                orig_rec_name = rec_name
                rec_path = os.path.join(RECORDINGS_DIR, rec_name)
                # Avoid overwriting existing recordings — add suffix if needed
                if os.path.exists(rec_path):
                    base, ext = os.path.splitext(rec_name)
                    rec_name = f"{base}_{wf.id}{ext}"
                    rec_path = os.path.join(RECORDINGS_DIR, rec_name)
                with open(rec_path, "wb") as f:
                    f.write(recordings[orig_rec_name])
                step.recording_filename = rec_name

            elif rec_name:
                # Recording referenced but not in bundle — keep the name in case it exists locally
                rec_path = os.path.join(RECORDINGS_DIR, rec_name)
                if os.path.isfile(rec_path):
                    step.recording_filename = rec_name

            # Recreate linked Action entity if present in export data
            linked_action_data = s_data.get("linked_action")
            if linked_action_data:
                try:
                    linked_rec = linked_action_data.get("recording_filename", "")
                    # Restore linked action's recording file if in bundle and different from step's
                    if linked_rec and linked_rec in recordings and linked_rec != rec_name:
                        os.makedirs(RECORDINGS_DIR, exist_ok=True)
                        orig_linked_rec = linked_rec
                        linked_rec_path = os.path.join(RECORDINGS_DIR, linked_rec)
                        if os.path.exists(linked_rec_path):
                            base, ext = os.path.splitext(linked_rec)
                            linked_rec = f"{base}_{wf.id}{ext}"
                            linked_rec_path = os.path.join(RECORDINGS_DIR, linked_rec)
                        with open(linked_rec_path, "wb") as f:
                            f.write(recordings[orig_linked_rec])
                    # Use step's recording_filename if linked action's matches the original
                    action_rec = linked_rec if linked_rec else (step.recording_filename or "")
                    new_action = Action(
                        title=linked_action_data.get("title", step.name),
                        description=linked_action_data.get("description", ""),
                        additional_trigger_words=linked_action_data.get("additional_trigger_words", "[]"),
                        is_instruction=linked_action_data.get("is_instruction", False),
                        instruction_text=linked_action_data.get("instruction_text", ""),
                        recording_filename=action_rec,
                    )
                    db.add(new_action)
                    db.flush()
                    step.action_id = new_action.id
                except Exception as e:
                    logger.warning(f"Could not recreate linked action for step {step.id}: {e}")

            # Restore screenshot file
            scr_name = s_data.get("screenshot_filename", "")
            if scr_name and scr_name in screenshots:
                scr_dir = os.path.join(DB_DIR, "workflow_screenshots")
                os.makedirs(scr_dir, exist_ok=True)
                # Rename to new step ID
                ext = os.path.splitext(scr_name)[1] or ".png"
                new_scr_name = f"step_{step.id}{ext}"
                scr_path = os.path.join(scr_dir, new_scr_name)
                with open(scr_path, "wb") as f:
                    f.write(screenshots[scr_name])
                step.screenshot_path = scr_path

        for step in position_to_step.values():
            step.on_pass_goto = _position_to_step_id(pass_refs.get(step.id), position_to_step)
            step.on_fail_goto = _position_to_step_id(fail_refs.get(step.id), position_to_step)

        for v_data in data.get("variables", []):
            db.add(AutoWorkflowVariable(
                workflow_id=wf.id,
                name=v_data.get("name", "var"),
                default_value=v_data.get("default_value", ""),
                description=v_data.get("description", ""),
            ))

        db.commit()
        return wf.id


def import_workflow_bundle(bundle_bytes: bytes) -> int:
    """
    Import a .dwf bundle (ZIP). Extracts workflow.json, recordings, and screenshots,
    then calls import_workflow with the extracted assets.
    """
    import zipfile
    import io

    buf = io.BytesIO(bundle_bytes)
    recordings = {}
    screenshots = {}

    with zipfile.ZipFile(buf, "r") as zf:
        # Read manifest
        data = json.loads(zf.read("workflow.json"))

        # Extract recordings
        for name in zf.namelist():
            if name.startswith("recordings/") and not name.endswith("/"):
                fname = os.path.basename(name)
                recordings[fname] = zf.read(name)
            elif name.startswith("screenshots/") and not name.endswith("/"):
                fname = os.path.basename(name)
                screenshots[fname] = zf.read(name)

    return import_workflow(data, recordings=recordings, screenshots=screenshots)


def _step_id_to_position(step_id: Optional[int], steps: list) -> Optional[int]:
    """Convert a step ID to its position number for export. Returns None if not found or -1 for explicit end."""
    if step_id is None:
        return None
    if step_id == -1:
        return -1
    for s in steps:
        if s.id == step_id:
            return s.position
    return None


def _position_to_step_id(position: Optional[int], position_map: dict) -> Optional[int]:
    if position is None:
        return None
    if position == -1:
        return -1
    step = position_map.get(position)
    return step.id if step else None


def list_presets() -> List[Dict[str, str]]:
    """List available preset files (.dwf bundles and .json) from steprunner/presets/."""
    import zipfile
    import io
    presets_dir = _get_presets_dir()
    if not os.path.isdir(presets_dir):
        return []
    results = []
    for fname in sorted(os.listdir(presets_dir)):
        fpath = os.path.join(presets_dir, fname)
        if fname.endswith(".dwf"):
            try:
                with zipfile.ZipFile(fpath, "r") as zf:
                    data = json.loads(zf.read("workflow.json"))
                has_recordings = any(n.startswith("recordings/") for n in zf.namelist())
                has_screenshots = any(n.startswith("screenshots/") for n in zf.namelist())
                results.append({
                    "filename": fname,
                    "name": data.get("name", fname.replace(".dwf", "")),
                    "description": (data.get("description", "") or "")[:200],
                    "step_count": len(data.get("steps", [])),
                    "has_recordings": has_recordings,
                    "has_screenshots": has_screenshots,
                    "bundle": True,
                })
            except Exception:
                results.append({"filename": fname, "name": fname, "description": "Invalid bundle", "step_count": 0, "bundle": True})
        elif fname.endswith(".json"):
            try:
                with open(fpath, "r") as f:
                    data = json.load(f)
                results.append({
                    "filename": fname,
                    "name": data.get("name", fname.replace(".json", "")),
                    "description": (data.get("description", "") or "")[:200],
                    "step_count": len(data.get("steps", [])),
                    "bundle": False,
                })
            except Exception:
                results.append({"filename": fname, "name": fname, "description": "Invalid JSON", "step_count": 0, "bundle": False})
    return results


def load_preset(filename: str) -> Optional[int]:
    """Load a preset file (.dwf bundle or .json) and import it as a new workflow."""
    presets_dir = _get_presets_dir()
    fpath = os.path.join(presets_dir, filename)
    if not os.path.isfile(fpath):
        return None
    if filename.endswith(".dwf"):
        with open(fpath, "rb") as f:
            return import_workflow_bundle(f.read())
    else:
        with open(fpath, "r") as f:
            data = json.load(f)
        return import_workflow(data)


def save_preset(workflow_id: int, filename: Optional[str] = None) -> Optional[str]:
    """Export a workflow to a .dwf bundle preset file. Returns the filename."""
    import re

    # Check if workflow has any recordings or screenshots
    with get_session() as db:
        wf = db.query(AutoWorkflow).filter(AutoWorkflow.id == workflow_id).first()
        if not wf:
            return None

    bundle_bytes = export_workflow_bundle(workflow_id)
    if not bundle_bytes:
        return None

    if not filename:
        data = export_workflow(workflow_id)
        safe_name = re.sub(r'[^a-z0-9_]', '', (data.get("name", "workflow") or "workflow").lower().replace(" ", "_"))
        filename = f"{safe_name}.dwf"
    elif not filename.endswith(".dwf"):
        filename = filename.rsplit(".", 1)[0] + ".dwf"

    presets_dir = _get_presets_dir()
    os.makedirs(presets_dir, exist_ok=True)
    with open(os.path.join(presets_dir, filename), "wb") as f:
        f.write(bundle_bytes)
    return filename


# ── Serialization ──

def _serialize_workflow(wf: AutoWorkflow) -> Dict[str, Any]:
    steps = sorted(wf.steps, key=lambda s: s.position)
    return {
        "id": wf.id, "name": wf.name,
        "description": wf.description or "",
        "status": wf.status,
        "workflow_type": wf.workflow_type or "manual",
        "schedule_enabled": wf.schedule_enabled,
        "schedule_preset": wf.schedule_preset,
        "schedule_cron": wf.schedule_cron,
        "schedule_time": wf.schedule_time,
        "schedule_days": wf.schedule_days,
        "schedule_timezone": wf.schedule_timezone,
        "next_run_at": wf.next_run_at.isoformat() if wf.next_run_at else None,
        "last_run_at": wf.last_run_at.isoformat() if wf.last_run_at else None,
        "start_step_position": wf.start_step_position or 0,
        "created_date": wf.created_date.isoformat() if wf.created_date else None,
        "modified_date": wf.modified_date.isoformat() if wf.modified_date else None,
        "steps": [_serialize_step(s) for s in steps],
        "variables": [
            {"id": v.id, "name": v.name, "default_value": v.default_value or "", "description": v.description or ""}
            for v in wf.variables
        ],
        "runs": [
            {
                "id": r.id,
                "started_at": r.started_at.isoformat() if r.started_at else None,
                "completed_at": r.completed_at.isoformat() if r.completed_at else None,
                "status": r.status,
                "current_step_id": r.current_step_id,
            }
            for r in sorted(wf.runs, key=lambda r: r.started_at or wf.created_date, reverse=True)[:5]
        ],
    }


def _serialize_step(step: AutoWorkflowStep) -> Dict[str, Any]:
    return {
        "id": step.id, "position": step.position,
        "name": step.name, "description": step.description or "",
        "action_type": step.action_type or "agent_instruction",
        "instruction": step.instruction or "",
        "validation_type": step.validation_type or "none",
        "validation_prompt": step.validation_prompt or "",
        "screenshot_path": step.screenshot_path or "",
        "recording_filename": step.recording_filename or "",
        "action_id": step.action_id,
        "routing_mode": step.routing_mode or "static",
        "routing_prompt": step.routing_prompt or "",
        "on_pass_goto": step.on_pass_goto,
        "on_fail_goto": step.on_fail_goto,
        "wait_before_next": step.wait_before_next or 0,
        "max_retries": step.max_retries or 0,
        "timeout_seconds": step.timeout_seconds or 300,
        "require_approval": step.require_approval or False,
        "status": step.status or "pending",
        "result": step.result,
        "code": step.code or "",
        "validation_code": step.validation_code or "",
        "linked_project_id": step.linked_project_id,
        "wait_for_continue": step.wait_for_continue or False,
    }


# ---------------------------------------------------------------------------
# Legacy StepRunner adapter functions
# ---------------------------------------------------------------------------
# These functions were originally in distr/core/step_runner/service.py and
# operate on the legacy StepRunner DB models.  They are kept here as thin
# adapters so that callers that still reference the old service module can
# simply update their import path.  Over time these should be migrated to
# use the unified AutoWorkflow models.
# ---------------------------------------------------------------------------


def list_sessions(
    limit: int = 50,
    session_type: Optional[str] = None,
    search: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """List recent StepRunner sessions for the API."""
    from distr.core.db.step_runner import StepRunnerSession

    with get_session() as session:
        q = session.query(StepRunnerSession)
        if session_type:
            q = q.filter(StepRunnerSession.session_type == session_type)
        if search and search.strip():
            term = f"%{search.strip()}%"
            q = q.filter(StepRunnerSession.instruction.ilike(term))
        rows = q.order_by(StepRunnerSession.modified_date.desc()).limit(limit).all()
        return [
            {
                "id": r.id,
                "instruction": (r.instruction or "")[:200],
                "status": r.status,
                "session_type": r.session_type or "instruction",
                "schedule": r.schedule,
                "schedule_time": r.schedule_time,
                "schedule_days": r.schedule_days,
                "next_run_at": r.next_run_at.isoformat() if r.next_run_at else None,
                "enabled": r.enabled if r.enabled is not None else True,
                "created_date": r.created_date.isoformat() if r.created_date else None,
            }
            for r in rows
        ]


def get_session_with_steps(session_id: int) -> Optional[Dict[str, Any]]:
    """Get a StepRunner session and its steps as a dict for API response."""
    from distr.core.db.step_runner import StepRunnerSession

    with get_session() as session:
        db_session = session.query(StepRunnerSession).filter(StepRunnerSession.id == session_id).first()
        if not db_session:
            return None
        steps = sorted(db_session.steps, key=lambda s: s.position)
        return {
            "id": db_session.id,
            "instruction": db_session.instruction,
            "status": db_session.status,
            "chat_id": db_session.chat_id,
            "session_type": db_session.session_type or "instruction",
            "schedule": db_session.schedule,
            "next_run_at": db_session.next_run_at.isoformat() if db_session.next_run_at else None,
            "last_run_at": db_session.last_run_at.isoformat() if db_session.last_run_at else None,
            "schedule_time": db_session.schedule_time,
            "schedule_days": getattr(db_session, "schedule_days", None),
            "timezone": db_session.timezone,
            "enabled": db_session.enabled if db_session.enabled is not None else True,
            "created_date": db_session.created_date.isoformat() if db_session.created_date else None,
            "context_rules": getattr(db_session, "context_rules", None),
            "workflow_input": getattr(db_session, "workflow_input", None),
            "runs": _get_session_run_history(session_id, 5),
            "steps": [
                {
                    "id": s.id,
                    "position": s.position,
                    "title": s.title,
                    "instruction": s.instruction,
                    "verification": getattr(s, "verification", None),
                    "status": s.status,
                    "result": s.result,
                    "tool_used": s.tool_used,
                    "step_type": getattr(s, "step_type", "run_command"),
                    "config": getattr(s, "config", None),
                    "code": getattr(s, "code", None),
                }
                for s in steps
            ],
        }


def _get_session_run_history(session_id: int, limit: int = 10) -> List[Dict[str, Any]]:
    """Get run history for a StepRunner session."""
    from distr.core.db.step_runner import StepRunnerRun

    with get_session() as session:
        rows = (
            session.query(StepRunnerRun)
            .filter(StepRunnerRun.session_id == session_id)
            .order_by(StepRunnerRun.started_at.desc())
            .limit(limit)
            .all()
        )
        return [
            {
                "id": r.id,
                "started_at": r.started_at.isoformat() if r.started_at else None,
                "completed_at": r.completed_at.isoformat() if r.completed_at else None,
                "status": r.status,
                "step_results": r.step_results,
            }
            for r in rows
        ]


def update_step_status(
    step_id: int,
    status: Optional[str] = None,
    result: Optional[str] = None,
    tool_used: Optional[str] = None,
    title: Optional[str] = None,
    instruction: Optional[str] = None,
    config: Optional[str] = None,
    code: Optional[str] = None,
) -> bool:
    """Update a StepRunner step's status, result, tool_used, title, instruction, config, or code."""
    from distr.core.db.step_runner import StepRunnerStep

    with get_session() as session:
        step = session.query(StepRunnerStep).filter(StepRunnerStep.id == step_id).first()
        if not step:
            return False
        if status is not None:
            step.status = status
        if result is not None:
            step.result = result
        if tool_used is not None:
            step.tool_used = tool_used
        if title is not None:
            step.title = title
        if instruction is not None:
            step.instruction = instruction
        if config is not None:
            step.config = config
        if code is not None:
            step.code = code
        session.commit()
        return True


def update_session_status(session_id: int, status: str) -> bool:
    """Update a StepRunner session's status."""
    from distr.core.db.step_runner import StepRunnerSession

    with get_session() as session:
        s = session.query(StepRunnerSession).filter(StepRunnerSession.id == session_id).first()
        if not s:
            return False
        s.status = status
        session.commit()
        return True


def delete_session(session_id: int) -> bool:
    """Delete a StepRunner session and its steps and runs."""
    from distr.core.db.step_runner import StepRunnerSession, StepRunnerStep, StepRunnerRun

    with get_session() as session:
        s = session.query(StepRunnerSession).filter(StepRunnerSession.id == session_id).first()
        if not s:
            return False
        session.query(StepRunnerRun).filter(StepRunnerRun.session_id == session_id).delete()
        session.query(StepRunnerStep).filter(StepRunnerStep.session_id == session_id).delete()
        session.delete(s)
        session.commit()
        return True


def add_step_to_session(
    session_id: int,
    title: str,
    instruction: str,
    position: Optional[int] = None,
    step_type: Optional[str] = None,
    config: Optional[str] = None,
    code: Optional[str] = None,
) -> Optional[Any]:
    """Add a step to an existing StepRunner session. Returns the new step or None."""
    from distr.core.db.step_runner import StepRunnerSession, StepRunnerStep

    with get_session() as db:
        s = db.query(StepRunnerSession).filter(StepRunnerSession.id == session_id).first()
        if not s:
            return None
        steps = sorted(s.steps, key=lambda x: x.position)
        pos = position if position is not None else (max((st.position for st in steps), default=-1) + 1)
        step = StepRunnerStep(
            session_id=session_id,
            position=pos,
            title=title or "New step",
            instruction=instruction or "",
            status="pending",
            step_type=step_type or "run_command",
            config=config,
            code=code,
        )
        db.add(step)
        db.commit()
        db.refresh(step)
        return step


def remove_step(session_id: int, step_id: int) -> bool:
    """Remove a step from a StepRunner session."""
    from distr.core.db.step_runner import StepRunnerStep

    with get_session() as db:
        step = (
            db.query(StepRunnerStep)
            .filter(StepRunnerStep.id == step_id, StepRunnerStep.session_id == session_id)
            .first()
        )
        if not step:
            return False
        db.delete(step)
        db.commit()
        return True


def create_workflow_input(
    source_type: str,
    text: str = "",
    title: str = "",
    images: Optional[list] = None,
    attachments: Optional[list] = None,
    metadata: Optional[dict] = None,
) -> dict:
    """Create a WorkflowInput dict suitable for passing to plan_session."""
    return {
        "source_type": source_type,
        "text": text,
        "title": title,
        "images": images or [],
        "attachments": attachments or [],
        "metadata": metadata or {},
    }


_LEGACY_PLAN_PROMPT = """Break down this instruction into ordered, executable sub-steps for an automation agent.

You can use capabilities like opening apps/websites, clicking UI elements, typing, taking screenshots, and checking visible state.
For browser/web tasks, the agent has a playwright_browser tool that runs headless Chrome, automatically captures screenshots + browser console logs (errors, warnings, failed network requests), and sends them to a vision LLM for analysis. Use this for navigating websites, filling forms, testing web pages, and validating visual state.

For UI tasks, follow these rules:
- Keep each step atomic (one action per step).
- Separate app launch from navigation from interaction.
- Use precise UI descriptions (button text, location, panel).
- Include verification checks after important transitions when helpful.
- For web/browser verification steps, the playwright_browser tool will provide both a screenshot analysis AND console log data — write verification criteria that reference both visual state and console output when relevant (e.g. "Page shows dashboard AND no console errors").

Instruction:
{instruction}

Respond with a JSON array of steps. Each step must have:
- "title": short label (e.g., "Open browser")
- "instruction": what to do (e.g., "Open Chrome and navigate to example.com")
- Optional "verification": what to verify after the step (e.g., "Page shows login form AND no console errors or failed requests")
- Optional "type": one of "ui", "data", or "verification"

Example format:
[
  {{"title": "Open browser", "instruction": "Open Google Chrome", "type": "ui"}},
  {{"title": "Go to site", "instruction": "Navigate to https://example.com", "verification": "Example homepage is visible AND no console errors", "type": "ui"}}
]

Return ONLY the JSON array, no markdown or explanation."""


def _legacy_call_llm_for_plan(instruction: str) -> Optional[List[Dict[str, str]]]:
    """Call LLM to break down instruction into steps (legacy StepRunner path)."""
    from distr.core.settings import load_settings_from_db

    settings = load_settings_from_db()
    provider = (
        (settings.get("conversational_llm_provider") or "").strip()
        or (settings.get("agent_provider") or "").strip()
        or "Ollama"
    ).strip().lower()
    model = (
        (settings.get("conversational_llm_model") or "").strip()
        or (settings.get("agent_model") or "").strip()
        or ""
    )
    if not model and provider == "ollama":
        model = "llama3.2"

    prompt = _LEGACY_PLAN_PROMPT.format(instruction=instruction)
    messages = [{"role": "user", "content": prompt}]

    try:
        import litellm
        response = litellm.completion(
            model=_litellm_model(provider, model, settings),
            messages=messages,
            max_tokens=2048,
            temperature=0.3,
        )
        content = response.choices[0].message.content.strip()
        content = re.sub(r"^```\w*\s*", "", content)
        content = re.sub(r"\s*```\s*$", "", content)
        parsed = json.loads(content)
        if not isinstance(parsed, list):
            logger.warning("LLM returned non-array: %s", type(parsed))
            return None
        steps = []
        for i, item in enumerate(parsed):
            if isinstance(item, dict):
                t = str(item.get("title") or item.get("label") or f"Step {i + 1}")
                inst = str(item.get("instruction") or item.get("text") or "")
                verification = str(item.get("verification") or "").strip() or None
                step_type = str(item.get("type") or "").strip().lower() or None
                if step_type not in {"ui", "data", "verification"}:
                    step_type = None
                if inst:
                    step = {"title": t, "instruction": inst}
                    if verification:
                        step["verification"] = verification
                    if step_type:
                        step["type"] = step_type
                    steps.append(step)
            elif isinstance(item, str):
                steps.append({"title": f"Step {i + 1}", "instruction": item})
        return steps if steps else None
    except Exception as e:
        logger.error("Legacy plan LLM call failed: %s", e, exc_info=True)
        return None


def plan_session(
    instruction: str,
    chat_id: Optional[int] = None,
    workflow_input: Optional[dict] = None,
) -> Optional[int]:
    """Create a StepRunner session by breaking down the instruction into steps.

    Uses single-step fast path for simple instructions. Retries LLM once on failure.
    Returns the created session id, or None on failure.
    """
    from distr.core.db.step_runner import StepRunnerSession, StepRunnerStep

    steps_data = None
    if _is_simple_instruction(instruction):
        steps_data = [{"title": "Step 1", "instruction": instruction.strip()}]
    if not steps_data:
        steps_data = _legacy_call_llm_for_plan(instruction)
        if not steps_data:
            steps_data = _legacy_call_llm_for_plan(instruction)  # Retry once
    if not steps_data:
        steps_data = [{"title": "Step 1", "instruction": instruction.strip()}]

    workflow_input_json = None
    if workflow_input is not None:
        try:
            workflow_input_json = json.dumps(workflow_input)
        except (TypeError, ValueError) as exc:
            logger.warning("Failed to serialize workflow_input: %s", exc)

    with get_session() as session:
        db_session = StepRunnerSession(
            instruction=instruction,
            status="planned",
            chat_id=chat_id,
            workflow_input=workflow_input_json,
        )
        session.add(db_session)
        session.flush()
        for i, s in enumerate(steps_data):
            step = StepRunnerStep(
                session_id=db_session.id,
                position=i,
                title=s.get("title", f"Step {i + 1}"),
                instruction=s.get("instruction", ""),
                verification=s.get("verification"),
                status="pending",
                step_type=s.get("step_type", "run_command"),
                config=s.get("config"),
                code=s.get("code"),
            )
            session.add(step)
        session.commit()
        session.refresh(db_session)
        return int(db_session.id)


def create_scheduled_session(
    instruction: str,
    schedule: str,
    chat_id: Optional[int] = None,
    schedule_time: Optional[str] = None,
    timezone: Optional[str] = None,
    schedule_days: Optional[str] = None,
) -> Optional[int]:
    """Create a scheduled StepRunner session."""
    from distr.core.db.step_runner import StepRunnerSession
    from distr.core.workflow.scheduler import schedule_to_cron, _next_run_from_cron

    wf_input = create_workflow_input(source_type="scheduled", text=instruction)
    session_id = plan_session(instruction, chat_id, workflow_input=wf_input)
    if not session_id:
        return None
    cron = schedule_to_cron(schedule.strip(), schedule_time, timezone, schedule_days)
    if not cron:
        return session_id
    with get_session() as db:
        s = db.query(StepRunnerSession).filter(StepRunnerSession.id == session_id).first()
        if s:
            s.session_type = "scheduled"
            s.schedule = schedule.strip()
            s.schedule_time = schedule_time
            s.schedule_days = schedule_days
            s.timezone = timezone
            s.enabled = True
            s.next_run_at = _next_run_from_cron(cron, timezone=timezone, allow_current_minute=True)
            db.commit()
    return session_id


def update_scheduled_session(
    session_id: int,
    enabled: Optional[bool] = None,
    schedule: Optional[str] = None,
    schedule_time: Optional[str] = None,
    schedule_days: Optional[str] = None,
    timezone: Optional[str] = None,
) -> bool:
    """Update a scheduled StepRunner session's enabled state or schedule."""
    from distr.core.db.step_runner import StepRunnerSession
    from distr.core.workflow.scheduler import schedule_to_cron, _next_run_from_cron

    with get_session() as session:
        s = session.query(StepRunnerSession).filter(StepRunnerSession.id == session_id).first()
        if not s or s.session_type != "scheduled":
            return False
        if enabled is not None:
            s.enabled = enabled
        if schedule is not None:
            s.schedule = schedule
        if schedule_time is not None:
            s.schedule_time = schedule_time
        if schedule_days is not None:
            s.schedule_days = schedule_days
        if timezone is not None:
            s.timezone = timezone
        if schedule is not None or schedule_time is not None or schedule_days is not None or timezone is not None:
            cron = schedule_to_cron(s.schedule, s.schedule_time, s.timezone, s.schedule_days)
            if cron:
                s.next_run_at = _next_run_from_cron(cron, from_dt=None, timezone=s.timezone, allow_current_minute=True)
                logger.info("Step Runner: session %d next_run_at set to %s (cron: %s)", session_id, s.next_run_at, cron)
        session.commit()
        return True



def duplicate_session(session_id: int) -> Optional[int]:
    """Duplicate a StepRunner session and its steps. Returns new session id or None."""
    from distr.core.db.step_runner import StepRunnerSession, StepRunnerStep

    with get_session() as session:
        orig = session.query(StepRunnerSession).filter(StepRunnerSession.id == session_id).first()
        if not orig:
            return None
        new_session = StepRunnerSession(
            instruction=orig.instruction,
            status="planned",
            chat_id=orig.chat_id,
            session_type="instruction",
            schedule=None,
            next_run_at=None,
            last_run_at=None,
            enabled=True,
            context_rules=getattr(orig, "context_rules", None),
        )
        session.add(new_session)
        session.flush()
        for step in sorted(orig.steps, key=lambda s: s.position):
            new_step = StepRunnerStep(
                session_id=new_session.id,
                position=step.position,
                title=step.title,
                instruction=step.instruction,
                verification=getattr(step, "verification", None),
                status="pending",
                step_type=getattr(step, "step_type", "run_command"),
                config=getattr(step, "config", None),
                code=getattr(step, "code", None),
            )
            session.add(new_step)
        session.commit()
        session.refresh(new_session)
        return int(new_session.id)
