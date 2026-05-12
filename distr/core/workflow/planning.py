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
- "send_to_project_cli" — send the instruction text to the linked project's CLI session (project terminal). Best when you want project-specific CLI execution delegated to the project's terminal.
- "http_request" — make an HTTP request (GET, POST, PUT, DELETE, etc.).
- "execute_code" — run a Python script (code is auto-generated from your instruction). Use for data processing, file I/O, or computation tasks.
- "playwright" — browser automation with Playwright (code is auto-generated from your instruction). Use for web tasks like navigating sites, filling forms, clicking buttons, scraping data, taking screenshots.
- "computer_use" — tight local vision-action loop for mechanical desktop/browser UI tasks when Playwright cannot be used; captures screenshots, asks the configured vision model for one action, executes through the sidecar, then repeats.
- "play_recording" — replay a previously recorded macro/action.

Rules:
- Keep each step atomic (one clear action per step).
- Every step must have an observable verification/success criterion.
- Every step must describe what to do if it cannot make progress.
- Use "playwright" for all web browser tasks (navigate, login, fill forms, scrape, screenshot).
- Use "computer_use" for local GUI tasks that require screen control but are mechanical/repetitive and do not need orchestration reasoning on every click.
- Use "agent_instruction" for desktop app interaction and general-purpose tasks.
- Use "send_to_project_cli" when work should run in the linked project's terminal context.
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
- "verification": observable success criteria for this step
- Optional "stuck_behavior": what to report or collect if the step cannot proceed
- Optional "config": type-specific execution config. For "run_command" include {{"command":"..."}}. For "http_request" include {{"url":"...","method":"GET"}}.

