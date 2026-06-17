"""Per-step harness suggestions for workflow loop steps."""

from __future__ import annotations

import json
import re
from typing import Any

from distr.core.workflow.loop_catalog import LOOP_ARCHETYPES, infer_loop_archetype
from distr.core.workflow.planning import parse_loop_contract

# ECC skill bundles keyed by loop archetype (ids under plugins/ecc/skills or skills/).
ARCHETYPE_SKILL_BUNDLES: dict[str, list[str]] = {
    "check_fix_until_green": ["tdd-workflow", "build-error-resolver", "verification-loop"],
    "review_cleanup": ["code-reviewer", "refactor-cleaner", "finishing-a-development-branch"],
    "incremental_ship": ["tdd-workflow", "ceo-scope-review", "finishing-a-development-branch"],
    "ship_with_ci": ["tdd-workflow", "build-error-resolver", "finishing-a-development-branch"],
    "watch_maintain": ["systematic-debugging", "qa-tester"],
    "event_gate": ["tdd-workflow", "qa-tester"],
}

ACTION_TYPES = (
    "send_to_project_cli",
    "run_command",
    "agent_instruction",
    "playwright",
    "computer_use",
    "execute_code",
    "http_request",
)

UI_TOOL_IDS = ("playwright", "computer_use", "browser_use", "cli", "other")

BACKEND_BY_ACTION: dict[str, str] = {
    "send_to_project_cli": "codex",
    "run_command": "",
    "agent_instruction": "",
    "playwright": "",
    "computer_use": "",
}


def _ui_tools_from_harness(action_type: str, tools: list[str] | None = None) -> list[str]:
    """Map harness/internal tool ids to workflow step editor checkboxes."""
    ui: list[str] = []
    for raw in tools or []:
        token = str(raw or "").strip().lower()
        if token in UI_TOOL_IDS:
            if token not in ui:
                ui.append(token)
        elif token in {"browser", "playwright"}:
            for item in ("playwright", "browser_use"):
                if item not in ui:
                    ui.append(item)
        elif token in {"sidecar", "vision", "computer_use"}:
            if "computer_use" not in ui:
                ui.append("computer_use")
        elif token in {"project_cli", "cli", "ide"}:
            if "cli" not in ui:
                ui.append("cli")
        elif token in {"shell", "orchestrator", "workflow_agent"}:
            if "other" not in ui:
                ui.append("other")
    if ui:
        return ui
    action = (action_type or "").strip().lower()
    if action == "computer_use":
        return ["computer_use"]
    if action == "playwright":
        return ["playwright", "browser_use"]
    if action == "send_to_project_cli":
        return ["cli"]
    if action in {"run_command", "agent_instruction"}:
        return ["other"]
    return []


def derive_action_type_from_ui_tools(tools: list[str] | None) -> str:
    selected = [str(t or "").strip().lower() for t in (tools or []) if str(t or "").strip()]
    if "computer_use" in selected:
        return "computer_use"
    if "playwright" in selected or "browser_use" in selected:
        return "playwright"
    if "cli" in selected:
        return "send_to_project_cli"
    if "other" in selected:
        return "agent_instruction"
    return "send_to_project_cli"


def _instruction_needs_clarification(instruction: str) -> bool:
    text = (instruction or "").strip().lower()
    if len(text) < 12:
        return True
    vague = ("tbd", "todo", "figure out", "something", "maybe", "not sure", "???")
    return any(token in text for token in vague)


