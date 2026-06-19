from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient


def test_cli_backends_endpoint_filters_out_ide_backends(monkeypatch):
    from distr.gui.web.routes.settings import create_routes

    monkeypatch.setattr(
        "distr.core.project_cli_backends.get_backend_statuses",
        lambda active_backend=None: {
            "backends": [
                {"id": "codex", "name": "Codex CLI", "ready": True, "state": "ready"},
                {"id": "codex_ide", "name": "Codex IDE", "ready": True, "state": "ready"},
            ]
        },
    )

    app = FastAPI()
    templates_dir = Path(__file__).resolve().parents[2] / "distr/gui/web/templates"
    app.include_router(create_routes(templates_dir), prefix="/api")
    client = TestClient(app)

    response = client.get("/api/projects/cli-backends")

    assert response.status_code == 200
    rows = response.json()["backends"]
    assert len(rows) == 1
    assert rows[0]["kind"] == "cli"
    assert rows[0]["supports_model_picker"] is True


def test_cli_models_endpoint_flags_ide_backends_and_recommended_model(monkeypatch):
    from distr.gui.web.routes.settings import create_routes

    monkeypatch.setattr(
        "distr.core.settings.load_settings_from_db",
        lambda: {
            "coding_llm_provider": "ollama",
            "coding_llm_model": "llama3.2",
        },
    )
    monkeypatch.setattr(
        "distr.core.project_cli_backends.ide_handoff.is_ide_backend",
        lambda backend_id: backend_id == "codex_ide",
    )

    app = FastAPI()
    templates_dir = Path(__file__).resolve().parents[2] / "distr/gui/web/templates"
    app.include_router(create_routes(templates_dir), prefix="/api")
    client = TestClient(app)

    response = client.get("/api/projects/cli-models", params={"backend_id": "codex_ide"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["backend_kind"] == "ide"
    assert payload["supports_model_picker"] is False
    assert payload["recommended_model"]["id"] == "auto"
