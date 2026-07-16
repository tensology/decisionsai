"""Compile explicit channel instructions into neutral workflow route constraints."""

from __future__ import annotations

import re
from typing import Any


_BACKEND_ALIASES = {
    "codex": "codex",
    "claude": "claude_code",
    "claude code": "claude_code",
    "kilo": "kilo",
    "kilo code": "kilo",
    "pi": "pi",
    "ollama": "pi",
}

_ROLE_TERMS = {
    "planning": (
        "plan", "planning", "scope", "scoping", "brief", "design",
        "ingest", "requirements", "project context",
    ),
    "implementation": ("implement", "implementation", "build", "coding", "development"),
    "review": ("review", "validation", "validate", "verification", "verify", "qa", "audit"),
    "deployment": ("deploy", "deployment", "publish", "release", "shipping"),
}


def infer_step_role(step: dict[str, Any] | None) -> str:
    """Return a stable role for a workflow step without depending on a vendor."""
    step = step or {}
    config = step.get("config") if isinstance(step.get("config"), dict) else {}
    explicit = str(config.get("step_role") or config.get("role") or "").strip().lower()
    if explicit in _ROLE_TERMS:
        return explicit
    # A step title is the strongest declaration of purpose. Looking at the
    # entire instruction first misclassifies titles such as "Write plan.md"
    # merely because their acceptance text says "before implementation".
    for keys in (("name", "description"), ("instruction", "step_type")):
        value = " ".join(str(step.get(key) or "") for key in keys).lower()
        for role in ("deployment", "review", "planning", "implementation"):
            if any(re.search(rf"\b{re.escape(term)}\b", value) for term in _ROLE_TERMS[role]):
                return role
    return "execution"


def compile_requested_execution_policy(value: str) -> dict[str, Any]:
    """Parse only explicit routing/approval language; never guess hidden intent."""
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    lowered = text.lower()
    roles: dict[str, dict[str, Any]] = {}

    for role, terms in _ROLE_TERMS.items():
        role_pattern = "|".join(re.escape(term) for term in terms)
        for label, backend in _BACKEND_ALIASES.items():
            label_pattern = re.escape(label)
            if re.search(
                rf"\b(?:use|choose|run|route|with)\s+{label_pattern}\b[^,.;]{{0,35}}\b(?:{role_pattern})\b"
                rf"|\b(?:{role_pattern})\b[^,.;]{{0,35}}\b(?:use|using|with|via|on)\s+{label_pattern}\b",
                lowered,
            ):
                roles.setdefault(role, {})["backend"] = backend
                roles[role]["requested_vendor"] = label
                break

    planning_window = re.search(
        r"(?:prefer|use|choose|route).{0,45}(?:local|free).{0,45}(?:plan|planning|scope|scoping|brief)"
        r"|(?:plan|planning|scope|scoping|brief).{0,45}(?:prefer|use|choose|route).{0,45}(?:local|free)",
        lowered,
    )
    if planning_window:
        planning = roles.setdefault("planning", {})
        planning["prefer_local"] = "local" in planning_window.group(0)
        planning["free_only"] = "free" in planning_window.group(0)
        planning["force_reselect"] = True

    if re.search(r"\b(?:different|independent|separate)\s+(?:model|vendor|provider|agent)\b.{0,35}\b(?:review|validation|verify|qa|audit)\b", lowered) or re.search(
        r"\b(?:review|validation|verify|qa|audit)\b.{0,35}\b(?:different|independent|separate)\s+(?:model|vendor|provider|agent)\b",
        lowered,
    ):
        roles.setdefault("review", {})["independent_from"] = "implementation"

    approval_before_roles: list[str] = []
    if re.search(
        r"\b(?:ask|check|confirm|get approval|require approval)\b.{0,40}\b(?:before|prior to)\b.{0,20}\b(?:deploy|deployment|publish|release|ship)\b",
        lowered,
    ):
        approval_before_roles.append("deployment")

    policy: dict[str, Any] = {}
    if roles:
        policy["roles"] = roles
    if approval_before_roles:
        policy["approval_before_roles"] = approval_before_roles
    if policy:
        policy.update({"version": 1, "source": "explicit_request"})
    return policy


def apply_requested_step_policy(
    config: dict[str, Any],
    *,
    step: dict[str, Any],
    run_data: dict[str, Any] | None,
) -> tuple[dict[str, Any], str, dict[str, Any]]:
    """Merge run-request constraints below explicit workflow-step overrides."""
    merged = dict(config or {})
    policy = (run_data or {}).get("requested_execution_policy")
    if not isinstance(policy, dict):
        return merged, infer_step_role(step), {}
    role = infer_step_role(step)
    roles = policy.get("roles") if isinstance(policy.get("roles"), dict) else {}
    requested = roles.get(role) if isinstance(roles.get(role), dict) else {}
    if not requested:
        return merged, role, {}

    # Explicit run instructions may swap a workflow's ordinary default route.
    # Safety-critical steps can opt out with ``route_locked``.
    if requested.get("backend") and not bool(merged.get("route_locked")):
        merged["backend_id"] = str(requested["backend"])
    model_policy = dict(merged.get("model_policy") or {})
    for key in ("free_only", "prefer_local", "force_reselect"):
        if key in requested:
            model_policy[key] = bool(requested[key])
    if model_policy:
        merged["model_policy"] = model_policy
    return merged, role, dict(requested)
