import pytest
from pathlib import Path
from fastapi import FastAPI
from fastapi.testclient import TestClient

from distr.gui.web.routes.settings.llms import _supports_llm_type


@pytest.mark.parametrize("model", [
    {"id": "local-planner", "supports_tools": False, "input_modalities": ["text"], "output_modalities": ["text"]},
    {"id": "vision-planner", "input_modalities": ["text", "image"], "output_modalities": ["text"]},
    "muse-glimmer:30b-mlx",
])
def test_planning_accepts_text_models_without_tool_calling(model):
    assert _supports_llm_type(model, "planning", "ollama")


@pytest.mark.parametrize("model", [
    "text-embedding-3-large", "gpt-image-1", "dall-e-3", "whisper-1",
    "gpt-realtime", "tts-1", "gpt-4o-transcribe", "omni-moderation-latest",
    {"id": "media", "output_modalities": ["image"]},
    {"id": "audio-input", "input_modalities": ["audio"], "output_modalities": ["text"]},
])
def test_planning_rejects_non_chat_endpoints(model):
    assert not _supports_llm_type(model, "planning", "openai")


def test_other_model_menus_keep_their_capability_requirements():
    model = {"id": "local-planner", "supports_tools": False, "output_modalities": ["text"]}
    assert not _supports_llm_type(model, "conversational", "ollama")
    assert not _supports_llm_type(model, "image", "ollama")


def test_codex_cli_is_offered_only_to_planning_without_an_api_key(monkeypatch):
    from distr.gui.web.routes.settings import create_routes
    monkeypatch.setattr('distr.core.settings.load_settings_from_db', lambda:{})
    monkeypatch.setattr('distr.core.project_cli_backends.codex_proposal.available_models', lambda:['cli-model'])
    app = FastAPI()
    app.include_router(create_routes(Path(__file__).resolve().parents[2] / 'distr/gui/web/templates'), prefix='/api')
    client = TestClient(app)
    regular = client.get('/api/llms/available-providers').json()['providers']
    planning = client.get('/api/llms/available-providers?type=planning').json()['providers']
    assert not any(p['id'] == 'codex' for p in regular)
    assert {'id':'codex','name':'OpenAI'} in planning
    assert client.get('/api/llms/models?type=planning&provider=codex').json()['models'] == ['cli-model']


@pytest.mark.parametrize("text_model", [False, True])
def test_planning_endpoint_never_falls_back_to_rejected_media_models(monkeypatch, tmp_path, text_model):
    from distr.gui.web.routes.settings import create_routes
    from distr.core.services import model_catalog_cache
    monkeypatch.setattr(model_catalog_cache, "MODEL_CATALOG_CACHE_DIR", tmp_path)
    monkeypatch.setattr("distr.core.settings.load_settings_from_db", lambda: {
        "openai_enabled": True, "openai_key": "synthetic-key"})
    catalog = [{"id": "gpt-image-1", "name": "Image"}]
    if text_model:
        catalog.append({"id": "synthetic-text-model", "name": "Text", "supports_tools": False})
    monkeypatch.setattr("distr.gui.utils.get_ollama_models.get_openai_models", lambda key: catalog)
    app = FastAPI()
    app.include_router(create_routes(Path(__file__).resolve().parents[2] / "distr/gui/web/templates"), prefix="/api")
    response = TestClient(app).get("/api/llms/models", params={"type": "planning", "provider": "openai"})
    assert response.status_code == 200
    assert [row["id"] for row in response.json()["models"]] == (["synthetic-text-model"] if text_model else [])
