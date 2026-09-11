"""Deterministic selection between one direct agent turn and an orchestrated workflow."""

from __future__ import annotations

import re
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
    text = re.sub(r"\s+", " ", str(instruction or "")).strip().lower()
    assessment = dict(assessment or {})
    complexity = str(assessment.get("complexity") or "medium").strip().lower()
    if complexity not in {"low", "medium", "high"}:
        complexity = "medium"
    operational_state = str(
        assessment.get("operational_state") or assessment.get("sentiment") or "neutral"
    ).strip().lower()
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
    coordination_hits = [marker for marker in _COORDINATION_MARKERS if marker in text]
    if len(coordination_hits) >= 2:
        signals.append("multi_phase_scope")
    if len(text) >= 1200:
        signals.append("large_instruction")
    if operational_state in {"blocked", "failing", "risk"}:
        signals.append(f"operational_{operational_state}")

    needs_workflow = bool(
        explicit_workflow
        or (complexity == "high" and any(signal in signals for signal in ("multi_phase_scope", "large_instruction")))
        or (complexity == "high" and operational_state in {"blocked", "failing", "risk"})
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
