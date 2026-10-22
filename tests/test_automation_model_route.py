"""automodel 2026-10-22: automation CLI route resolution (pure, no DB)."""
from pathlib import Path

from distr.core.automation.model_route import resolve_automation_route

GLOBAL = {"automation_cli_backend": "claude", "automation_cli_model_provider": "anthropic", "automation_cli_model": "claude-opus-4"}


def test_override_beats_global():
    cfg = {"cli_route_mode": "custom", "cli_backend": "codex", "cli_model_provider": "openai", "cli_model": "gpt-5.3-codex"}
    route, source = resolve_automation_route(cfg, GLOBAL)
    assert source == "automation_override"
    assert route == {"backend": "codex", "model_provider": "openai", "model": "gpt-5.3-codex"}


def test_global_when_inherit():
    route, source = resolve_automation_route({"cli_route_mode": "inherit"}, GLOBAL)
    assert source == "automation_global_default"
    assert route["backend"] == "claude" and route["model"] == "claude-opus-4"


def test_global_when_no_config():
    assert resolve_automation_route({}, GLOBAL)[1] == "automation_global_default"
    assert resolve_automation_route(None, GLOBAL)[1] == "automation_global_default"


def test_fallback_when_nothing_set():
    route, source = resolve_automation_route({}, {"automation_cli_backend": ""})
    assert source == "complexity_routing_fallback" and route == {}


def test_legacy_imported_route_is_an_override_unless_inherit():
    legacy = {"backend": "pi", "model_provider": "ollama", "model": "qwen3"}
    assert resolve_automation_route(legacy, GLOBAL)[1] == "automation_import_configuration"
    route, source = resolve_automation_route({**legacy, "cli_route_mode": "inherit"}, GLOBAL)
    assert source == "automation_global_default" and route["backend"] == "claude"
    route, source = resolve_automation_route({**legacy, "cli_route_mode": "inherit"}, {})
    assert source == "complexity_routing_fallback" and route == {}


def test_custom_without_backend_falls_through():
    assert resolve_automation_route({"cli_route_mode": "custom", "cli_backend": ""}, GLOBAL)[1] == "automation_global_default"


def test_auto_model_kept_explicit_and_fallbacks_preserved():
    cfg = {"cli_route_mode": "custom", "cli_backend": "Cursor", "cli_model": "", "fallback_backend": "Pi", "fallback_model": "qwen3"}
    route, _ = resolve_automation_route(cfg, {})
    assert route == {"backend": "cursor", "model": "auto", "fallback_backend": "pi", "fallback_model": "qwen3"}


def test_dispatch_uses_resolver():
    src = (Path(__file__).resolve().parents[1] / "distr/core/automation_orchestrator.py").read_text()
    assert "resolve_automation_route(config, _settings)" in src
    assert 'route_source' in src and '"source": "automation_import_configuration"' in src
