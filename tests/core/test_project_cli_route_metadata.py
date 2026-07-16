from pathlib import Path
import types

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


def test_cli_backends_endpoint_uses_setup_status_without_model_probe(monkeypatch):
    from distr.gui.web.routes.settings import create_routes

    monkeypatch.setattr(
        "distr.core.project_cli_backends.get_backend_statuses",
        lambda active_backend=None: {
            "backends": [
                {"id": "codex", "name": "Codex CLI", "ready": True, "state": "ready", "installed": True, "message": "Codex CLI is installed and ready."},
            ]
        },
    )
    def _fail_model_probe(_backend_id, settings=None):
        raise AssertionError("backend dropdown should not probe model catalogs")

    monkeypatch.setattr("distr.core.project_cli_backends.catalog_probe.models_for_cli_backend", _fail_model_probe)

    app = FastAPI()
    templates_dir = Path(__file__).resolve().parents[2] / "distr/gui/web/templates"
    app.include_router(create_routes(templates_dir), prefix="/api")
    client = TestClient(app)

    response = client.get("/api/projects/cli-backends")

    assert response.status_code == 200
    row = response.json()["backends"][0]
    assert row["workflow_ready"] is True
    assert row["health_state"] == "ready"
    assert row["catalog_verified"] is False
    assert row["model_catalog_state"] == "not_loaded"
    assert row["models_source"] == "runtime-snapshot"


def test_project_cli_backends_endpoint_reads_runtime_state_without_model_probe(monkeypatch):
    from distr.gui.web.routes.settings import create_routes
    from distr.core.project_cli_backends.live_sessions import (
        clear_live_session_buffer,
        set_live_session_connected,
        set_live_session_running,
    )

    class _FakeProject:
        id = 7
        coding_backend = "codex"

    class _FakeQuery:
        def filter(self, *_args, **_kwargs):
            return self

        def first(self):
            return _FakeProject()

    class _FakeDbSession:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def query(self, _model):
            return _FakeQuery()

    monkeypatch.setattr("distr.core.db.get_session", lambda: _FakeDbSession())
    monkeypatch.setattr(
        "distr.core.project_cli_backends.get_backend_statuses",
        lambda active_backend=None: {
            "active_backend": active_backend or "codex",
            "backends": [
                {
                    "id": "codex",
                    "name": "Codex CLI",
                    "installed": True,
                    "ready": True,
                    "state": "ready",
                    "message": "Codex CLI is installed and ready.",
                    "supports_rpc": True,
                },
            ],
        },
    )

    def _fail_model_probe(_backend_id, settings=None):
        raise AssertionError("backend dropdown should not probe model catalogs")

    monkeypatch.setattr("distr.core.project_cli_backends.catalog_probe.models_for_cli_backend", _fail_model_probe)

    clear_live_session_buffer(7, "codex", board_id=3)
    set_live_session_connected(7, "codex", True, board_id=3, external_session_id="thread-7")
    set_live_session_running(7, "codex", True, board_id=3)

    app = FastAPI()
    templates_dir = Path(__file__).resolve().parents[2] / "distr/gui/web/templates"
    app.include_router(create_routes(templates_dir), prefix="/api")
    client = TestClient(app)

    response = client.get("/api/projects/7/cli-backends", params={"board_id": 3})

    set_live_session_running(7, "codex", False, board_id=3)
    set_live_session_connected(7, "codex", False, board_id=3, external_session_id="")
    clear_live_session_buffer(7, "codex", board_id=3)

    assert response.status_code == 200
    row = response.json()["backends"][0]
    assert row["workflow_ready"] is True
    assert row["connected"] is True
    assert row["running"] is True
    assert row["external_thread_id"] == "thread-7"
    assert row["models_source"] == "runtime-snapshot"


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


def test_terminal_buffer_endpoint_reports_live_one_shot_cli_state(monkeypatch):
    from distr.gui.web.routes.settings import create_routes
    from distr.core.project_cli_backends.live_sessions import (
        clear_live_session_buffer,
        publish_live_session_event,
        set_live_session_connected,
        set_live_session_running,
    )

    class _FakeProject:
        id = 7
        folder_location = "/tmp/project"

    class _FakeQuery:
        def filter(self, *_args, **_kwargs):
            return self

        def first(self):
            return _FakeProject()

    class _FakeDbSession:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def query(self, _model):
            return _FakeQuery()

    class _FakeBackend:
        supports_rpc = False

    monkeypatch.setattr("distr.core.db.get_session", lambda: _FakeDbSession())
    monkeypatch.setattr("distr.gui.web.routes.settings.projects._backend_id_for_project", lambda project: "codex")
    monkeypatch.setattr("distr.core.project_cli_backends.get_backend", lambda backend_id: _FakeBackend())

    clear_live_session_buffer(7, "codex")
    publish_live_session_event(7, "codex", {"type": "message_end", "message": {"role": "user", "content": "Ship the workflow fix"}})
    publish_live_session_event(7, "codex", {"type": "message_update", "assistantMessageEvent": {"type": "text_delta", "delta": "Working on it"}})
    publish_live_session_event(7, "codex", {"type": "message_update", "assistantMessageEvent": {"type": "done"}})
    set_live_session_connected(7, "codex", True, external_session_id="thread-live-7")
    set_live_session_running(7, "codex", True)

    app = FastAPI()
    templates_dir = Path(__file__).resolve().parents[2] / "distr/gui/web/templates"
    app.include_router(create_routes(templates_dir), prefix="/api")
    client = TestClient(app)

    response = client.get("/api/projects/7/terminal/buffer")

    set_live_session_running(7, "codex", False)
    set_live_session_connected(7, "codex", False, external_session_id="")
    clear_live_session_buffer(7, "codex")

    assert response.status_code == 200
    payload = response.json()
    assert payload["alive"] is True
    assert payload["connected"] is True
    assert payload["backend_id"] == "codex"
    assert payload["external_thread_id"] == "thread-live-7"
    assert payload["supports_rpc"] is False
    assert "Ship the workflow fix" in payload["buffer"]
    assert "Working on it" in payload["buffer"]


