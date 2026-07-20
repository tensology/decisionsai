"""Harness intake classification helpers."""

from __future__ import annotations

import re
from typing import Any


UI_TERMS = (
    "ui",
    "ux",
    "frontend",
    "react",
    "css",
    "layout",
    "screen",
    "screenshot",
    "visual",
    "flow",
    "click",
    "button",
    "form",
    "responsive",
    "polish",
    "redesign",
)

UI_ACTION_TERMS = (
    "add",
    "build",
    "change",
    "create",
    "design",
    "fix",
    "implement",
    "improve",
    "make",
    "optimize",
    "polish",
    "redesign",
    "refactor",
    "rename",
    "remove",
    "restyle",
    "style",
    "tighten",
    "tweak",
    "update",
)

UI_PRESERVATION_TERMS = (
    "do not change",
    "do not modify",
    "do not touch",
    "don't change",
    "don't modify",
    "don't touch",
    "keep intact",
    "leave unchanged",
    "preserve",
    "without changing",
    "without modifying",
)

HIGH_RISK_TERMS = {
    "auth": ("auth", "login", "permission", "role"),
    "payments": ("payment", "payments", "billing", "stripe", "invoice"),
    "migration": ("migration", "schema", "database migration"),
    "cross_module": ("cross-module", "cross module", "across modules", "multi-module"),
    "unknown_requirements": ("ambiguous", "unknown requirements", "not sure", "unclear"),
}


def _has_term(text: str, term: str) -> bool:
    """Match intent terms as words/phrases, never arbitrary substrings.

    Short markers such as ``ui`` and ``auth`` previously matched words like
    ``build`` and ``authoritative``, silently promoting unrelated backend work.
    """
    phrase = r"\s+".join(re.escape(part) for part in str(term or "").split())
    return bool(phrase and re.search(rf"(?<!\w){phrase}(?!\w)", text, re.IGNORECASE))


def _has_any(text: str, terms: tuple[str, ...]) -> bool:
    return any(_has_term(text, term) for term in terms)


def _has_ui_work_intent(text: str) -> bool:
    """Distinguish requested UI work from constraints that merely mention UI."""
    for segment in re.split(r"[\n.!?;]+", text or ""):
        segment = segment.strip()
        if not segment or not _has_any(segment, UI_TERMS):
            continue
        if _has_any(segment, UI_PRESERVATION_TERMS):
            continue
        if _has_any(segment, UI_ACTION_TERMS) or _has_term(segment, "ui critical"):
            return True
    return False


def _detect_override(text: str) -> str:
    if "ui critical" in text:
        return "ui_critical"
    if "promote to codex" in text:
        return "promote_to_codex"
    if "demote to cursor" in text:
        return "demote_to_cursor"
    return ""


def classify_intake(text: str, *, file_count: int = 0, todo_count: int = 0) -> dict[str, Any]:
    """Classify incoming work for Hermes routing and quality gates."""
    raw = text or ""
    lowered = raw.lower()
    words = re.findall(r"\w+", lowered)
    ui_heavy = _has_ui_work_intent(lowered)
    risk_flags: list[str] = []
    reasons: list[str] = []

    for flag, terms in HIGH_RISK_TERMS.items():
        if _has_any(lowered, terms):
            risk_flags.append(flag)
            reasons.append(f"Detected {flag.replace('_', ' ')} risk.")

    if (
        "schedule" in lowered
        or "scheduled" in lowered
        or ((" at " in lowered or lowered.startswith("at ")) and ("press " in lowered or "type " in lowered))
    ):
        risk_flags.append("scheduled_desktop_action")
        reasons.append("Detected scheduled desktop action language.")

    override = _detect_override(lowered)
    if override:
        reasons.append(f"Detected override phrase: {override.replace('_', ' ')}.")
    if override == "ui_critical" and "ui_critical" not in risk_flags:
        risk_flags.append("ui_critical")

    score = 0
    if len(words) > 180:
        score += 1
    if len(words) > 420:
        score += 1
    if file_count > 2 or todo_count > 4:
        score += 1
    if ui_heavy and ("new pattern" in lowered or "redesign" in lowered or "polish" in lowered):
        score += 1
    high_risk_count = len([flag for flag in risk_flags if flag in {"auth", "payments", "migration", "cross_module"}])
    if high_risk_count:
        score += high_risk_count

    if score >= 3:
        complexity = "high"
    elif score == 0 and len(words) <= 80 and any(
        term in lowered for term in ("rename", "copy", "label", "button text", "small css")
    ):
        complexity = "low"
    else:
        complexity = "medium"

    if override in {"promote_to_codex", "ui_critical"} or complexity == "high":
        route_pressure = "codex"
    elif override == "demote_to_cursor" and complexity == "low" and not risk_flags:
        route_pressure = "cursor"
    elif ui_heavy and (complexity != "low" or "unknown_requirements" in risk_flags):
        route_pressure = "codex"
    elif complexity == "low":
        route_pressure = "cursor"
    else:
        route_pressure = "policy"

    if ui_heavy:
        reasons.append("Detected UI or flow work.")
    reasons.append(f"Classified complexity as {complexity}.")

    return {
        "ui_heavy": ui_heavy,
        "complexity": complexity,
        "risk_flags": sorted(set(risk_flags)),
        "override": override,
        "route_pressure": route_pressure,
        "reasons": reasons,
    }