def suggest_step_harness(
    *,
    instruction: str = "",
    action_type: str = "",
    archetype: str = "",
    loop_contract: dict[str, Any] | None = None,
    step_role: str = "",
) -> dict[str, Any]:
    """Return orchestrator-facing harness defaults for a workflow step."""
    loop_contract = loop_contract or {}
    parsed = parse_loop_contract(instruction)
    archetype = archetype or infer_loop_archetype(instruction, parsed)
    archetype_spec = LOOP_ARCHETYPES.get(archetype) or LOOP_ARCHETYPES["check_fix_until_green"]
    lower = (instruction or "").lower()
    role = (step_role or "").strip().lower()

    if not action_type:
        if role == "check" or "check command" in lower or "between iterations run" in lower:
            action_type = archetype_spec.get("check_action") or "run_command"
        elif any(w in lower for w in ("review", "report", "evaluate", "exit when")):
            action_type = "agent_instruction"
        elif any(w in lower for w in ("browser", "playwright", "navigate", "http://", "https://")):
            action_type = "playwright"
        elif any(w in lower for w in ("screenshot", "desktop", "click", "gui")):
            action_type = "computer_use"
        else:
            action_type = archetype_spec.get("primary_action") or "send_to_project_cli"

    skills = list(ARCHETYPE_SKILL_BUNDLES.get(archetype) or [])
    if action_type == "playwright":
        skills = ["webapp-testing", "e2e-testing"]
    elif action_type == "run_command":
        skills = ["verification-loop", "tdd-workflow"]

    check_command = str(loop_contract.get("check_command") or parsed.get("check_command") or "").strip()
    config: dict[str, Any] = {}
    if action_type == "run_command" and check_command:
        config["command"] = check_command

    complexity = "medium"
    model = "auto"
    if action_type in ("playwright", "computer_use"):
        complexity = "low"
        model = "auto"
        config["complexity"] = complexity
        config["prefer_fast_vision_model"] = True

    backend_id = BACKEND_BY_ACTION.get(action_type) or ""
    if action_type == "send_to_project_cli":
        if any(w in lower for w in ("cursor", "ide")):
            backend_id = "cursor_ide"
        elif "claude" in lower:
            backend_id = "claude_code"
        elif "hermes agent" in lower or "hermes operator" in lower:
            backend_id = "hermes_agent"
        elif "cline" in lower:
            backend_id = "cline"

    guardrail = ""
    contract_guardrails = loop_contract.get("guardrails") or []
    if isinstance(contract_guardrails, list) and contract_guardrails:
        guardrail = "\n".join(f"- {str(item).strip()}" for item in contract_guardrails if str(item).strip())

    validation_type = "llm_judgment"
    validation_prompt = ""
    if action_type == "run_command":
        validation_type = "exit_code"
        validation_prompt = "Command exits 0."
    elif "exit when" in lower or role == "evaluate":
        exit_when = str(loop_contract.get("exit_when") or parsed.get("exit_when") or "").strip()
        validation_prompt = exit_when or "Step outcome matches the loop exit criteria."

    clarify_questions: list[str] = []
    needs_clarify = _instruction_needs_clarification(instruction)
    if needs_clarify:
        clarify_questions = [
            "What project or repo should this step target?",
            "What does done look like for this step?",
            "Which executor should run it (CLI, IDE, browser, or Hermes Agent)?",
        ]

    tools: list[str] = []
    if action_type == "send_to_project_cli":
        tools = ["project_cli"]
    elif action_type == "run_command":
        tools = ["shell"]
    elif action_type == "playwright":
        tools = ["browser", "playwright"]
    elif action_type == "computer_use":
        tools = ["browser", "sidecar", "vision"]
    elif action_type == "agent_instruction":
        tools = ["orchestrator", "workflow_agent"]

    return {
        "action_type": action_type,
        "backend_id": backend_id,
        "model": model,
        "complexity": complexity,
        "skills": skills,
        "tools": tools,
        "ui_tools": _ui_tools_from_harness(action_type, tools),
        "guardrail": guardrail,
        "validation_type": validation_type,
        "validation_prompt": validation_prompt,
        "wait_for_continue": bool(needs_clarify or "ask me" in lower or "clarify" in lower),
        "clarify_questions": clarify_questions,
        "config": config,
        "archetype": archetype,
        "rationale": (
            f"Suggested for {archetype} loop step using {action_type} "
            f"(complexity={complexity}). "
            + (
                "Use a fast vision/UI model for browser or desktop control; escalate only for planning."
                if action_type in ("playwright", "computer_use")
                else "Skills equip the executor; validation and wait_for_continue gate human clarification."
            )
        ),
    }


