from distr.core.kanban.ticket_policy import (
    infer_ticket_complexity,
    normalize_source_provider,
    normalize_ticket_complexity,
    resolve_ticket_cli_route,
)


def test_ticket_complexity_defaults_to_medium_and_detects_high():
    assert normalize_ticket_complexity("") == "medium"
    assert infer_ticket_complexity("Rename button", "simple copy change") == "low"
    assert infer_ticket_complexity(
        "Migrate workflow orchestration",
        "Add database schema migration, regression tests, server deployment, and websocket integration.",
    ) == "high"


def test_source_provider_normalization():
    assert normalize_source_provider("email") == "gmail"
    assert normalize_source_provider("wa") == "whatsapp"
    assert normalize_source_provider("jira-issue") == "jira"


def test_complexity_route_uses_global_settings_over_project_backend(monkeypatch):
    class Backend:
        def __init__(self, ready=True):
            self._ready = ready

        def setup_status(self):
            return type("Status", (), {"ready": self._ready})()

    monkeypatch.setattr("distr.core.project_cli_backends.get_backend", lambda backend_id: Backend(True))
    monkeypatch.setattr(
        "distr.core.settings.load_settings_from_db",
        lambda: {"project_cli_high_backend": "codex", "project_cli_high_model": "gpt-5.3-codex"},
    )
    project = type("Project", (), {"coding_backend": "claude_code", "coding_backend_model": "opus"})()

    route = resolve_ticket_cli_route(project, "high")

    assert route == {
        "complexity": "high",
        "backend": "codex",
        "model": "gpt-5.3-codex",
        "codex_reasoning_effort": "",
        "codex_service_tier": "",
    }


def test_complexity_route_maps_legacy_default_to_policy_backend(monkeypatch):
    class Backend:
        def setup_status(self):
            return type("Status", (), {"ready": True})()

    monkeypatch.setattr("distr.core.project_cli_backends.get_backend", lambda backend_id: Backend())
    monkeypatch.setattr("distr.core.settings.load_settings_from_db", lambda: {})
    project = type("Project", (), {"coding_backend": "pi", "coding_backend_model": ""})()

    assert resolve_ticket_cli_route(project, "low")["backend"] == "cursor"
    assert resolve_ticket_cli_route(project, "medium")["backend"] == "codex"
