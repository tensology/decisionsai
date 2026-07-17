"""Existing-first workflow selection and generated workflow quality gates."""

from __future__ import annotations

import json
import re
from typing import Any


_SOFTWARE_TERMS = {
    "api", "app", "backend", "bug", "build", "code", "database", "develop",
    "feature", "frontend", "implementation", "mobile", "product", "refactor", "repository",
    "service", "ship", "software", "test", "ticket", "ui", "website",
}
_UI_TERMS = {
    "accessibility", "browser", "css", "design", "frontend", "html", "layout",
    "button", "color", "colour", "component", "playwright", "responsive", "ui", "ux",
    "visual", "website",
}
_BACKEND_TERMS = {
    "api", "backend", "database", "endpoint", "migration", "queue", "schema",
    "server", "service", "sql", "worker",
}
_LARGE_SCOPE_TERMS = {
    "all tickets", "backlog", "collection of tickets", "end to end", "large request",
    "multiple tickets", "one-shot", "one shot", "product", "project", "ticket group",
}
_REQUIRED_SOFTWARE_ROLES = {"planning", "implementation", "review", "reporting"}
_REQUIRED_CONFIG_FIELDS = {
    "skills", "tools", "guardrail", "failure_checklist", "required_context",
    "expected_outputs", "model_policy",
}