Example:
[
  {{"title": "Open website", "instruction": "Navigate to https://example.com and verify the page loads", "action_type": "playwright", "verification": "Page title contains 'Example'", "stuck_behavior": "Report the URL, visible error, and screenshot path if navigation fails."}},
  {{"title": "Login", "instruction": "Fill in the username and password fields, then click the login button", "action_type": "playwright", "verification": "Authenticated dashboard or expected post-login page is visible."}},
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


_GENERIC_STEP_TITLES = {
    "",
    "step",
    "step 1",
    "instruction",
    "instruction from user",
    "user instruction",
    "task",
    "do task",
    "execute task",
}

_GENERIC_STEP_INSTRUCTIONS = _GENERIC_STEP_TITLES | {
    "do it",
    "complete the task",
    "perform the task",
    "user request",
    "request from user",
}

_ACTION_TIMEOUT_SECONDS = {
    "agent_instruction": 300,
    "computer_use": 180,
    "run_command": 120,
    "send_to_project_cli": 300,
    "http_request": 60,
    "execute_code": 180,
    "playwright": 240,
    "play_recording": 180,
}

_ACTION_MAX_RETRIES = {
    "agent_instruction": 1,
    "computer_use": 1,
    "run_command": 1,
    "send_to_project_cli": 0,
    "http_request": 1,
    "execute_code": 1,
    "playwright": 1,
    "play_recording": 0,
}


def _derive_step_title(instruction: str, index: int) -> str:
    text = re.sub(r"\s+", " ", (instruction or "").strip())
    text = re.sub(r"^(please\s+|can you\s+|could you\s+)", "", text, flags=re.I)
    if not text:
        return f"Step {index + 1}"
    words = text.split()
    title = " ".join(words[:8]).strip(" .,:;")
    if len(title) > 70:
        title = title[:67].rstrip() + "..."
    return title[:1].upper() + title[1:] if title else f"Step {index + 1}"


def _coerce_config(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
            return dict(parsed) if isinstance(parsed, dict) else {}
        except Exception:
            return {}
    return {}


def _default_verification(instruction: str, action_type: str) -> str:
    base = re.sub(r"\s+", " ", (instruction or "").strip()).rstrip(".")
    if len(base) > 180:
        base = base[:177].rstrip() + "..."
    if action_type == "run_command":
        return f"Command exits successfully and output confirms: {base}."
    if action_type == "send_to_project_cli":
        return f"Project CLI acknowledges completion or returns actionable failure details for: {base}."
    if action_type == "http_request":
        return f"HTTP response status and body indicate the requested operation completed: {base}."
    if action_type in {"execute_code", "playwright"}:
        return f"Generated code runs without errors and its output confirms: {base}."
    if action_type == "computer_use":
        return f"Final screen state visibly confirms: {base}."
    if action_type == "play_recording":
        return f"Recorded action completes and the expected resulting UI state is visible: {base}."
    return f"Step result explicitly confirms completion of: {base}."


def _default_stuck_behavior(action_type: str) -> str:
    if action_type == "computer_use":
        return "Stop after 3 unchanged/failed screen attempts and report the visible blocker plus screenshot context."
    if action_type in {"playwright", "execute_code", "run_command"}:
        return "Stop after one retry and report the command/code error, relevant output, and next diagnostic step."
    if action_type == "send_to_project_cli":
        return "Report the CLI response, terminal state, and whether the project shell needs user attention."
    return "Report the blocker, evidence observed, and the exact next input needed from the user."


def _default_config(action_type: str, instruction: str, config: Dict[str, Any]) -> Dict[str, Any]:
    cfg = dict(config or {})
    if action_type == "run_command":
        cfg.setdefault("command", instruction)
        cfg.setdefault("timeout_seconds", _ACTION_TIMEOUT_SECONDS[action_type])
    elif action_type == "http_request":
        url_match = re.search(r"https?://[^\s)>\]\"']+", instruction or "")
        if url_match:
            cfg.setdefault("url", url_match.group(0).rstrip(".,"))
        cfg.setdefault("method", "GET")
        cfg.setdefault("timeout_seconds", _ACTION_TIMEOUT_SECONDS[action_type])
    elif action_type == "computer_use":
        cfg.setdefault("goal", instruction)
        cfg.setdefault("instruction", instruction)
        cfg.setdefault("max_iterations", 12)
        cfg.setdefault("stuck_threshold", 3)
        cfg.setdefault("screenshot_resize_width", 1280)
    elif action_type == "send_to_project_cli":
        cfg.setdefault("instruction", instruction)
    elif action_type in {"execute_code", "playwright"}:
        cfg.setdefault("instruction", instruction)
    return cfg


def _normalize_plan_steps(steps_data: List[Dict[str, Any]], source_instruction: str) -> List[Dict[str, Any]]:
    """Clean LLM-planned steps before persisting them.

    This is deliberately deterministic: the workflow database should never get
    vague labels like "instruction from user" when we have enough instruction
    text to derive a concrete title.
    """
    normalized: List[Dict[str, Any]] = []
    valid_action_types = {
        "agent_instruction",
        "run_command",
        "send_to_project_cli",
        "http_request",
        "execute_code",
        "playwright",
        "computer_use",
        "play_recording",
    }
    for i, raw_step in enumerate(steps_data or []):
        if isinstance(raw_step, str):
            raw_step = {"instruction": raw_step, "action_type": "agent_instruction"}
        if not isinstance(raw_step, dict):
            continue
        instruction = str(raw_step.get("instruction") or raw_step.get("text") or "").strip()
        if not instruction or instruction.lower() in _GENERIC_STEP_INSTRUCTIONS:
            instruction = (source_instruction or "").strip()
        title = str(raw_step.get("title") or raw_step.get("label") or "").strip()
        if title.lower() in _GENERIC_STEP_TITLES or re.fullmatch(r"step\s*\d+", title.lower() or ""):
            title = _derive_step_title(instruction, i)
        action_type = str(raw_step.get("action_type") or "agent_instruction").strip().lower()
        if action_type not in valid_action_types:
            action_type = "agent_instruction"
        step = dict(raw_step)
        step["title"] = title or f"Step {i + 1}"
        step["instruction"] = instruction
        step["action_type"] = action_type
        verification = (
            str(
                raw_step.get("verification")
                or raw_step.get("success_criteria")
                or raw_step.get("validation_prompt")
                or ""
            ).strip()
        )
        if not verification:
            verification = _default_verification(instruction, action_type)
        step["verification"] = verification
        step["validation_prompt"] = verification
        step["validation_type"] = str(raw_step.get("validation_type") or "llm_judgment").strip() or "llm_judgment"
        step["stuck_behavior"] = str(raw_step.get("stuck_behavior") or "").strip() or _default_stuck_behavior(action_type)
        step["max_retries"] = int(raw_step.get("max_retries", _ACTION_MAX_RETRIES[action_type]) or 0)
        step["timeout_seconds"] = int(raw_step.get("timeout_seconds", _ACTION_TIMEOUT_SECONDS[action_type]) or 300)
        step["config"] = _default_config(action_type, instruction, _coerce_config(raw_step.get("config")))
        normalized.append(step)
    return normalized


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
        valid_action_types = {"agent_instruction", "run_command", "send_to_project_cli", "http_request", "execute_code", "playwright", "computer_use", "play_recording"}
        for i, item in enumerate(parsed):
            if isinstance(item, dict):
                title = str(item.get("title") or item.get("label") or f"Step {i + 1}")
                inst = str(item.get("instruction") or item.get("text") or "")
                verification = str(item.get("verification") or "").strip() or None
                stuck_behavior = str(item.get("stuck_behavior") or "").strip() or None
                # Map action_type from LLM response — validate and default to agent_instruction
                action_type = str(item.get("action_type") or "").strip().lower()
                if action_type not in valid_action_types:
                    action_type = "agent_instruction"
                if inst:
                    step = {"title": title, "instruction": inst, "action_type": action_type}
                    if verification:
                        step["verification"] = verification
                    if stuck_behavior:
                        step["stuck_behavior"] = stuck_behavior
                    steps.append(step)
            elif isinstance(item, str):
                steps.append({"title": f"Step {i + 1}", "instruction": item, "action_type": "agent_instruction"})
        return _normalize_plan_steps(steps, instruction) if steps else None
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
    steps_data = _normalize_plan_steps(steps_data, instruction)

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
                config=json.dumps(s.get("config") or {}),
                code=s.get("code"),
                validation_type=s.get("validation_type") or "llm_judgment",
                validation_prompt=s.get("validation_prompt") or s.get("verification"),
                verification=s.get("verification"),
                max_retries=s.get("max_retries", 0),
                timeout_seconds=s.get("timeout_seconds", 300),
            )
            if s.get("stuck_behavior"):
                step.description = f"Stuck behavior: {s['stuck_behavior']}"
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
            paths = item.get("artifact_paths") or []
            block = f"- {title}: {result}"
            if paths:
                block += f"\n  Artifact paths: {', '.join(str(p) for p in paths)}"
            lines.append(block)
        lines.append("")
    lines.extend(
        [
            f"Current step: {step_title or f'Step {step_index + 1}'}",
            f"Task: {step_instruction}",
            "",
            (
                "Execute the task exactly as instructed and return an accurate result for THIS step."
            ),
            (
                "Instruction fidelity is the priority: do not invent extra scope, extra analysis, or adjacent tasks."
            ),
            (
                "Match the level of detail requested by the instruction. "
                "If the instruction asks for detail, provide detail; if it asks for a direct answer, be direct."
            ),
            (
                "Do not use templated status wrappers like 'Step X Complete', 'What I accomplished', "
                "or markdown section headings unless explicitly requested."
            ),
            (
                "Include technical artifacts (paths, IDs, diagnostics, environment info) only when they are "
                "explicitly requested or necessary to complete the instruction."
            ),
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
    steps_data = _normalize_plan_steps(steps_data, instruction)

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
                config=json.dumps(s.get("config") or {}),
                code=s.get("code"),
                validation_type=s.get("validation_type") or "llm_judgment",
                validation_prompt=s.get("validation_prompt") or s.get("verification"),
                verification=s.get("verification"),
                max_retries=s.get("max_retries", 0),
                timeout_seconds=s.get("timeout_seconds", 300),
            )
            if s.get("stuck_behavior"):
                step.description = f"Stuck behavior: {s['stuck_behavior']}"
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