def suggest_step_harness_llm(
    *,
    instruction: str = "",
    guardrail: str = "",
    validation_prompt: str = "",
    loop_contract: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Use the orchestrator LLM to pick skills/tools from step text. Falls back to heuristics."""
    loop_contract = loop_contract or {}
    baseline = suggest_step_harness(
        instruction=instruction,
        loop_contract=loop_contract,
    )
    fallback = {
        **baseline,
        "skills": list(baseline.get("skills") or []),
        "ui_tools": _ui_tools_from_harness(
            str(baseline.get("action_type") or ""),
            list(baseline.get("tools") or []),
        ),
        "source": "heuristic",
    }
    try:
        from distr.core.orchestrator import get_orchestrator_role_model
        from distr.core.settings import load_settings_from_db
        from distr.core.skills.catalog import orchestrator_skill_catalog
        from distr.core.workflow.planning import _litellm_model

        import litellm

        settings = load_settings_from_db()
        provider, model = get_orchestrator_role_model("orchestrator")
        if not provider and not model:
            return fallback

        catalog = orchestrator_skill_catalog(limit=120)
        allowed_ids = {str(row.get("id") or "") for row in catalog if row.get("id")}
        prompt = {
            "task": (
                "Choose bundled skills and additional tools for one workflow loop step. "
                "Use only skill ids from available_skills."
            ),
            "instruction": (instruction or "").strip()[:4000],
            "guardrail": (guardrail or "").strip()[:2000],
            "validation": (validation_prompt or "").strip()[:2000],
            "loop_contract": loop_contract,
            "available_skills": catalog,
            "allowed_tools": list(UI_TOOL_IDS),
            "response_schema": {
                "skills": ["skill_id"],
                "tools": list(UI_TOOL_IDS),
                "wait_for_continue": "boolean",
                "rationale": "string",
            },
        }
        response = litellm.completion(
            model=_litellm_model(provider.strip().lower(), model, settings),
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are the workflow orchestrator. "
                        "Pick the smallest useful skill set and tool checkboxes for this step. "
                        "Set wait_for_continue true only when the step text is too vague to run safely "
                        "without human clarification. Respond with valid JSON only."
                    ),
                },
                {"role": "user", "content": json.dumps(prompt, ensure_ascii=False)},
            ],
            max_tokens=600,
            temperature=0.2,
        )
        content = (response.choices[0].message.content or "").strip()
        content = re.sub(r"^```\w*\s*", "", content)
        content = re.sub(r"\s*```\s*$", "", content)
        parsed = json.loads(content)
        if not isinstance(parsed, dict):
            return fallback

        skills = [
            str(item).strip()
            for item in (parsed.get("skills") or [])
            if str(item).strip() in allowed_ids
        ]
        ui_tools = [
            str(item).strip().lower()
            for item in (parsed.get("tools") or [])
            if str(item).strip().lower() in UI_TOOL_IDS
        ]
        ui_tools = list(dict.fromkeys(ui_tools))
        if not skills:
            skills = list(baseline.get("skills") or [])
        if not ui_tools:
            ui_tools = _ui_tools_from_harness(
                str(baseline.get("action_type") or ""),
                list(baseline.get("tools") or []),
            )
        action_type = derive_action_type_from_ui_tools(ui_tools)
        return {
            "action_type": action_type,
            "skills": skills,
            "ui_tools": ui_tools,
            "tools": ui_tools,
            "wait_for_continue": bool(parsed.get("wait_for_continue")),
            "rationale": str(parsed.get("rationale") or "").strip(),
            "source": "orchestrator_llm",
        }
    except Exception:
        return fallback


def merge_step_harness_config(existing: dict[str, Any] | None, suggestion: dict[str, Any]) -> dict[str, Any]:
    """Merge harness suggestion into step config without clobbering explicit user values."""
    base = dict(existing or {})
    for key in ("backend_id", "model", "complexity", "skills", "tools", "clarify_questions", "guardrail"):
        if key not in base or base.get(key) in (None, "", []):
            if suggestion.get(key) not in (None, "", []):
                base[key] = suggestion[key]
    if not base.get("command") and suggestion.get("config", {}).get("command"):
        base.setdefault("command", suggestion["config"]["command"])
    return base
