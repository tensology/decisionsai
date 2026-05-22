"""Ticket metadata policy for source provenance, complexity, and CLI routing."""

from __future__ import annotations

import re
from typing import Any


VALID_COMPLEXITIES = {"low", "medium", "high"}


def normalize_ticket_complexity(value: str | None) -> str:
    raw = (value or "").strip().lower()
    if raw in VALID_COMPLEXITIES:
        return raw
    return "medium"


def infer_ticket_complexity(
    title: str = "",
    description: str = "",
    *,
    file_count: int = 0,
    todo_count: int = 0,
) -> str:
    """Conservative ticket complexity classifier.

    Medium is the default because most project work benefits from the reliable
    Codex path. Low is for clearly small edits; high is for broader, risky,
    multi-system work that should use the strongest available route.
    """
    text = f"{title}\n{description}".lower()
    words = re.findall(r"\w+", text)
    score = 0

    if len(words) > 180:
        score += 1
    if len(words) > 420:
        score += 1
    if file_count > 2 or todo_count > 4:
        score += 1
    if any(term in text for term in ("migration", "database", "schema", "auth", "security", "payment", "workflow")):
        score += 1
    if any(term in text for term in ("refactor", "architecture", "orchestrator", "subagent", "integration", "regression")):
        score += 1
    if any(term in text for term in ("production", "deploy", "server", "postgres", "websocket", "webrtc")):
        score += 1

    low_markers = ("typo", "copy", "label", "small css", "button text", "rename", "simple")
    if score == 0 and len(words) <= 80 and any(term in text for term in low_markers):
        return "low"
    if score >= 3:
        return "high"
    return "medium"


def normalize_source_provider(value: str | None) -> str:
    raw = (value or "").strip().lower().replace("-", "_").replace(" ", "_")
    aliases = {
        "": "",
        "wa": "whatsapp",
        "gmail": "gmail",
        "email": "gmail",
        "google_mail": "gmail",
        "jira_issue": "jira",
        "trello_card": "trello",
    }
    return aliases.get(raw, raw)


DEFAULT_COMPLEXITY_ROUTES: dict[str, dict[str, str]] = {
    "low": {"backend": "cursor", "model": "auto"},
    "medium": {"backend": "codex", "model": "auto"},
    "high": {"backend": "codex", "model": "gpt-5.3-codex"},
}


def _global_complexity_route(level: str) -> dict[str, str]:
    from distr.core.kanban.codex_prefs import normalize_codex_intelligence, normalize_codex_speed
    from distr.core.project_cli_backends import normalize_backend_id
    from distr.core.settings import load_settings_from_db

    defaults = DEFAULT_COMPLEXITY_ROUTES[level]
    try:
        settings = load_settings_from_db()
    except Exception:
        settings = {}
    backend = normalize_backend_id(settings.get(f"project_cli_{level}_backend") or defaults["backend"])
    model = (settings.get(f"project_cli_{level}_model") or defaults["model"]).strip()
    route: dict[str, str] = {"backend": backend, "model": model}
    if backend == "codex":
        route["codex_reasoning_effort"] = normalize_codex_intelligence(
            settings.get(f"project_cli_{level}_codex_intelligence")
        )
        route["codex_service_tier"] = normalize_codex_speed(
            settings.get(f"project_cli_{level}_codex_speed")
        )
    return route


def resolve_ticket_cli_route(project: Any, complexity: str | None) -> dict[str, str]:
    """Return the intended backend/model for a ticket complexity.

    Ticket complexity is global routing policy, not project metadata. Projects
    provide the folder/context; the selected backend/model comes from Settings.
    """
    from distr.core.project_cli_backends import get_backend

    level = normalize_ticket_complexity(complexity)
    route = _global_complexity_route(level)
    backend = route["backend"]
    model = route["model"]
    try:
        if not get_backend(backend).setup_status().ready:
            fallback = DEFAULT_COMPLEXITY_ROUTES[level]
            backend = fallback["backend"]
            model = fallback["model"]
            if not get_backend(backend).setup_status().ready:
                backend = "pi"
                model = ""
    except Exception:
        backend = "pi"
        model = ""
    out: dict[str, str] = {"complexity": level, "backend": backend, "model": model}
    if backend == "codex":
        out["codex_reasoning_effort"] = route.get("codex_reasoning_effort", "")
        out["codex_service_tier"] = route.get("codex_service_tier", "")
    return out
