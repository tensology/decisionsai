"""Unit tests for the skins API routes.

Tests the 6 endpoints in distr/gui/web/routes/settings/skins.py
using FastAPI TestClient with mocked dependencies.

Requirements: 11.1-11.10
"""
import json
import io
import os
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock
from zipfile import ZipFile

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient
from PIL import Image

from distr.core.skin_config import SkinConfig, RenderingConfig, EventResponse
from distr.gui.web.routes.settings.skins import register_routes


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

MINIMAL_SKIN_JSON = json.dumps({
    "type": "oracle",
    "name": "TestOracle",
    "rendering": {
        "shape": "round", "border": True, "shadow": True, "glow_on_hold": True,
        "chroma_key": [0, 255, 0], "chroma_threshold": 35,
    },
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
    (skin_folder / "idle.webp").write_bytes(b"RIFF")  # authored picker thumbnail
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

    return TestClient(app), skin_dir


class TestSkinArchives:
    def _files(self, config_name="Travel Compass"):
        config = json.loads(MINIMAL_SKIN_JSON)
        config["name"] = config_name
        config["events"]["idle"]["animation"] = "idle.png"
        image = io.BytesIO()
        Image.new("RGB", (16, 16), "navy").save(image, format="PNG")
        return {"skin.json": json.dumps(config).encode(), "idle.png": image.getvalue()}

    def _zip(self, files, prefix=""):
        buffer = io.BytesIO()
        with ZipFile(buffer, "w") as archive:
            for name, data in files.items():
                archive.writestr(prefix + name, data)
        return buffer.getvalue()

    def test_export_and_import_round_trip(self, client):
        app, root = client
        files = self._files()
        source = root / "test-skin"
        for path in source.iterdir():
            path.unlink()
        for name, data in files.items():
            (source / name).write_bytes(data)
        with patch("distr.core.paths.AVATARS_DIR", str(root)), patch(
            "distr.core.services.settings_service.update_oracle_skin",
        ) as activate:
            exported = app.get("/skins/test-skin/export")
            assert exported.status_code == 200
            assert "test-skin.zip" in exported.headers["content-disposition"]
            assert exported.headers["content-type"] == "application/zip"
            response = app.post("/skins/import", content=exported.content, headers={"Content-Type": "application/zip"})
            assert response.status_code == 201
            assert response.json() == {"folder_name": "imported-travel-compass", "name": "Travel Compass"}
            activate.assert_called_once_with("imported-travel-compass")
            installed = root / "imported-travel-compass"
            assert (installed / "idle.png").read_bytes() == files["idle.png"]
            assert app.get("/skins/imported-travel-compass/config").json()["name"] == "Travel Compass"
            collision = app.post("/skins/import", content=exported.content, headers={"Content-Type": "application/zip"})
            assert collision.status_code == 409
            activate.assert_called_once()

    def test_single_wrapped_skin_folder_supported(self, client):
        app, root = client
        upload = self._zip(self._files(), "Portable skin/")
        with patch("distr.core.paths.AVATARS_DIR", str(root)), patch(
            "distr.core.services.settings_service.update_oracle_skin",
        ):
            result = app.post("/skins/import", content=upload, headers={"Content-Type": "application/zip"})
        assert result.status_code == 201

    @pytest.mark.parametrize("change,detail", [
        ("missing", "missing 'idle.png'"),
        ("unsupported", "Unsupported file"),
        ("wrong-media", "does not match its media format"),
        ("nested", "nested, duplicate, or invalid filenames"),
        ("not-zip", "Could not read"),
    ])
    def test_invalid_import_never_installs_or_loads(self, client, change, detail):
        app, root = client
        files = self._files()
        if change == "missing":
            del files["idle.png"]
        elif change == "unsupported":
            files["notes.txt"] = b"unsupported"
        elif change == "wrong-media":
            files["idle.png"] = b"not image data"
        elif change == "nested":
            files["folder/another.png"] = files["idle.png"]
        content = self._zip(files) if change != "not-zip" else b"not a zip"
        with patch("distr.core.paths.AVATARS_DIR", str(root)), patch(
            "distr.core.services.settings_service.update_oracle_skin",
        ) as activate:
            result = app.post("/skins/import", content=content, headers={"Content-Type": "application/zip"})
        assert result.status_code == 422
        assert detail in result.json()["detail"]
        assert [path.name for path in root.iterdir()] == ["test-skin"]
        activate.assert_not_called()


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
        assert data["skins"][0]["preview_image"] == "idle.webp"
        assert data["skins"][0]["chroma_key"] == [0, 255, 0]
        assert data["skins"][0]["chroma_threshold"] == 35
        assert data["skins"][0]["preview_image_current"] is True
        assert len(data["skins"][0]["media_version"]) == 12

    def test_media_version_changes_and_stale_still_is_flagged(self, skin_dir):
        app = FastAPI()
        router = APIRouter()
        register_routes(router, None)
        app.include_router(router)
        folder = skin_dir / "test-skin"

        with patch("distr.core.paths.AVATARS_DIR", str(skin_dir)):
            client = TestClient(app)
            before = client.get("/skins").json()["skins"][0]
            # The idle clip is regenerated after the authored still.
            still_time = os.stat(folder / "idle.webp").st_mtime_ns
            (folder / "idle.webm").write_bytes(b"\x1a\x45\xdf\xa3 new clip")
            os.utime(folder / "idle.webm", ns=(still_time + 10**9, still_time + 10**9))
            after = client.get("/skins").json()["skins"][0]

        assert after["media_version"] != before["media_version"]
        assert after["preview_image"] == "idle.webp"  # the desktop picker keeps its thumbnail
        assert after["preview_image_current"] is False

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
# GET and POST /api/skins/{name}/size
# ---------------------------------------------------------------------------

def test_skin_size_route_uses_skin_default_and_accepts_a_larger_size(skin_dir):
    config_file = skin_dir / "test-skin" / "skin.json"
    config = json.loads(config_file.read_text())
    config["rendering"]["default_size_px"] = 280
    config_file.write_text(json.dumps(config))
    app = FastAPI()
    router = APIRouter()
    register_routes(router, None)
    app.include_router(router)

    with patch("distr.core.paths.AVATARS_DIR", str(skin_dir)), patch(
        "distr.core.db.skin_sizes.get_skin_size", side_effect=lambda _, default: default
    ), patch("distr.core.db.skin_sizes.set_skin_size") as save_size:
        client = TestClient(app)
        assert client.get("/skins/test-skin/size").json() == {
            "skin_name": "test-skin", "sphere_size": 14, "pixels": 280,
        }
        assert client.post("/skins/test-skin/size", json={"sphere_size": 16}).json()["pixels"] == 320
        save_size.assert_called_once_with("test-skin", 320)


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
