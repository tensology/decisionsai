"""Production-agent blueprint adherence for the Workflows step runner.

Maps the core-loop + seven-subsystem checklist onto DecisionsAI runs so Mission
Control can show what is enforced, and so budgets / version pins / tool bay
docs stay one module instead of scattered heuristics.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from hashlib import sha256
import json
from typing import Any, Literal


BlueprintStatus = Literal["pass", "partial", "fail"]

TOOL_BAY_VERSION = "1"
BLUEPRINT_CHECKLIST_VERSION = "1"

# Durable checklist: video rule → Decisions module → status.
# Status is the product stance for the Workflows spine (not the voice agent).
BLUEPRINT_CHECKLIST: tuple[dict[str, Any], ...] = (
    {"id": "C1", "subsystem": "core", "rule": "Path decided by code for predictable work", "modules": ["dispatcher", "router", "developer_workflow"], "status": "pass"},
    {"id": "C2", "subsystem": "core", "rule": "Model decides path only for open-ended work", "modules": ["step_iteration", "workflow_agent"], "status": "partial", "ceiling": "Agent freedom is step-local; whole-run ReAct is out of scope."},
    {"id": "C3", "subsystem": "core", "rule": "Clear exit condition", "modules": ["verification", "dogfood_gate", "control_policy"], "status": "pass"},
    {"id": "C4", "subsystem": "core", "rule": "Do not start with full agent autonomy", "modules": ["coordination_plan", "approval_decision"], "status": "pass"},
    {"id": "O1", "subsystem": "orchestration", "rule": "Prompt chaining", "modules": ["developer_workflow"], "status": "pass"},
    {"id": "O2", "subsystem": "orchestration", "rule": "Routing to the right lane", "modules": ["orchestrator_routing", "work_intake"], "status": "pass"},
    {"id": "O3", "subsystem": "orchestration", "rule": "Parallelization", "modules": ["coordination_plan"], "status": "partial", "ceiling": "Dual evaluators only; no general workstream fan-out."},
    {"id": "O4", "subsystem": "orchestration", "rule": "Orchestrator and workers", "modules": ["coordination_plan", "project_cli_backends"], "status": "pass"},
    {"id": "O5", "subsystem": "orchestration", "rule": "Evaluator-optimizer loop", "modules": ["verification", "router"], "status": "pass"},
    {"id": "O6", "subsystem": "orchestration", "rule": "Nest patterns; draw lanes first", "modules": ["coordination_plan", "ticket_contract", "runtime_contract"], "status": "partial"},
    {"id": "M1", "subsystem": "context_memory", "rule": "System prompt at right altitude", "modules": ["standards_memory", "step_executor"], "status": "partial"},
    {"id": "M2", "subsystem": "context_memory", "rule": "Just-in-time context", "modules": ["handoff_packet"], "status": "partial"},
    {"id": "M3", "subsystem": "context_memory", "rule": "Compaction when window fills", "modules": ["handoff_packet", "workspace_memory", "blueprint_adherence"], "status": "pass"},
    {"id": "M4", "subsystem": "context_memory", "rule": "Notes outside the window", "modules": ["workspace_memory", "steering_memory"], "status": "pass"},
    {"id": "M5", "subsystem": "context_memory", "rule": "Working vs long-term memory pump", "modules": ["workspace_memory", "blueprint_adherence"], "status": "pass"},
    {"id": "T1", "subsystem": "tools", "rule": "Fewer sharper tools", "modules": ["tools", "blueprint_adherence"], "status": "partial", "ceiling": "Development tool bay is small; voice/agent tool tree remains larger."},
    {"id": "T2", "subsystem": "tools", "rule": "Descriptions as new-hire docs", "modules": ["blueprint_adherence"], "status": "pass"},
    {"id": "T3", "subsystem": "tools", "rule": "Failures return what went wrong and what to try", "modules": ["step_executor", "blueprint_adherence"], "status": "pass"},
    {"id": "T4", "subsystem": "tools", "rule": "Formats close to model priors", "modules": ["tools"], "status": "pass"},
    {"id": "G1", "subsystem": "guardrails", "rule": "Least privilege", "modules": ["project_ops", "workspace_memory"], "status": "partial", "ceiling": "Shell/python not strongly sandboxed."},
    {"id": "G2", "subsystem": "guardrails", "rule": "Validate in and out", "modules": ["verification", "ticket_contract"], "status": "pass"},
    {"id": "G3", "subsystem": "guardrails", "rule": "Human before irreversible", "modules": ["control_policy", "approval_decision"], "status": "pass"},
    {"id": "G4", "subsystem": "guardrails", "rule": "Breakers for spend/loops/time", "modules": ["control_policy", "blueprint_adherence"], "status": "pass"},
    {"id": "G5", "subsystem": "guardrails", "rule": "Autonomy earned in rings", "modules": ["run_briefing", "approval_decision"], "status": "partial"},
    {"id": "I1", "subsystem": "instruments", "rule": "Flight recording per run", "modules": ["runtime_contract", "orchestration_events"], "status": "pass"},
    {"id": "I2", "subsystem": "instruments", "rule": "Evals score outcomes not paths", "modules": ["blueprint_eval_pack"], "status": "pass"},
    {"id": "I3", "subsystem": "instruments", "rule": "Judge with rubric", "modules": ["verification", "coordination_plan"], "status": "partial", "ceiling": "Human calibration of LLM judges is thin."},
    {"id": "I4", "subsystem": "instruments", "rule": "Drift needle success/takeovers/cost", "modules": ["runtime_contract", "blueprint_adherence"], "status": "pass"},
    {"id": "P1", "subsystem": "power", "rule": "Model routing by step", "modules": ["model_policy", "coordination_plan"], "status": "pass"},
    {"id": "P2", "subsystem": "power", "rule": "Prompt caching", "modules": [], "status": "fail", "ceiling": "Provider-owned; Decisions does not implement a cache regulator."},
    {"id": "P3", "subsystem": "power", "rule": "Parallelize independent calls", "modules": ["coordination_plan"], "status": "partial"},
    {"id": "P4", "subsystem": "power", "rule": "Token/turn budget enforced by code", "modules": ["blueprint_adherence", "control_policy"], "status": "pass"},
    {"id": "H1", "subsystem": "chassis", "rule": "Checkpoints outside the process", "modules": ["dispatcher", "router"], "status": "pass"},
    {"id": "H2", "subsystem": "chassis", "rule": "Safe retries / idempotent actions", "modules": ["router"], "status": "partial"},
    {"id": "H3", "subsystem": "chassis", "rule": "Degraded modes", "modules": ["provider_preflight", "control_policy"], "status": "pass"},
    {"id": "H4", "subsystem": "chassis", "rule": "Version prompts, tools, models", "modules": ["blueprint_adherence", "dispatcher"], "status": "pass"},
)

DEVELOPMENT_TOOL_BAY: tuple[dict[str, str], ...] = (
    {
        "id": "cli",
        "label": "Project CLI",
        "when_to_use": "Read or edit project files, run repository commands, and implement ticket work.",
        "when_not_to_use": "Do not use for irreversible external side effects (payments, mass deletes) without an approval gate.",
        "example": "Inspect the named files, apply the smallest diff that satisfies acceptance criteria, run the relevant tests.",
        "on_failure": "Return the failing command, stderr summary, and the next concrete recovery step (retry once, narrow scope, or ask for input).",
    },
    {
        "id": "playwright",
        "label": "Playwright",
        "when_to_use": "Capture browser evidence or verify UI flows when the ticket requires screenshots.",
        "when_not_to_use": "Skip when the ticket says browser evidence is N/A or the work is research/documentation only.",
        "example": "Open the target URL, assert the acceptance path, save a screenshot path in the result packet.",
        "on_failure": "Report the selector/URL that failed and whether a retry, alternate path, or human login is required.",
    },
    {
        "id": "shell",
        "label": "Shell",
        "when_to_use": "Short repository commands that are not better expressed through the project CLI worker.",
        "when_not_to_use": "Avoid destructive rm/git rewrite and anything outside the project root.",
        "example": "Run a focused test or list matching files.",
        "on_failure": "Include exit code, truncated stderr, and whether the command is safe to retry.",
    },
    {
        "id": "http",
        "label": "HTTP",
        "when_to_use": "Fetch a named URL or API status required by the ticket.",
        "when_not_to_use": "Do not call unpaid paid APIs or mutate production systems without approval.",
        "example": "GET the documented endpoint and record status + body summary.",
        "on_failure": "Return status code, error body excerpt, and whether credentials or a different URL are needed.",
    },
)


def checklist_snapshot() -> dict[str, Any]:
    """Return the durable blueprint checklist for docs and Mission Control."""
    by_status = {"pass": 0, "partial": 0, "fail": 0}
    for item in BLUEPRINT_CHECKLIST:
        by_status[str(item.get("status") or "partial")] = by_status.get(str(item.get("status") or "partial"), 0) + 1
    return {
        "version": BLUEPRINT_CHECKLIST_VERSION,
        "tool_bay_version": TOOL_BAY_VERSION,
        "counts": by_status,
        "checks": [dict(item) for item in BLUEPRINT_CHECKLIST],
    }


def render_tool_bay_docs(*, tool_ids: list[str] | None = None) -> str:
    """Render ACI-style tool docs for worker prompts."""
    wanted = {str(item or "").strip().lower() for item in (tool_ids or []) if str(item or "").strip()}
    lines = [f"Tool bay v{TOOL_BAY_VERSION} (use only these sockets unless the step explicitly adds more):"]
    selected = [tool for tool in DEVELOPMENT_TOOL_BAY if not wanted or tool["id"] in wanted]
    if not selected:
        selected = list(DEVELOPMENT_TOOL_BAY)
    for tool in selected:
        lines.append(
            f"- {tool['id']} ({tool['label']}): {tool['when_to_use']} "
            f"Do not: {tool['when_not_to_use']} "
            f"Example: {tool['example']} "
            f"On failure: {tool['on_failure']}"
        )
    return "\n".join(lines)


def format_tool_failure(*, tool_id: str, error: str, suggestion: str = "") -> str:
    """Normalize tool failures into actionable recovery text for the model."""
    clean_error = " ".join(str(error or "unknown failure").split())
    clean_suggestion = " ".join(str(suggestion or "").split())
    tool = next((item for item in DEVELOPMENT_TOOL_BAY if item["id"] == str(tool_id or "").strip().lower()), None)
    default_try = tool["on_failure"] if tool else "Retry once with a narrower scope, or ask for human input if blocked."
    return (
        f"Tool '{tool_id or 'unknown'}' failed: {clean_error[:800]}. "
        f"Try next: {clean_suggestion[:500] or default_try}"
    )


def default_run_power_budget(*, complexity: str = "medium") -> dict[str, Any]:
    """Code-enforced turn/token breakers for one workflow run."""
    level = str(complexity or "medium").strip().lower()
    if level not in {"low", "medium", "high"}:
        level = "medium"
    turns = {"low": 24, "medium": 48, "high": 80}[level]
    tokens = {"low": 80_000, "medium": 180_000, "high": 320_000}[level]
    return {
        "max_turns": turns,
        "max_tokens": tokens,
        "turns_used": 0,
        "tokens_used": 0,
        "estimated_cost_usd": 0.0,
        "complexity": level,
        "enforcement": "hard",
        "exhausted": False,
    }


def _estimate_token_cost(tokens: int, *, model_provider: str = "") -> float:
    """Cheap heuristic cost meter for the drift needle (not billing-grade)."""
    provider = str(model_provider or "").strip().lower()
    # ponytail: rough USD/1k-token rates for Mission Control only; upgrade to
    # provider invoices when a billing feed exists.
    rate = 0.0 if provider in {"ollama", "local", ""} else 0.002
    if "claude" in provider or provider == "anthropic":
        rate = 0.008
    if provider in {"openai", "codex"}:
        rate = 0.005
    return round((max(0, int(tokens)) / 1000.0) * rate, 6)


def consume_run_power_budget(
    run_data: dict[str, Any],
    *,
    turns: int = 1,
    tokens: int = 0,
    model_provider: str = "",
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """Increment run power usage; return (run_data, exhaustion interrupt dict|None)."""
    data = dict(run_data or {})
    budget = dict(data.get("power_budget") or {})
    if not budget:
        complexity = str(
            (data.get("execution_route") or {}).get("complexity")
            if isinstance(data.get("execution_route"), dict)
            else data.get("complexity")
            or "medium"
        )
        budget = default_run_power_budget(complexity=complexity)
    budget["turns_used"] = int(budget.get("turns_used") or 0) + max(0, int(turns or 0))
    budget["tokens_used"] = int(budget.get("tokens_used") or 0) + max(0, int(tokens or 0))
    delta_cost = _estimate_token_cost(int(tokens or 0), model_provider=model_provider)
    budget["estimated_cost_usd"] = round(float(budget.get("estimated_cost_usd") or 0.0) + delta_cost, 6)
    exhausted = (
        int(budget.get("turns_used") or 0) >= int(budget.get("max_turns") or 0)
        or int(budget.get("tokens_used") or 0) >= int(budget.get("max_tokens") or 0)
    )
    budget["exhausted"] = bool(exhausted)
    data["power_budget"] = budget
    interrupt = None
    if exhausted:
        interrupt = {
            "should_interrupt": True,
            "reason": "The run power budget was exhausted.",
            "question": (
                f"This run used {budget['turns_used']}/{budget['max_turns']} turns and "
                f"{budget['tokens_used']}/{budget['max_tokens']} tokens "
                f"(~${budget['estimated_cost_usd']:.4f}). Raise the budget, change approach, or stop?"
            ),
            "recommendation": "Raise the budget only if acceptance criteria still justify the spend.",
            "options": ["Raise budget", "Change approach", "Stop"],
        }
    return data, interrupt


def build_run_version_pin(
    *,
    workflow_id: int | None,
    workflow_name: str = "",
    workflow_revision: str = "",
    coordination_plan: dict[str, Any] | None = None,
    tool_ids: list[str] | None = None,
    prompt_fingerprint: str = "",
) -> dict[str, Any]:
    """Pin prompt/tool/model/workflow revision for chassis versioning."""
    plan = coordination_plan if isinstance(coordination_plan, dict) else {}
    assignments = plan.get("assignments") if isinstance(plan.get("assignments"), dict) else {}
    routes = []
    for assignment in assignments.values():
        if not isinstance(assignment, dict):
            continue
        route = assignment.get("primary_route") if isinstance(assignment.get("primary_route"), dict) else {}
        routes.append({
            "step_id": assignment.get("step_id"),
            "role": assignment.get("role"),
            "backend": route.get("backend"),
            "model": route.get("model"),
            "model_provider": route.get("model_provider") or route.get("provider"),
        })
    tool_docs = render_tool_bay_docs(tool_ids=tool_ids)
    pin = {
        "pinned_at": datetime.now(timezone.utc).isoformat(),
        "workflow_id": int(workflow_id or 0) or None,
        "workflow_name": str(workflow_name or ""),
        "workflow_revision": str(workflow_revision or ""),
        "tool_bay_version": TOOL_BAY_VERSION,
        "tool_bay_hash": sha256(tool_docs.encode("utf-8", errors="ignore")).hexdigest()[:16],
        "prompt_fingerprint": str(prompt_fingerprint or "")[:64],
        "coordination_strategy": str(plan.get("strategy") or ""),
        "routes": routes,
        "blueprint_checklist_version": BLUEPRINT_CHECKLIST_VERSION,
    }
    raw = json.dumps(pin, sort_keys=True, default=str)
    pin["manifest_hash"] = sha256(raw.encode("utf-8", errors="ignore")).hexdigest()[:16]
    return pin


def build_run_blueprint_snapshot(run_data: dict[str, Any] | None = None) -> dict[str, Any]:
    """Compact Mission Control view of blueprint adherence for one run."""
    data = run_data if isinstance(run_data, dict) else {}
    plan = data.get("coordination_plan") if isinstance(data.get("coordination_plan"), dict) else {}
    budget = data.get("power_budget") if isinstance(data.get("power_budget"), dict) else {}
    interrupt = data.get("interrupt_context") if isinstance(data.get("interrupt_context"), dict) else {}
    version_pin = data.get("version_pin") if isinstance(data.get("version_pin"), dict) else {}
    memory_notes = data.get("memory_compaction_notes") if isinstance(data.get("memory_compaction_notes"), list) else []
    drift = data.get("drift_metrics") if isinstance(data.get("drift_metrics"), dict) else {}
    review_modes = sorted({
        str((assignment or {}).get("review_mode") or "")
        for assignment in (plan.get("assignments") or {}).values()
        if isinstance(assignment, dict) and assignment.get("review_mode")
    })
    return {
        "checklist_version": BLUEPRINT_CHECKLIST_VERSION,
        "orchestration_strategy": str(plan.get("strategy") or "single"),
        "review_modes": review_modes,
        "adaptive_multi_model": bool(plan.get("adaptive_multi_model_enabled")),
        "power_budget": {
            "turns_used": int(budget.get("turns_used") or 0),
            "max_turns": int(budget.get("max_turns") or 0),
            "tokens_used": int(budget.get("tokens_used") or 0),
            "max_tokens": int(budget.get("max_tokens") or 0),
            "estimated_cost_usd": float(budget.get("estimated_cost_usd") or 0.0),
            "exhausted": bool(budget.get("exhausted")),
        },
        "interrupt_line": {
            "waiting_kind": str(data.get("waiting_kind") or ""),
            "reason": str(interrupt.get("reason") or ""),
            "question": str(interrupt.get("question") or data.get("waiting_prompt") or ""),
            "recommendation": str(interrupt.get("recommendation") or ""),
        },
        "memory_notes_written": len(memory_notes),
        "latest_memory_note": (memory_notes[-1] if memory_notes else "")[:500],
        "version_pin": {
            "manifest_hash": version_pin.get("manifest_hash") or "",
            "tool_bay_version": version_pin.get("tool_bay_version") or "",
            "workflow_revision": version_pin.get("workflow_revision") or "",
            "prompt_fingerprint": version_pin.get("prompt_fingerprint") or "",
        },
        "drift": {
            "task_success": drift.get("task_success"),
            "human_takeovers": int(drift.get("human_takeovers") or 0),
            "cost_per_task_usd": float(drift.get("cost_per_task_usd") or budget.get("estimated_cost_usd") or 0.0),
        },
        "checklist_counts": checklist_snapshot()["counts"],
    }


def update_drift_metrics(run_data: dict[str, Any], *, human_takeover: bool = False, completed: bool | None = None) -> dict[str, Any]:
    """Maintain the live drift needle fields on a run."""
    data = dict(run_data or {})
    drift = dict(data.get("drift_metrics") or {})
    if human_takeover:
        drift["human_takeovers"] = int(drift.get("human_takeovers") or 0) + 1
    if completed is not None:
        drift["task_success"] = bool(completed)
    budget = data.get("power_budget") if isinstance(data.get("power_budget"), dict) else {}
    drift["cost_per_task_usd"] = float(budget.get("estimated_cost_usd") or drift.get("cost_per_task_usd") or 0.0)
    data["drift_metrics"] = drift
    return data


def record_memory_compaction_note(run_data: dict[str, Any], note: str) -> dict[str, Any]:
    """Append a compacted long-term note pointer onto the run overlay."""
    data = dict(run_data or {})
    notes = list(data.get("memory_compaction_notes") or [])
    clean = " ".join(str(note or "").split()).strip()
    if clean:
        notes.append(clean[:1000])
    data["memory_compaction_notes"] = notes[-12:]
    return data


def compact_worker_context(
    *,
    role: str,
    objective: str,
    prior_text: str,
    references: list[str] | None = None,
    max_chars: int | None = None,
) -> dict[str, Any]:
    """Summarize a bloated window into notes + pointer restart payload."""
    from distr.core.workflow.handoff_packet import handoff_budget_for_role

    budget = int(max_chars or handoff_budget_for_role(role))
    text = str(prior_text or "")
    if len(text) <= budget:
        return {
            "compacted": False,
            "summary": text,
            "note": "",
            "restart_context": text,
            "references": list(references or []),
        }
    head = text[: max(0, int(budget * 0.55))]
    tail = text[-max(0, int(budget * 0.25)) :]
    note = (
        f"Compacted {role or 'step'} context for objective '{str(objective or '')[:160]}'. "
        f"Preserved head/tail only; inspect references for full evidence."
    )
    refs = list(references or [])[:12]
    restart = (
        f"{note}\n\nObjective:\n{str(objective or '')[:1200]}\n\n"
        f"Compacted working memory:\n{head.rstrip()}\n[...compacted...]\n{tail.lstrip()}\n"
    )
    if refs:
        restart += "\nPointers:\n" + "\n".join(f"- {item}" for item in refs)
    return {
        "compacted": True,
        "summary": restart[:budget],
        "note": note,
        "restart_context": restart[:budget],
        "references": refs,
    }


def ensure_run_blueprint_defaults(run_data: dict[str, Any], *, complexity: str = "medium") -> dict[str, Any]:
    """Seed power budget and empty drift metrics without clobbering existing state."""
    data = deepcopy(run_data or {})
    if not isinstance(data.get("power_budget"), dict) or not data.get("power_budget"):
        data["power_budget"] = default_run_power_budget(complexity=complexity)
    if not isinstance(data.get("drift_metrics"), dict):
        data["drift_metrics"] = {"human_takeovers": 0, "task_success": None, "cost_per_task_usd": 0.0}
    if not isinstance(data.get("memory_compaction_notes"), list):
        data["memory_compaction_notes"] = []
    return data
