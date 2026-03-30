"""
Step Runner Service

Breaks down big instructions into executable sub-steps using an LLM,
and provides execution orchestration.
"""
import json
import logging
import re
from typing import List, Dict, Any, Optional

from distr.core.db import get_session
from distr.core.db.step_runner import StepRunnerSession, StepRunnerStep
from distr.core.settings import load_settings_from_db

logger = logging.getLogger(__name__)

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


def _call_llm_for_plan(instruction: str) -> Optional[List[Dict[str, str]]]:
    """Call LLM to break down instruction into steps. Returns list of {title, instruction} dicts."""
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
        logger.error("Step Runner plan LLM call failed: %s", e, exc_info=True)
        return None


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


def create_scheduled_session(
    instruction: str,
    schedule: str,
    chat_id: Optional[int] = None,
    schedule_time: Optional[str] = None,
    timezone: Optional[str] = None,
    schedule_days: Optional[str] = None,
) -> Optional[int]:
    """
    Create a scheduled session. Uses LLM to break into steps (or single-step if simple), then sets schedule.

    schedule: preset ("daily", "hourly", "weekly") or cron ("0 9 * * *") or "daily:08:00"
    schedule_time: "08:00" for 8am (used with daily/weekly)
    schedule_days: for weekly, "1,3,5" = Mon, Wed, Fri (0=Sun, 1=Mon, ..., 6=Sat)
    timezone: e.g. "America/New_York"
    """
    # Auto-create a WorkflowInput with source_type="scheduled"
    wf_input = create_workflow_input(
        source_type="scheduled",
        text=instruction,
    )
    session_id = plan_session(instruction, chat_id, workflow_input=wf_input)
    if not session_id:
        return None
    cron = _schedule_to_cron(schedule, schedule_time, timezone, schedule_days)
    if not cron:
        return session_id
    from datetime import datetime
    from distr.core.step_runner.scheduler import _next_run_from_cron
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


def _schedule_to_cron(
    schedule: Optional[str],
    schedule_time: Optional[str] = None,
    timezone: Optional[str] = None,
    schedule_days: Optional[str] = None,
) -> Optional[str]:
    """Convert preset to cron. Supports daily:HH:MM format."""
    if not schedule or not schedule.strip():
        return None
    from distr.core.step_runner.scheduler import schedule_to_cron
    return schedule_to_cron(schedule.strip(), schedule_time, timezone, schedule_days)


def create_workflow_input(
    source_type: str,
    text: str = "",
    title: str = "",
    images: Optional[list] = None,
    attachments: Optional[list] = None,
    metadata: Optional[dict] = None,
) -> dict:
    """Create a WorkflowInput dict suitable for passing to plan_session.

    Returns a plain dict that can be JSON-serialized and stored on the session's
    ``workflow_input`` column.
    """
    return {
        "source_type": source_type,
        "text": text,
        "title": title,
        "images": images or [],
        "attachments": attachments or [],
        "metadata": metadata or {},
    }


def plan_session(
    instruction: str,
    chat_id: Optional[int] = None,
    workflow_input: Optional[dict] = None,
) -> Optional[int]:
    """
    Create a Step Runner session by breaking down the instruction into steps.

    Uses single-step fast path for simple instructions. Retries LLM once on failure.
    Returns the created session with steps, or None on failure.

    If *workflow_input* is provided (a dict matching the WorkflowInput schema),
    it is serialized to JSON and stored on the session's ``workflow_input`` column.
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


def build_step_context_prompt(
    *,
    step_index: int,
    total_steps: int,
    session_instruction: str,
    step_title: str,
    step_instruction: str,
    prior_results: List[Dict[str, str]],
    context_rules: str = "",
) -> str:
    """Build a context-aware prompt for step execution.
    
    For single-step sessions with no prior results and no context_rules,
    returns the raw instruction so fast-action detection (e.g. 'run action X')
    works correctly without the wrapper text polluting regex group captures.
    
    When context_rules is non-empty, it is prepended as a [CONTEXT AND RULES]
    section before the step runner header.
    """
    # Single step, no prior context, no context rules: send raw instruction
    # so fast-action detector can cleanly extract action names, commands, etc.
    if total_steps == 1 and not prior_results and not context_rules:
        return step_instruction

    parts: List[str] = []

    if context_rules:
        parts.append(f"[CONTEXT AND RULES]\n{context_rules}\n")

    lines = [
        f"[STEP RUNNER] Executing step {step_index + 1} of {total_steps}.",
        f"Overall goal: {session_instruction}",
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
    return "\n".join(parts)


def get_session_with_steps(session_id: int) -> Optional[Dict[str, Any]]:
    """Get a session and its steps as a dict for API response."""
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
            "runs": get_run_history(session_id, 5),
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


def list_sessions(limit: int = 50, session_type: Optional[str] = None, search: Optional[str] = None) -> List[Dict[str, Any]]:
    """List recent sessions for the API. Filter by session_type (instruction/scheduled) or search."""
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
    """Update a step's status, result, tool_used, title, instruction, config, or code."""
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


