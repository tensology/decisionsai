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

    low_markers = (
        "typo",
        "copy",
        "label",
        "small css",
        "button text",
        "rename",
        "simple",
        "readme",
        "documentation",
        "docs",
    )
    if score == 0 and len(words) <= 80 and any(term in text for term in low_markers):
        return "low"
    if score >= 3:
        return "high"
    return "medium"


def resolve_ticket_complexity(
    title: str = "",
    description: str = "",
    *,
    requested: str | None = None,
    file_count: int = 0,
    todo_count: int = 0,
) -> str:
    """Resolve user-requested complexity into the stored routing level.

    `auto` means the orchestrator/system should assess the ticket. Explicit
    low/medium/high values are treated as manual overrides.
    """
    raw = (requested or "").strip().lower()
    if raw in VALID_COMPLEXITIES:
        return raw
    return infer_ticket_complexity(
        title,
        description,
        file_count=file_count,
        todo_count=todo_count,
    )


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
    model_provider = str(settings.get(f"project_cli_{level}_model_provider") or "").strip().lower()
    from distr.core.project_cli_backends.ide_handoff import is_ide_backend

    if is_ide_backend(backend):
        model = ""
    route: dict[str, str] = {"backend": backend, "model": model}
    if model_provider:
        route["model_provider"] = model_provider
    fallback_backend = normalize_backend_id(settings.get(f"project_cli_{level}_fallback_backend") or "")
    fallback_model = (settings.get(f"project_cli_{level}_fallback_model") or "").strip()
    if fallback_backend and not is_ide_backend(fallback_backend):
        route["fallback_backend"] = fallback_backend
        route["fallback_model"] = fallback_model or "auto"
    if backend == "codex":
        route["codex_reasoning_effort"] = normalize_codex_intelligence(
            settings.get(f"project_cli_{level}_codex_intelligence")
        )
        route["codex_service_tier"] = normalize_codex_speed(
            settings.get(f"project_cli_{level}_codex_speed")
        )
    return route


def _apply_configured_fallback(route: dict[str, str], level: str) -> dict[str, str]:
    """Use the user-configured CLI fallback when the primary backend is unavailable."""
    from distr.core.project_cli_backends import get_backend, normalize_backend_id

    fallback_backend = normalize_backend_id(route.get("fallback_backend") or "")
    if not fallback_backend:
        fallback = DEFAULT_COMPLEXITY_ROUTES[level]
        fallback_backend = normalize_backend_id(fallback.get("backend") or "pi")
        fallback_model = str(fallback.get("model") or "auto").strip()
    else:
        fallback_model = str(route.get("fallback_model") or "auto").strip()
    try:
        if get_backend(fallback_backend).setup_status().ready:
            out = {
                "complexity": level,
                "backend": fallback_backend,
                "model": fallback_model,
            }
            if fallback_backend == "codex":
                out["codex_reasoning_effort"] = route.get("codex_reasoning_effort", "")
                out["codex_service_tier"] = route.get("codex_service_tier", "")
            return out
    except Exception:
        pass
    return {
        "complexity": level,
        "backend": "pi",
        "model": "",
    }


def resolve_ticket_cli_route(
    project: Any,
    complexity: str | None,
    board: Any | None = None,
) -> dict[str, str]:
    """Return the intended backend/model for a ticket complexity.

    Ticket complexity is global routing policy by default. Boards may override
    per-complexity backend/model via ``orchestrator_policy.complexity_routing``.
    """
    from distr.core.project_cli_backends import get_backend

    level = normalize_ticket_complexity(complexity)
    route = _global_complexity_route(level)

    if board is not None:
        try:
            from distr.core.orchestrator import parse_board_orchestrator_policy

            policy = parse_board_orchestrator_policy(getattr(board, "orchestrator_policy", None))
            board_routes = policy.get("complexity_routing") or {}
            if isinstance(board_routes, dict):
                override = board_routes.get(level) or {}
                if isinstance(override, dict):
                    from distr.core.project_cli_backends import normalize_backend_id

                    if override.get("backend"):
                        route["backend"] = normalize_backend_id(override["backend"])
                    if override.get("model"):
                        route["model"] = str(override["model"]).strip()
                    if override.get("model_provider") or override.get("provider"):
                        route["model_provider"] = str(
                            override.get("model_provider") or override.get("provider") or ""
                        ).strip().lower()
                    if route.get("backend") == "codex":
                        if override.get("codex_reasoning_effort"):
                            route["codex_reasoning_effort"] = str(override["codex_reasoning_effort"]).strip()
                        if override.get("codex_service_tier"):
                            route["codex_service_tier"] = str(override["codex_service_tier"]).strip()
        except Exception:
            pass

    backend = route["backend"]
    model = route["model"]
    try:
        if not get_backend(backend).setup_status().ready:
            return _apply_configured_fallback({**route, "complexity": level}, level)
    except Exception:
        return _apply_configured_fallback({**route, "complexity": level}, level)
    out: dict[str, str] = {"complexity": level, "backend": backend, "model": model}
    if route.get("model_provider"):
        out["model_provider"] = route["model_provider"]
    if backend == "codex":
        out["codex_reasoning_effort"] = route.get("codex_reasoning_effort", "")
        out["codex_service_tier"] = route.get("codex_service_tier", "")
    return out
