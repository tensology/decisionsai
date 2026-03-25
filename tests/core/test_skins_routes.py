"""Unit tests for the skins API routes.

Tests the 6 endpoints in distr/gui/web/routes/settings/skins.py
using FastAPI TestClient with mocked dependencies.

Requirements: 11.1-11.10
"""
import json
import os
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

from distr.core.skin_config import SkinConfig, RenderingConfig, EventResponse
from distr.gui.web.routes.settings.skins import register_routes


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

MINIMAL_SKIN_JSON = json.dumps({
    "type": "oracle",
    "name": "TestOracle",
    "rendering": {"shape": "round", "border": True, "shadow": True, "glow_on_hold": True},
    "events": {
        "idle": {
            "animation": "idle.webm",
            "show_player": False,
            "show_chat_bubble": False,
            "glow": False,
            "glow_color": [0, 0, 0],
            "glow_speed": 1000,
            "glow_style": "breathing",
            "tray_icon": "default",
        }
    },
    "transitions": {},
})


@pytest.fixture
def skin_dir(tmp_path):
    """Create a temp avatars dir with one valid skin folder."""
    skin_folder = tmp_path / "test-skin"
    skin_folder.mkdir()
    (skin_folder / "skin.json").write_text(MINIMAL_SKIN_JSON, encoding="utf-8")
    (skin_folder / "idle.webm").write_bytes(b"\x1a\x45\xdf\xa3")  # fake webm
    (skin_folder / "idle.gif").write_bytes(b"GIF89a")  # fake gif
    (skin_folder / "readme.txt").write_text("not an animation")
    return tmp_path


@pytest.fixture
def client(skin_dir):
    """Create a TestClient with the skins router, patching AVATARS_DIR."""
    app = FastAPI()
    router = APIRouter()
    register_routes(router, None)
    app.include_router(router)

    with patch("distr.gui.web.routes.settings.skins.discover_skins") as mock_discover, \
         patch("distr.gui.web.routes.settings.skins.get_skin_by_name") as mock_get_skin:
        # We won't use these patches by default — individual tests patch as needed
        pass

    return TestClient(app), skin_dir


# ---------------------------------------------------------------------------
# GET /api/skins
# ---------------------------------------------------------------------------

class TestListSkins:
    def test_returns_discovered_skins(self, skin_dir):
        app = FastAPI()
        router = APIRouter()
        register_routes(router, None)
        app.include_router(router)

        with patch("distr.core.paths.AVATARS_DIR", str(skin_dir)):
            client = TestClient(app)
            resp = client.get("/skins")

        assert resp.status_code == 200
        data = resp.json()
        assert "skins" in data
        assert len(data["skins"]) == 1
        assert data["skins"][0]["folder_name"] == "test-skin"
        assert data["skins"][0]["name"] == "TestOracle"
        assert data["skins"][0]["type"] == "oracle"

    def test_empty_when_no_valid_skins(self, tmp_path):
        app = FastAPI()
        router = APIRouter()
        register_routes(router, None)
        app.include_router(router)

        with patch("distr.core.paths.AVATARS_DIR", str(tmp_path)):
            client = TestClient(app)
            resp = client.get("/skins")

        assert resp.status_code == 200
        assert resp.json()["skins"] == []


# ---------------------------------------------------------------------------
# POST /api/skins/select
# ---------------------------------------------------------------------------

class TestSelectSkin:
    def test_valid_skin_selection(self, skin_dir):
        app = FastAPI()
        router = APIRouter()
        register_routes(router, None)
        app.include_router(router)

        with patch("distr.core.paths.AVATARS_DIR", str(skin_dir)), \
             patch("distr.core.services.settings_service.update_oracle_skin") as mock_update:
            client = TestClient(app)
            resp = client.post("/skins/select", json={"skin_name": "test-skin"})

        assert resp.status_code == 200
        assert resp.json()["success"] is True
        assert resp.json()["selected_skin"] == "test-skin"
        mock_update.assert_called_once_with("test-skin")

    def test_invalid_skin_returns_400(self, skin_dir):
        app = FastAPI()
        router = APIRouter()
        register_routes(router, None)
        app.include_router(router)

        with patch("distr.core.paths.AVATARS_DIR", str(skin_dir)):
            client = TestClient(app)
            resp = client.post("/skins/select", json={"skin_name": "nonexistent"})

        assert resp.status_code == 400
        assert "nonexistent" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# GET /api/skins/{name}/config
# ---------------------------------------------------------------------------

