"""2026-10-22: a fixed thread pin must beat Auto; Auto tiers come from project_cli_{level}_* settings."""
from types import SimpleNamespace

from distr.core.kanban.ticket_policy import resolve_ticket_cli_route
from distr.core.workflow import development_harness


def _project():
    return SimpleNamespace(id=0, coding_backend="pi", coding_backend_model="auto")


def test_manual_pin_wins_over_assessed_auto_route():
    metadata = {"model_route": {"route_mode": "manual", "backend": "pi", "provider": "ollama", "model_name": "deepseek-v4.1-flash:cloud"}}
    assessment = {"complexity": "high", "route": {"backend": "codex", "model": "gpt-5.3-codex"}}
    backend, model, options, complexity = development_harness._route(_project(), metadata, assessment)
    assert backend == "pi"
    assert model == "deepseek-v4.1-flash:cloud"
    assert options.get("model_provider") == "ollama"
    assert complexity == "high"


def test_auto_uses_configured_complexity_tier():
    settings = {
        "project_cli_medium_backend": "pi",
        "project_cli_medium_model": "deepseek-v4.1-flash:cloud",
        "project_cli_medium_model_provider": "ollama",
    }
    route = resolve_ticket_cli_route(None, "medium", settings=settings)
    assert route["backend"] == "pi"
    assert route["model"] == "deepseek-v4.1-flash:cloud"
    assert route.get("model_provider") == "ollama"


def test_auto_kilocode_tier_is_pi_with_kilocode_provider():
    settings = {"project_cli_low_backend": "kilocode", "project_cli_low_model": "some/model"}
    route = resolve_ticket_cli_route(None, "low", settings=settings)
    assert route["backend"] == "pi"
    assert route.get("model_provider") == "kilocode"
