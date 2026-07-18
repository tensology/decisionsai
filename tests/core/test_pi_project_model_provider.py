from unittest.mock import MagicMock, patch

from distr.core.pi_preflight import (
    infer_project_model_provider,
    resolve_coding_cli_config,
)


def test_free_openrouter_project_model_repairs_global_ollama_provider():
    assert infer_project_model_provider(
        "ollama", "qwen/qwen3-coder:free"
    ) == "openrouter"


def test_openrouter_free_router_repairs_global_ollama_provider():
    assert infer_project_model_provider("ollama", "openrouter/free") == "openrouter"


def test_local_model_keeps_ollama_provider():
    assert infer_project_model_provider("ollama", "ornith:35b") == "ollama"


def test_explicit_non_ollama_provider_is_not_rewritten():
    assert infer_project_model_provider(
        "kilocode", "openrouter/free"
    ) == "kilocode"


def test_global_free_model_repairs_stale_ollama_provider():
    settings_row = ("ollama", "qwen/qwen3-coder:free")
    session = MagicMock()
    session.execute.return_value.first.return_value = settings_row
    session.query.return_value.filter.return_value.first.return_value = None
    manager = MagicMock()
    manager.__enter__.return_value = session
    manager.__exit__.return_value = False

    with patch("distr.core.db.get_session", return_value=manager):
        provider, model, _cwd = resolve_coding_cli_config(project_id=16)

    assert provider == "openrouter"
    assert model == "qwen/qwen3-coder:free"