class TestGetSkinConfig:
    def test_returns_config_for_valid_skin(self, skin_dir):
        app = FastAPI()
        router = APIRouter()
        register_routes(router, None)
        app.include_router(router)

        with patch("distr.core.paths.AVATARS_DIR", str(skin_dir)):
            client = TestClient(app)
            resp = client.get("/skins/test-skin/config")

        assert resp.status_code == 200
        data = resp.json()
        assert data["type"] == "oracle"
        assert data["name"] == "TestOracle"
        assert "events" in data
        assert "idle" in data["events"]

    def test_returns_404_for_missing_skin(self, skin_dir):
        app = FastAPI()
        router = APIRouter()
        register_routes(router, None)
        app.include_router(router)

        with patch("distr.core.paths.AVATARS_DIR", str(skin_dir)):
            client = TestClient(app)
            resp = client.get("/skins/nonexistent/config")

        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# PUT /api/skins/{name}/config
# ---------------------------------------------------------------------------

class TestUpdateSkinConfig:
    def test_valid_config_update(self, skin_dir):
        app = FastAPI()
        router = APIRouter()
        register_routes(router, None)
        app.include_router(router)

        updated = json.loads(MINIMAL_SKIN_JSON)
        updated["name"] = "UpdatedOracle"

        with patch("distr.core.paths.AVATARS_DIR", str(skin_dir)):
            client = TestClient(app)
            resp = client.put("/skins/test-skin/config", json=updated)

        assert resp.status_code == 200
        assert resp.json()["success"] is True

        # Verify it was written to disk
        written = json.loads((skin_dir / "test-skin" / "skin.json").read_text())
        assert written["name"] == "UpdatedOracle"

    def test_invalid_config_returns_400(self, skin_dir):
        app = FastAPI()
        router = APIRouter()
        register_routes(router, None)
        app.include_router(router)

        with patch("distr.core.paths.AVATARS_DIR", str(skin_dir)):
            client = TestClient(app)
            resp = client.put("/skins/test-skin/config", json={"bad": "data"})

        assert resp.status_code == 400

    def test_nonexistent_folder_returns_404(self, skin_dir):
        app = FastAPI()
        router = APIRouter()
        register_routes(router, None)
        app.include_router(router)

        with patch("distr.core.paths.AVATARS_DIR", str(skin_dir)):
            client = TestClient(app)
            resp = client.put("/skins/nonexistent/config", json=json.loads(MINIMAL_SKIN_JSON))

        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# GET /api/skins/{name}/files
# ---------------------------------------------------------------------------

class TestListSkinFiles:
    def test_returns_only_animation_files(self, skin_dir):
        app = FastAPI()
        router = APIRouter()
        register_routes(router, None)
        app.include_router(router)

        with patch("distr.core.paths.AVATARS_DIR", str(skin_dir)):
            client = TestClient(app)
            resp = client.get("/skins/test-skin/files")

        assert resp.status_code == 200
        files = resp.json()["files"]
        assert "idle.webm" in files
        assert "idle.gif" in files
        assert "readme.txt" not in files
        assert "skin.json" not in files

    def test_nonexistent_folder_returns_404(self, skin_dir):
        app = FastAPI()
        router = APIRouter()
        register_routes(router, None)
        app.include_router(router)

        with patch("distr.core.paths.AVATARS_DIR", str(skin_dir)):
            client = TestClient(app)
            resp = client.get("/skins/nonexistent/files")

        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# GET /api/skins/{name}/preview/{filename}
# ---------------------------------------------------------------------------

class TestPreviewSkinFile:
    def test_serves_webm_file(self, skin_dir):
        app = FastAPI()
        router = APIRouter()
        register_routes(router, None)
        app.include_router(router)

        with patch("distr.core.paths.AVATARS_DIR", str(skin_dir)):
            client = TestClient(app)
            resp = client.get("/skins/test-skin/preview/idle.webm")

        assert resp.status_code == 200
        assert resp.headers["content-type"] == "video/webm"

    def test_serves_gif_file(self, skin_dir):
        app = FastAPI()
        router = APIRouter()
        register_routes(router, None)
        app.include_router(router)

        with patch("distr.core.paths.AVATARS_DIR", str(skin_dir)):
            client = TestClient(app)
            resp = client.get("/skins/test-skin/preview/idle.gif")

        assert resp.status_code == 200
        assert resp.headers["content-type"] == "image/gif"

    def test_missing_file_returns_404(self, skin_dir):
        app = FastAPI()
        router = APIRouter()
        register_routes(router, None)
        app.include_router(router)

        with patch("distr.core.paths.AVATARS_DIR", str(skin_dir)):
            client = TestClient(app)
            resp = client.get("/skins/test-skin/preview/missing.webm")

        assert resp.status_code == 404

    def test_unsupported_file_type_returns_400(self, skin_dir):
        app = FastAPI()
        router = APIRouter()
        register_routes(router, None)
        app.include_router(router)

        # Create a .txt file to try to serve
        (skin_dir / "test-skin" / "notes.txt").write_text("hello")

        with patch("distr.core.paths.AVATARS_DIR", str(skin_dir)):
            client = TestClient(app)
            resp = client.get("/skins/test-skin/preview/notes.txt")

        assert resp.status_code == 400