def reorder_steps(session_id: int, step_ids: List[int]) -> bool:
    """Reorder steps by new position order (step_ids)."""
    with get_session() as session:
        db_session = session.query(StepRunnerSession).filter(StepRunnerSession.id == session_id).first()
        if not db_session:
            return False
        for pos, step_id in enumerate(step_ids):
            step = session.query(StepRunnerStep).filter(
                StepRunnerStep.id == step_id,
                StepRunnerStep.session_id == session_id,
            ).first()
            if step:
                step.position = pos
        session.commit()
        return True


def delete_session(session_id: int) -> bool:
    """Delete a session and its steps and runs."""
    from distr.core.db.step_runner import StepRunnerRun, StepRunnerStep
    with get_session() as session:
        s = session.query(StepRunnerSession).filter(StepRunnerSession.id == session_id).first()
        if not s:
            return False
        session.query(StepRunnerRun).filter(StepRunnerRun.session_id == session_id).delete()
        session.query(StepRunnerStep).filter(StepRunnerStep.session_id == session_id).delete()
        session.delete(s)
        session.commit()
        return True


def duplicate_session(session_id: int) -> Optional[int]:
    """Duplicate a session and its steps. Returns new session id or None."""
    with get_session() as session:
        orig = session.query(StepRunnerSession).filter(StepRunnerSession.id == session_id).first()
        if not orig:
            return None
        new_session = StepRunnerSession(
            instruction=orig.instruction,
            status="planned",
            chat_id=orig.chat_id,
            session_type="instruction",  # Duplicate as one-time for safety
            schedule=None,
            next_run_at=None,
            last_run_at=None,
            enabled=True,
            context_rules=getattr(orig, "context_rules", None),
            # workflow_input intentionally NOT copied — duplicate is a new trigger
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


def get_run_history(session_id: int, limit: int = 10) -> List[Dict[str, Any]]:
    """Get run history for a scheduled session."""
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


def update_session_status(session_id: int, status: str) -> bool:
    """Update a session's status."""
    with get_session() as session:
        s = session.query(StepRunnerSession).filter(StepRunnerSession.id == session_id).first()
        if not s:
            return False
        s.status = status
        session.commit()
        return True


def get_or_create_audit_session(chat_id: int) -> Optional[int]:
    """Get or create an audit session for a chat. Returns session_id (int) to avoid passing detached ORM objects across session boundaries."""
    with get_session() as db:
        s = (
            db.query(StepRunnerSession)
            .filter(
                StepRunnerSession.chat_id == chat_id,
                StepRunnerSession.session_type == "audit",
            )
            .order_by(StepRunnerSession.modified_date.desc())
            .first()
        )
        if s:
            return s.id
        s = StepRunnerSession(
            instruction=f"Audit log for chat {chat_id}",
            status="in_progress",
            chat_id=chat_id,
            session_type="audit",
        )
        db.add(s)
        db.commit()
        db.refresh(s)
        return s.id


def append_audit_step(
    chat_id: int,
    tool_name: str,
    instruction: str,
    result: str,
    status: str = "completed",
    user_text: str = None,
    routing_path: str = None,
) -> bool:
    """Append a tool execution as a step to the chat's audit session. Creates session if needed."""
    try:
        session_id = get_or_create_audit_session(chat_id)
        if not session_id:
            return False
        with get_session() as db:
            s = db.query(StepRunnerSession).filter(StepRunnerSession.id == session_id).first()
            if not s:
                return False
            max_pos = max((st.position for st in s.steps), default=-1)
            # Embed routing telemetry in the instruction field for audit visibility
            inst = instruction[:500] if instruction else tool_name
            if routing_path:
                inst = f"[{routing_path}] {inst}"
            step = StepRunnerStep(
                session_id=s.id,
                position=max_pos + 1,
                title=tool_name.replace("_", " ").title(),
                instruction=inst[:500],
                status=status,
                result=(result[:2000] + "..." if len(result) > 2000 else result) if result else None,
                tool_used=tool_name,
            )
            db.add(step)
            db.commit()
        return True
    except Exception as e:
        logger.warning("append_audit_step failed: %s", e)
        return False


def add_step_to_session(
    session_id: int,
    title: str,
    instruction: str,
    position: Optional[int] = None,
    step_type: Optional[str] = None,
    config: Optional[str] = None,
    code: Optional[str] = None,
) -> Optional[StepRunnerStep]:
    """Add a step to an existing session. Returns the new step or None."""
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
    """Remove a step from a session."""
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


def update_scheduled_session(
    session_id: int,
    enabled: Optional[bool] = None,
    schedule: Optional[str] = None,
    schedule_time: Optional[str] = None,
    schedule_days: Optional[str] = None,
    timezone: Optional[str] = None,
) -> bool:
    """Update a scheduled session's enabled state or schedule."""
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
            from distr.core.step_runner.scheduler import schedule_to_cron, _next_run_from_cron
            cron = schedule_to_cron(s.schedule, s.schedule_time, s.timezone, s.schedule_days)
            if cron:
                # Always base off utcnow when user explicitly saves a schedule;
                # allow_current_minute=True so saving at 09:00 fires today, not tomorrow.
                s.next_run_at = _next_run_from_cron(cron, from_dt=None, timezone=s.timezone, allow_current_minute=True)
                logger.info("Step Runner: session %d next_run_at set to %s (cron: %s)", session_id, s.next_run_at, cron)
        session.commit()
        return True