def _words(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9+#.-]+", (text or "").lower()))


def classify_work_request(text: str) -> dict[str, Any]:
    """Describe only the capability signals needed for workflow selection."""
    lowered = (text or "").lower()
    words = _words(lowered)
    ui = bool(words & _UI_TERMS)
    backend = bool(words & _BACKEND_TERMS)
    software = ui or backend or bool(words & _SOFTWARE_TERMS)
    large_scope = any(term in lowered for term in _LARGE_SCOPE_TERMS)
    return {
        "software": software,
        "ui": ui,
        "backend": backend,
        "large_scope": large_scope,
        "request_text": (text or "").strip(),
    }


def _config_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(value or "{}")
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def audit_workflow_contract(
    workflow: dict[str, Any],
    *,
    request_profile: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Score whether a workflow is executable, observable, and context-complete."""
    profile = request_profile or classify_work_request("")
    steps = sorted(workflow.get("steps") or [], key=lambda step: int(step.get("position") or 0))
    missing: list[str] = []
    roles: set[str] = set()
    tools: set[str] = set()
    skills: set[str] = set()
    validation_count = 0

    minimum_steps = 5 if profile.get("software") else 2
    if len(steps) < minimum_steps:
        missing.append(f"at least {minimum_steps} purposeful steps")

    for index, step in enumerate(steps):
        label = str(step.get("name") or f"step {index + 1}")
        instruction = str(step.get("instruction") or "").strip()
        config = _config_dict(step.get("config"))
        role = str(config.get("step_role") or "").strip().lower()
        if role:
            roles.add(role)
        tools.update(str(item).strip() for item in config.get("tools") or [] if str(item).strip())
        skills.update(str(item).strip() for item in config.get("skills") or [] if str(item).strip())
        if len(instruction) < 60:
            missing.append(f"{label}: scoped instruction")
        for field in sorted(_REQUIRED_CONFIG_FIELDS):
            value = config.get(field)
            if value in (None, "", [], {}):
                missing.append(f"{label}: {field}")
        validation_type = str(step.get("validation_type") or "none").strip().lower()
        validation_prompt = str(step.get("validation_prompt") or "").strip()
        if validation_type == "none" or len(validation_prompt) < 20:
            missing.append(f"{label}: meaningful validation")
        else:
            validation_count += 1

    if profile.get("software"):
        missing_roles = sorted(_REQUIRED_SOFTWARE_ROLES - roles)
        if missing_roles:
            missing.append(f"workflow roles: {', '.join(missing_roles)}")
        if not ({"cli", "shell"} & tools):
            missing.append("software execution tools")
        if profile.get("ui") and not ({"playwright", "browser_use", "computer_use"} & tools):
            missing.append("UI/browser validation tools")

    run_settings = _config_dict(workflow.get("run_settings"))
    for flag in ("memory_enabled", "load_project_memory", "capture_memory_deltas"):
        if run_settings.get(flag) is not True:
            missing.append(f"run setting: {flag}")

    total_checks = max(1, len(steps) * (len(_REQUIRED_CONFIG_FIELDS) + 2) + 4)
    quality = max(0, round(100 * (1 - min(len(missing), total_checks) / total_checks)))
    return {
        "viable": not missing,
        "quality_score": quality,
        "missing": missing,
        "step_count": len(steps),
        "roles": sorted(roles),
        "tools": sorted(tools),
        "skills": sorted(skills),
        "validation_count": validation_count,
    }


def _scope_score(workflow: dict[str, Any], profile: dict[str, Any], audit: dict[str, Any]) -> int:
    text = " ".join(
        [str(workflow.get("name") or ""), str(workflow.get("description") or "")]
        + [str(step.get("name") or "") for step in workflow.get("steps") or []]
    ).lower()
    scope_words = _words(text)
    score = int(audit.get("quality_score") or 0)
    if profile.get("software") and str(workflow.get("name") or "").strip().lower() == "development":
        score += 25
    if profile.get("ui"):
        score += 35 if scope_words & {"ui", "frontend", "visual", "web", "website"} else 0
        score += 15 if {"playwright", "browser_use"} & set(audit.get("tools") or []) else 0
    if profile.get("backend"):
        score += 35 if scope_words & {"backend", "api", "database", "server"} else 0
        score += 10 if {"cli", "shell"} & set(audit.get("tools") or []) else 0
    return score


def _scope_relevant(workflow: dict[str, Any], profile: dict[str, Any]) -> bool:
    name = str(workflow.get("name") or "").strip().lower()
    description = str(workflow.get("description") or "").lower()
    scope_text = f"{name} {description}"
    scope_words = _words(scope_text)
    if profile.get("software"):
        if name == "development":
            return True
        if profile.get("ui") and scope_words & _UI_TERMS:
            return True
        if profile.get("backend") and scope_words & _BACKEND_TERMS:
            return True
        return bool(scope_words & _SOFTWARE_TERMS)
    ignored = {"a", "an", "and", "create", "for", "me", "the", "to", "workflow"}
    request_terms = _words(str(profile.get("request_text") or "")) - ignored
    return bool(request_terms & _words(scope_text))


def select_workflow_for_request(
    request_text: str,
    *,
    candidates: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Choose the strongest viable existing workflow; creation is the fallback."""
    from distr.core.workflow.service import get_workflow, list_workflows

    profile = classify_work_request(request_text)
    if candidates is None:
        candidates = [
            workflow
            for row in list_workflows(limit=200)
            if (workflow := get_workflow(int(row["id"])))
        ]

    ranked: list[dict[str, Any]] = []
    for workflow in candidates:
        audit = audit_workflow_contract(workflow, request_profile=profile)
        relevant = _scope_relevant(workflow, profile)
        ranked.append({
            "workflow_id": workflow.get("id"),
            "workflow_name": workflow.get("name"),
            "score": _scope_score(workflow, profile, audit),
            "viable": bool(audit["viable"] and relevant),
            "scope_relevant": relevant,
            "audit": audit,
        })
    ranked.sort(key=lambda row: (bool(row["viable"]), int(row["score"])), reverse=True)
    selected = next((row for row in ranked if row["viable"]), None)
    if selected:
        reason = (
            f"Reused {selected['workflow_name']} because its full execution contract covers "
            "the requested scope, context, tools, validation, routing, and memory."
        )
    else:
        reason = "No existing workflow passed the complete execution-contract audit."
    return {
        "selected": selected,
        "create_required": selected is None,
        "reason": reason,
        "request_profile": profile,
        "candidates": ranked,
    }


def validate_generated_workflow_payload(
    workflow: dict[str, Any],
    *,
    request_text: str,
) -> dict[str, Any]:
    """Reject skeletal model-generated workflows before they reach the database."""
    profile = classify_work_request(request_text)
    audit = audit_workflow_contract(workflow, request_profile=profile)
    missing = list(audit["missing"])
    if not str(workflow.get("context_rules") or "").strip():
        missing.append("workflow context_rules")
    steps = workflow.get("steps") or []
    if steps:
        for index, step in enumerate(steps):
            if "on_pass_goto_position" not in step:
                missing.append(f"step {index + 1}: explicit pass routing")
            if "on_fail_goto_position" not in step:
                missing.append(f"step {index + 1}: explicit failure routing")
    return {
        **audit,
        "viable": not missing,
        "missing": missing,
    }
