from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient


def test_model_catalog_cache_reuses_cached_provider_result(monkeypatch, tmp_path: Path):
    from distr.core.services import model_catalog_cache

    monkeypatch.setattr(model_catalog_cache, "MODEL_CATALOG_CACHE_DIR", tmp_path)
    calls = []

    def _fetch():
        calls.append("fetch")
        return [{"id": "gpt-4o", "name": "GPT-4o"}]

    first = model_catalog_cache.get_or_fetch_model_catalog(
        "openai",
        fetcher=_fetch,
        auth_fingerprint="key-a",
    )
    second = model_catalog_cache.get_or_fetch_model_catalog(
        "openai",
        fetcher=_fetch,
        auth_fingerprint="key-a",
    )

    assert first == second
    assert calls == ["fetch"]


def test_model_catalog_cache_flush_forces_refetch(monkeypatch, tmp_path: Path):
    from distr.core.services import model_catalog_cache

    monkeypatch.setattr(model_catalog_cache, "MODEL_CATALOG_CACHE_DIR", tmp_path)
    calls = []

    def _fetch():
        calls.append("fetch")
        return [{"id": f"model-{len(calls)}", "name": f"Model {len(calls)}"}]

    first = model_catalog_cache.get_or_fetch_model_catalog(
        "openai",
        fetcher=_fetch,
        auth_fingerprint="key-a",
    )
    model_catalog_cache.flush_model_catalog_cache("openai")
    second = model_catalog_cache.get_or_fetch_model_catalog(
        "openai",
        fetcher=_fetch,
        auth_fingerprint="key-a",
    )

    assert first != second
    assert calls == ["fetch", "fetch"]


def test_llms_models_reload_endpoint_flushes_only_model_catalog_cache(monkeypatch, tmp_path: Path):
    from distr.gui.web.routes.settings import create_routes
    from distr.core.services import model_catalog_cache

    monkeypatch.setattr(model_catalog_cache, "MODEL_CATALOG_CACHE_DIR", tmp_path)
    monkeypatch.setattr(
        "distr.core.settings.load_settings_from_db",
        lambda: {
            "openai_enabled": True,
            "openai_key": "sk-test",
            "conversational_llm_provider": "openai",
            "conversational_llm_model": "gpt-4o",
        },
    )

    calls = []

    def _fetch_models(api_key: str):
        calls.append(api_key)
        return [{"id": f"gpt-4o-{len(calls)}", "name": f"GPT-4o {len(calls)}"}]

    monkeypatch.setattr(
        "distr.gui.utils.get_ollama_models.get_openai_models",
        _fetch_models,
    )

    app = FastAPI()
    templates_dir = Path(__file__).resolve().parents[2] / "distr/gui/web/templates"
    app.include_router(create_routes(templates_dir), prefix="/api")
    client = TestClient(app)

    first = client.get("/api/llms/models", params={"type": "conversational", "provider": "openai"})
    second = client.get("/api/llms/models", params={"type": "conversational", "provider": "openai"})
    reload_response = client.post("/api/llms/models/reload", json={"provider": "openai"})
    third = client.get("/api/llms/models", params={"type": "conversational", "provider": "openai"})
    settings_response = client.get("/api/llms")

    assert first.status_code == 200
    assert second.status_code == 200
    assert reload_response.status_code == 200
    assert third.status_code == 200
    assert calls == ["sk-test", "sk-test"]
    assert first.json()["models"] == second.json()["models"]
    assert first.json()["models"] != third.json()["models"]
    assert settings_response.status_code == 200
    assert settings_response.json()["conversational_model"] == "gpt-4o"


def test_image_models_endpoint_filters_openai_to_real_image_models(monkeypatch, tmp_path: Path):
    from distr.gui.web.routes.settings import create_routes
    from distr.core.services import model_catalog_cache

    monkeypatch.setattr(model_catalog_cache, "MODEL_CATALOG_CACHE_DIR", tmp_path)
    monkeypatch.setattr(
        "distr.core.settings.load_settings_from_db",
        lambda: {
            "openai_enabled": True,
            "openai_key": "sk-test",
            "image_llm_provider": "openai",
            "image_llm_model": "gpt-image-1",
        },
    )

    def _fetch_models(api_key: str):
        assert api_key == "sk-test"
        return [
            {"id": "gpt-4o", "name": "GPT-4o"},
            {"id": "gpt-4.1", "name": "GPT-4.1"},
            {"id": "gpt-image-1", "name": "GPT Image 1"},
            {"id": "dall-e-3", "name": "DALL-E 3"},
        ]

    monkeypatch.setattr(
        "distr.gui.utils.get_ollama_models.get_openai_models",
        _fetch_models,
    )

    app = FastAPI()
    templates_dir = Path(__file__).resolve().parents[2] / "distr/gui/web/templates"
    app.include_router(create_routes(templates_dir), prefix="/api")
    client = TestClient(app)

    response = client.get("/api/llms/models", params={"type": "image", "provider": "openai"})

    assert response.status_code == 200
    models = response.json()["models"]
    assert [row["id"] for row in models] == ["gpt-image-1", "dall-e-3"]
    assert [row["name"] for row in models] == ["GPT Image 1", "DALL-E 3"]