def test_cli_models_endpoint_returns_verified_kiro_models(monkeypatch):
    from distr.gui.web.routes.settings import create_routes

    monkeypatch.setattr("distr.core.settings.load_settings_from_db", lambda: {})

    class _FakeBackend:
        def setup_status(self):
            return types.SimpleNamespace(path="/usr/local/bin/kiro-cli")

    class _FakeCompleted:
        returncode = 0
        stdout = '{"models":[{"model_id":"auto","model_name":"Auto"},{"model_id":"claude-sonnet-4-5","model_name":"Claude Sonnet 4.5"}]}'

    monkeypatch.setattr(
        "distr.core.project_cli_backends.catalog_probe.get_backend",
        lambda backend_id: _FakeBackend(),
    )
    monkeypatch.setattr(
        "distr.core.project_cli_backends.catalog_probe.subprocess.run",
        lambda *args, **kwargs: _FakeCompleted(),
    )

    app = FastAPI()
    templates_dir = Path(__file__).resolve().parents[2] / "distr/gui/web/templates"
    app.include_router(create_routes(templates_dir), prefix="/api")
    client = TestClient(app)

    response = client.get("/api/projects/cli-models", params={"backend_id": "kiro"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["source"] == "kiro-cli"
    assert [row["id"] for row in payload["models"]] == ["auto", "claude-sonnet-4-5"]


def test_cli_models_endpoint_returns_auto_only_when_codex_catalog_is_unverified(monkeypatch):
    from distr.gui.web.routes.settings import create_routes

    monkeypatch.setattr("distr.core.settings.load_settings_from_db", lambda: {})

    app = FastAPI()
    templates_dir = Path(__file__).resolve().parents[2] / "distr/gui/web/templates"
    app.include_router(create_routes(templates_dir), prefix="/api")
    client = TestClient(app)

    response = client.get("/api/projects/cli-models", params={"backend_id": "codex"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["source"] == "codex-unverified"
    assert [row["id"] for row in payload["models"]] == ["auto"]
    assert "Use Auto for Codex-managed model selection" in payload["message"]


def test_cli_setup_marks_local_cli_keys_as_optional(monkeypatch):
    from distr.gui.web.routes.settings import create_routes

    monkeypatch.setattr("distr.core.settings.load_settings_from_db", lambda: {})

    app = FastAPI()
    templates_dir = Path(__file__).resolve().parents[2] / "distr/gui/web/templates"
    app.include_router(create_routes(templates_dir), prefix="/api")
    client = TestClient(app)

    response = client.get("/api/projects/cli-setup", params={"backend_id": "codex"})

    assert response.status_code == 200
    payload = response.json()
    rows = {row["id"]: row for row in payload["clis"]}
    assert rows["codex"]["credential_optional"] is True
    assert "optional" in rows["codex"]["notes"].lower()
    assert rows["cursor"]["credential_optional"] is True
    assert rows["claude_code"]["credential_optional"] is True


def test_cli_setup_test_endpoint_uses_workflow_truth_for_success(monkeypatch):
    from distr.gui.web.routes.settings import create_routes

    monkeypatch.setattr("distr.core.settings.load_settings_from_db", lambda: {})

    class _FakeBackend:
        def setup_status(self):
            return types.SimpleNamespace(
                to_dict=lambda: {
                    "id": "codex",
                    "name": "Codex CLI",
                    "installed": True,
                    "ready": True,
                    "state": "ready",
                    "message": "Codex CLI is installed and ready.",
                }
            )

    monkeypatch.setattr("distr.core.project_cli_backends.get_backend", lambda backend_id: _FakeBackend())
    monkeypatch.setattr(
        "distr.core.project_cli_backends.catalog_probe.models_for_cli_backend",
        lambda backend_id, settings=None: {
            "models": [{"id": "auto", "name": "Auto", "provider": "codex"}],
            "source": "codex-unverified",
            "message": "Codex CLI did not return a verified model list.",
            "kind": "cli",
            "supports_model_picker": True,
        },
    )

    app = FastAPI()
    templates_dir = Path(__file__).resolve().parents[2] / "distr/gui/web/templates"
    app.include_router(create_routes(templates_dir), prefix="/api")
    client = TestClient(app)

    response = client.post("/api/projects/cli-setup/codex/test")

    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is False
    assert payload["truth"]["workflow_ready"] is False
    assert payload["truth"]["health_state"] == "setup"
