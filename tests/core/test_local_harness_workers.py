"""Local Ollama harness workers are built from live installed tags (no hard-coded picker)."""
from __future__ import annotations

from types import SimpleNamespace

import distr.core.workflow.development_harness as harness


def test_parse_local_ollama_worker_prefix_and_legacy_aliases():
    assert harness.parse_local_ollama_worker("local_ollama:qwen3.8:27b") == "qwen3.8:27b"
    assert harness.parse_local_ollama_worker("local_ollama:muse-glimmer:30b-mlx") == "muse-glimmer:30b-mlx"
    assert harness.parse_local_ollama_worker("qwen38_27b") == "qwen3.8:27b"
    assert harness.parse_local_ollama_worker("glimmer") == "muse-glimmer:30b-mlx"
    assert harness.parse_local_ollama_worker("cursor") is None
    assert harness.parse_local_ollama_worker("pi") is None


def test_available_local_harness_workers_uses_live_installed_ids(monkeypatch):
    monkeypatch.setattr(
        harness,
        "_ollama_reachable_installed_ids",
        lambda: {
            "qwen3.8:27b",
            "muse-glimmer:30b-mlx",
            "ornith:9b",
            "nomic-embed-text:latest",
            "qwen3-next:80b-cloud",
        },
    )
    workers = harness.available_local_harness_workers()
    models = {row["model"] for row in workers}
    ids = {row["id"] for row in workers}
    assert models == {"qwen3.8:27b", "muse-glimmer:30b-mlx", "ornith:9b"}
    assert "nomic-embed-text:latest" not in models
    assert "qwen3-next:80b-cloud" not in models
    assert "local_ollama:qwen3.8:27b" in ids
    assert "local_ollama:muse-glimmer:30b-mlx" in ids
    # Labels demoted from old presets, not required for presence.
    labels = {row["model"]: row["label"] for row in workers}
    assert labels["qwen3.8:27b"] == "Qwen 3.8 27B"
    assert labels["muse-glimmer:30b-mlx"] == "Glimmer"
    # Static preset ids must not be the picker keys.
    assert "qwen38_27b" not in ids
    assert "glimmer" not in ids


def test_available_local_harness_workers_fail_closed_when_ollama_unreachable(monkeypatch):
    monkeypatch.setattr(harness, "_ollama_reachable_installed_ids", lambda: None)
    assert harness.available_local_harness_workers() == []


def test_manual_route_resolves_dynamic_local_worker(monkeypatch):
    monkeypatch.setattr(harness, "local_harness_model_is_available", lambda model, installed=None: model == "qwen3.8:27b")
    project = SimpleNamespace(id=2, coding_backend="codex", coding_backend_model="gpt-5")
    backend, model, options, complexity = harness._route(
        project,
        {
            "model_route": {
                "route_mode": "manual",
                "backend": "local_ollama:qwen3.8:27b",
                "provider": "ollama",
                "model_name": "qwen3.8:27b",
            }
        },
        {"complexity": "low", "route": {"backend": "codex", "model": "gpt-5"}},
    )
    assert (backend, model, complexity) == ("pi", "qwen3.8:27b", "low")
    assert options["model_provider"] == "ollama"
    assert options["local_harness_preset"] == "local_ollama:qwen3.8:27b"
    assert options["local_harness_available"] is True
    assert options["keep_alive"] == "5m"
    assert options["unload_when_done"] is True


def test_manual_route_resolves_legacy_glimmer_alias(monkeypatch):
    monkeypatch.setattr(
        harness,
        "local_harness_model_is_available",
        lambda model, installed=None: model == "muse-glimmer:30b-mlx",
    )
    project = SimpleNamespace(id=2, coding_backend="pi", coding_backend_model="")
    backend, model, options, _ = harness._route(
        project,
        {
            "model_route": {
                "route_mode": "manual",
                "backend": "glimmer",
                "provider": "",
                "model_name": "",
            }
        },
        {"complexity": "medium", "route": {}},
    )
    assert (backend, model) == ("pi", "muse-glimmer:30b-mlx")
    assert options["local_harness_available"] is True


def test_composer_no_longer_hardcodes_qwen_glimmer_as_picker_source():
    from pathlib import Path

    text = Path("distr/gui/web/static/development/threads/composer/index.js").read_text()
    assert "HARNESS_OPTIONS" in text
    assert "local_ollama:" in text
    assert "localOllamaWorkers" in text
    assert "ensureLocalHarnessPresets" in text
    # Demoted hints may remain, but picker must not hard-code those as HARNESS_OPTIONS entries.
    assert "{ id: 'qwen38_27b'" not in text
    assert "{ id: 'glimmer'" not in text


def test_update_model_route_preserves_local_ollama_worker_id():
    from distr.core.workflow import development_threads as threads

    class FakeChat:
        def __init__(self):
            self.id = 7
            self.parent_id = None
            self.provider = ""
            self.model_name = ""
            self.route_mode = "auto"
            self.params = "{}"

    class FakeDB:
        def __init__(self, chat):
            self._chat = chat
        def get(self, *_a, **_k):
            return self._chat
        def commit(self):
            pass
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False

    chat = FakeChat()
    result = threads.update_development_model_route(
        7,
        route_mode="manual",
        backend="glimmer",  # legacy alias
        provider="",
        model_name="",
        _session_provider=lambda: FakeDB(chat),
    )
    assert result["backend"] == "local_ollama:muse-glimmer:30b-mlx"
    assert result["provider"] == "ollama"
    assert result["model_name"] == "muse-glimmer:30b-mlx"

    result2 = threads.update_development_model_route(
        7,
        route_mode="manual",
        backend="local_ollama:qwen3.8:27b",
        provider="ollama",
        model_name="qwen3.8:27b",
        _session_provider=lambda: FakeDB(chat),
    )
    assert result2["backend"] == "local_ollama:qwen3.8:27b"
    assert result2["model_name"] == "qwen3.8:27b"
