"""Deterministic selection between one direct agent turn and an orchestrated workflow."""

from __future__ import annotations

import re
import hashlib
import json
from typing import Any


_WORKFLOW_MARKERS = (
    "workflow",
    "end to end",
    "end-to-end",
    "multiple steps",
    "multi-step",
    "all stages",
    "across the codebase",
    "across the project",
    "rigorously",
    "independent review",
    "implementation and testing",
    "plan implement test",
)
_COORDINATION_MARKERS = (
    "architecture",
    "migration",
    "refactor",
    "integration",
    "production",
    "security",
    "deploy",
    "release",
    "test",
    "validate",
    "review",
    "multiple",
    "every",
    "all",
)
_DIRECT_OVERRIDES = (
    "do not use a workflow",
    "don't use a workflow",
    "without a workflow",
    "work directly",
    "single direct turn",
)


def workflow_step_skills(role: str, configured: list[Any] | None = None) -> list[str]:
    """Return infrastructure skills available to one orchestrated worker."""
    values: list[Any] = ["decisions-harness-stack", "decisions-headroom", *(configured or [])]
    if str(role or "").strip().lower() in {"review", "final_polish", "reporting"}:
        values.append("agent-watchdog")
    output: list[str] = []
    for value in values:
        skill_id = str(value or "").strip()
        if skill_id and skill_id not in output:
            output.append(skill_id)
    return output


def choose_development_execution_mode(
    instruction: str,
    *,
    assessment: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Choose a direct worker or a thread-owned workflow without an LLM call."""
    assessment = dict(assessment or {})
    if isinstance(assessment.get("proposal_scope"), dict):
        return choose_proposal_execution_mode(assessment["proposal_scope"])
    text = re.sub(r"\s+", " ", str(instruction or "")).strip().lower()
    complexity = str(assessment.get("complexity") or "medium").strip().lower()
    if complexity not in {"low", "medium", "high"}:
        complexity = "medium"
    operational_state = str(
        assessment.get("operational_state") or assessment.get("sentiment") or "neutral"
    ).strip().lower()
    if str(assessment.get("execution_mode_override") or "").strip().lower() == "direct":
        return {
            "mode": "direct",
            "complexity": complexity,
            "operational_state": operational_state,
            "signals": ["user_selected_direct"],
            "reason": "The user chose one direct development-agent turn.",
        }
    if any(marker in text for marker in _DIRECT_OVERRIDES):
        return {
            "mode": "direct",
            "complexity": complexity,
            "operational_state": operational_state,
            "signals": ["explicit_direct_override"],
            "reason": "The instruction explicitly requested direct execution.",
        }

    signals: list[str] = []
    explicit_workflow = any(marker in text for marker in _WORKFLOW_MARKERS)
    if explicit_workflow:
        signals.append("workflow_language")
    # Match on word starts: a bare substring test counted "all" inside
    # "install"/"small" and "test" inside "latest", inflating the scope signal.
    coordination_hits = [
        marker for marker in _COORDINATION_MARKERS
        if re.search(rf"\b{re.escape(marker)}" + (r"\b" if marker == "all" else ""), text)
    ]
    if len(coordination_hits) >= 2:
        signals.append("multi_phase_scope")
    if len(text) >= 1200:
        signals.append("large_instruction")
    if operational_state in {"blocked", "failing", "risk"}:
        signals.append(f"operational_{operational_state}")

    needs_workflow = bool(
        explicit_workflow
        or (complexity == "high" and any(signal in signals for signal in ("multi_phase_scope", "large_instruction")))
    )
    return {
        "mode": "workflow" if needs_workflow else "direct",
        "complexity": complexity,
        "operational_state": operational_state,
        "signals": signals,
        "reason": (
            "The instruction needs coordinated planning, implementation, and independent validation."
            if needs_workflow
            else "The instruction fits one direct development-agent turn."
        ),
    }


def proposal_execution_scope(proposal: dict[str, Any]) -> dict[str, Any]:
    """Only requested work participates in routing, never transport scaffolding."""
    return {
        "title": str(proposal.get("title") or "").strip(),
        "description": str(proposal.get("description") or "").strip(),
        "tasks": [str(task).strip() for task in (proposal.get("tasks") if isinstance(proposal.get("tasks"), list) else []) if str(task).strip()],
    }


def choose_proposal_execution_mode(proposal: dict[str, Any]) -> dict[str, Any]:
    """Versioned, channel-independent rules for an automatic proposal handoff."""
    from distr.core.workflow.phases import RISK_KEYWORDS, classify

    scope = proposal_execution_scope(proposal)
    original = "\n".join([scope["title"], scope["description"], *scope["tasks"]])
    # Exclusions are constraints, not requests to perform the excluded work.
    clauses = re.split(r"(?<=[.!?;])\s+|\n+|\b(?:but|however|instead)\b", original.lower().replace("’", "'"))
    positive = []
    for clause in clauses:
        clause = re.split(r"\b(?:do not|don't|never|without)\b", clause, maxsplit=1)[0]
        if re.match(r"\s*no\s+(?:changes?|writes?|deletions?|deployments?|restores?|restarts?|(?:database|schema|code|file|files|settings)\s+(?:changes?|writes?|edits?))\b", clause):
            continue
        if clause.strip():
            positive.append(clause.strip())
    text = "\n".join(positive)
    risks = [word.strip() for word in RISK_KEYWORDS if word in text]
    explicit = bool(re.search(r"\b(?:use|run|execute|create)\s+(?:(?:a|the|this|an)\s+)?workflow\b|\bindependent review\b|\bend[- ]to[- ]end\b|\bmulti[- ]step\b", text))
    coordination = [word for word in ("architecture", "refactor", "integration", "across the project", "across the codebase", "multiple systems") if word in text]
    complex_work = len(scope["tasks"]) >= 4 or bool(coordination) or len(text) >= 1200
    mode = "workflow" if risks or explicit or complex_work else "direct"
    weight = classify(text, requested="heavy" if complex_work or explicit else None) if mode == "workflow" else None
    signals = [*(f"risk:{word}" for word in risks), *(f"scope:{word}" for word in coordination)]
    if explicit:
        signals.append("explicit_workflow_or_review")
    if len(scope["tasks"]) >= 4:
        signals.append("four_or_more_tasks")
    if len(text) >= 1200:
        signals.append("large_scope")
    return {
        "mode": mode, "complexity": "high" if mode == "workflow" else "low",
        "workflow_class": weight, "policy": "proposal-scope-v1",
        "scope_hash": hashlib.sha256(json.dumps(scope, sort_keys=True).encode()).hexdigest(),
        "signals": signals,
        "reason": ("Requested work matches fixed risk or coordinated-work rules: " + ", ".join(signals)) if mode == "workflow" else "The requested work fits one direct agent turn; no risk or coordinated-work rule matched.",
        "scope_text": text,
    }
