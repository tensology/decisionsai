"""
Workflow Planning — LLM planning, step generation, code generation.

Extracted from service.py as part of the module decomposition.
"""
import json
import logging
import re
from typing import List, Dict, Any, Optional

from distr.core.db import get_session
from distr.core.db.workflow import (
    AutoWorkflow, AutoWorkflowStep,
)

logger = logging.getLogger(__name__)


# ── LLM Planning ──

PLAN_PROMPT = """Break down this instruction into ordered, executable steps for an automation workflow.

You can choose from these step types (action_type):
- "agent_instruction" — general-purpose: the workflow agent will execute the instruction using any available tools (open apps, click, type, screenshot, browse web). Use this as the default for most desktop and general UI automation.
- "run_command" — execute a shell/command-line command directly.
- "http_request" — make an HTTP request (GET, POST, PUT, DELETE, etc.).
- "execute_code" — run a Python script (code is auto-generated from your instruction). Use for data processing, file I/O, or computation tasks.
- "playwright" — browser automation with Playwright (code is auto-generated from your instruction). Use for web tasks like navigating sites, filling forms, clicking buttons, scraping data, taking screenshots.
- "play_recording" — replay a previously recorded macro/action.

Rules:
- Keep each step atomic (one clear action per step).
- Use "playwright" for all web browser tasks (navigate, login, fill forms, scrape, screenshot).
- Use "agent_instruction" for desktop app interaction and general-purpose tasks.
- Use "execute_code" for data processing, file I/O, or computation.
- Use "run_command" only for simple shell commands (mkdir, cp, ls, app launch).
- For login flows, use "playwright" and include the URL.
- Separate navigation from interaction when possible.
- Add verification steps after important transitions.

Instruction:
{instruction}

Respond with a JSON array of steps. Each step must have:
- "title": short label (e.g., "Open website")
- "instruction": detailed description of what to do
- "action_type": one of the valid types listed above
- Optional "verification": what to check after the step

Example:
[
  {{"title": "Open website", "instruction": "Navigate to https://example.com and verify the page loads", "action_type": "playwright", "verification": "Page title contains 'Example'"}},
  {{"title": "Login", "instruction": "Fill in the username and password fields, then click the login button", "action_type": "playwright"}},
  {{"title": "Verify dashboard", "instruction": "Take a screenshot and confirm the dashboard is visible", "action_type": "playwright", "verification": "Dashboard heading is visible"}}
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
    """Call LLM to break down instruction into steps. Returns list of {title, instruction, action_type} dicts."""
    from distr.core.settings import load_settings_from_db
    from distr.core.llm_override import get_llm_override

    settings = load_settings_from_db()

    # Check for board-level LLM override (orchestrator role)
    override = get_llm_override()
    if override and override.orchestrator_provider:
        provider = override.orchestrator_provider.strip().lower()
        model = (override.orchestrator_model or "").strip()
        if not model and provider == "ollama":
            model = "llama3.2"
    else:
        # Check dedicated workflow LLM, then fall back to conversational
        provider = (
            (settings.get("workflow_llm_provider") or "").strip()
            or (settings.get("conversational_llm_provider") or "").strip()
            or (settings.get("agent_provider") or "").strip()
            or "Ollama"
        ).strip().lower()
        model = (
            (settings.get("workflow_llm_model") or "").strip()
            or (settings.get("conversational_llm_model") or "").strip()
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
        valid_action_types = {"agent_instruction", "run_command", "http_request", "execute_code", "playwright", "play_recording"}
        for i, item in enumerate(parsed):
            if isinstance(item, dict):
                title = str(item.get("title") or item.get("label") or f"Step {i + 1}")
                inst = str(item.get("instruction") or item.get("text") or "")
                verification = str(item.get("verification") or "").strip() or None
                # Map action_type from LLM response — validate and default to agent_instruction
                action_type = str(item.get("action_type") or "").strip().lower()
                if action_type not in valid_action_types:
                    action_type = "agent_instruction"
                if inst:
                    step = {"title": title, "instruction": inst, "action_type": action_type}
                    if verification:
                        step["verification"] = verification
                    steps.append(step)
            elif isinstance(item, str):
                steps.append({"title": f"Step {i + 1}", "instruction": item, "action_type": "agent_instruction"})
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
    planning_mode = "simple_instruction"
    if _is_simple_instruction(instruction):
        steps_data = [{"title": "Step 1", "instruction": instruction.strip(), "action_type": "agent_instruction"}]
    if not steps_data:
        steps_data = _call_llm_for_plan(instruction)
        planning_mode = "llm_planned"
        if not steps_data:
            steps_data = _call_llm_for_plan(instruction)  # Retry once
    if not steps_data:
        steps_data = [{"title": "Step 1", "instruction": instruction.strip(), "action_type": "agent_instruction"}]  # Fallback single-step
        planning_mode = "fallback_single_step"

    # Serialize workflow_input to JSON if provided
    workflow_input_json = None
    if workflow_input is not None:
        try:
            workflow_input_json = json.dumps(workflow_input)
        except (TypeError, ValueError) as exc:
            logger.warning("Failed to serialize workflow_input: %s", exc)

    logger.info("plan_workflow: planning_mode=%s, steps=%d for instruction: %.80s",
                planning_mode, len(steps_data), instruction)

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
                action_type=s.get("action_type", "agent_instruction"),
                status="pending",
                step_type=s.get("action_type", "agent_instruction"),
                config=s.get("config"),
                code=s.get("code"),
            )
            # Map LLM verification output to proper validation fields
            verification = s.get("verification")
            if verification and verification.strip():
                step.validation_prompt = verification.strip()
                step.validation_type = "llm_judgment"
            db.add(step)
        db.commit()
        db.refresh(wf)
        return int(wf.id)


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
    section before the workflow header.

    When ``continuation_input`` is non-empty, it is appended as a ``[USER INPUT]``
    section after the main prompt body.

    All ``{{variable}}`` placeholders are resolved via
    ``variable_resolver.resolve_variables()`` before returning.

    ``session_instruction`` is accepted as a backward-compatible alias for
    ``workflow_description``.

    **Validates: Requirements 6.1, 6.2, 6.3, 6.4, 6.5**
    """
    from distr.core.workflow_engine.variable_resolver import resolve_variables

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
        f"[WORKFLOW ENGINE] Executing step {step_index + 1} of {total_steps}.",
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
        steps_data = [{"title": "Step 1", "instruction": instruction.strip(), "action_type": "agent_instruction"}]
    if not steps_data:
        steps_data = _call_llm_for_plan(instruction)
        if not steps_data:
            steps_data = _call_llm_for_plan(instruction)  # Retry once
    if not steps_data:
        steps_data = [{"title": "Step 1", "instruction": instruction.strip(), "action_type": "agent_instruction"}]  # Fallback

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
                action_type=s.get("action_type", "agent_instruction"),
                status="pending",
                step_type=s.get("action_type", "agent_instruction"),
                config=s.get("config"),
                code=s.get("code"),
            )
            # Map LLM verification output to proper validation fields
            verification = s.get("verification")
            if verification and verification.strip():
                step.validation_prompt = verification.strip()
                step.validation_type = "llm_judgment"
            db.add(step)
            db.flush()
            created.append({
                "id": step.id,
                "position": step.position,
                "name": step.name,
                "instruction": step.instruction,
                "action_type": step.action_type,
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
    from distr.core.workflow_engine.code_generator import CodeGeneratorService
    from distr.core.workflow_engine.step_types import StepType

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
    from distr.core.workflow_engine.test_loop import TestLoopService
    from distr.core.workflow_engine.step_types import StepType

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
