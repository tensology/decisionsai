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
    "final_polish": (
        "final polish", "production polish", "ship audit", "release polish",
    ),
    "reporting": (
        "report", "reporting", "result packet", "compact memory", "memory update",
    ),
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
        # Final polish often also contains words such as "audit" or "release",
        # so recognize the more specific role before review/deployment.
        for role in (
            "final_polish", "reporting", "deployment", "review", "planning", "implementation",
        ):
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
    if re.search(
        r"\b(?:read[ -]?only|without (?:editing|changing|modifying)|"
        r"do not (?:edit|change|modify)|don't (?:edit|change|modify)|no file changes)\b",
        lowered,
    ):
        policy["read_only"] = True
        # A concrete, already-scoped verification command does not need two
        # planning agents and a prose-reporting agent before/after the check.
        # Keep broad audits/research on the full workflow; narrow named tests or
        # commands can enter at independent review and use its result packet as
        # the terminal report.
        if re.search(
            r"(?:\btests?/[\w./-]+\.py\b|\bpytest\b|\bnpm\s+(?:run\s+)?test\b|"
            r"\b(?:run|execute)\s+the\s+(?:existing\s+)?(?:named\s+)?test\s+suite\b)",
            lowered,
        ):
            policy["verification_only"] = True
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


def apply_approved_provider_replacements_to_route(
    route: dict[str, Any] | None,
    replacements: list[dict[str, Any]] | None,
) -> dict[str, Any]:
    """Apply durable, user-approved provider swaps to one execution route.

    Ticket groups carry the approval in their common metadata so each queued
    ticket can reuse the decision.  This helper deliberately runs before
    provider preflight: checking the retired route first would ask the same
    question again even though the user already approved its replacement.
    """
    candidate = dict(route or {})
    if not candidate:
        return candidate

    from distr.core.project_cli_backends import normalize_backend_id

    candidate_backend = normalize_backend_id(
        str(candidate.get("backend") or "").strip()
    )
    candidate_model = str(candidate.get("model") or "auto").strip()
    for replacement in replacements or []:
        if not isinstance(replacement, dict):
            continue
        from_backend = normalize_backend_id(
            str(replacement.get("from_backend") or "").strip()
        )
        from_model = str(replacement.get("from_model") or "auto").strip()
        if candidate_backend != from_backend or candidate_model != from_model:
            continue
        candidate.update(
            {
                "backend": normalize_backend_id(
                    str(replacement.get("to_backend") or "").strip()
                ),
                "model": str(replacement.get("to_model") or "auto").strip(),
                "source": "approved_provider_replacement",
                "requires_approval": False,
            }
        )
        candidate.pop("model_provider", None)
        break
    return candidate
