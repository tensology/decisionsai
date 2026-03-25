"""Unit tests for distr.core.skin_discovery."""

import json
import logging
from pathlib import Path

import pytest

from distr.core.skin_discovery import discover_skins, get_skin_by_name


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_MINIMAL_ORACLE = {
    "type": "oracle",
    "name": "Oracle",
    "rendering": {"shape": "round", "border": True, "shadow": True, "glow_on_hold": True},
    "events": {
        "idle": {
            "animation": "0.gif",
            "show_player": False,
            "show_chat_bubble": False,
            "glow": False,
        }
    },
}

_MINIMAL_AVATAR = {
    "type": "avatar",
    "name": "Clippy",
    "rendering": {"shape": "square", "border": False, "shadow": False, "glow_on_hold": False},
    "events": {
        "idle": {
            "animation": "idle.webm",
            "show_player": False,
            "show_chat_bubble": False,
            "glow": False,
        }
    },
}


def _write_skin(base: Path, folder: str, data: dict) -> None:
    d = base / folder
    d.mkdir(parents=True, exist_ok=True)
    (d / "skin.json").write_text(json.dumps(data), encoding="utf-8")


# ---------------------------------------------------------------------------
# discover_skins tests
# ---------------------------------------------------------------------------


class TestDiscoverSkins:
    def test_returns_valid_skins(self, tmp_path: Path):
        _write_skin(tmp_path, "oracle", _MINIMAL_ORACLE)
        _write_skin(tmp_path, "clippy", _MINIMAL_AVATAR)

        result = discover_skins(tmp_path)
        names = [name for name, _ in result]

        assert len(result) == 2
        assert "oracle" in names
        assert "clippy" in names

    def test_oracle_sorted_first(self, tmp_path: Path):
        _write_skin(tmp_path, "zzz-avatar", _MINIMAL_AVATAR)
        _write_skin(tmp_path, "oracle", _MINIMAL_ORACLE)

        result = discover_skins(tmp_path)
        assert result[0][0] == "oracle"
        assert result[0][1].type == "oracle"

    def test_avatars_sorted_alphabetically_by_name(self, tmp_path: Path):
        avatar_b = {**_MINIMAL_AVATAR, "name": "Bravo"}
        avatar_a = {**_MINIMAL_AVATAR, "name": "Alpha"}

        _write_skin(tmp_path, "skin-b", avatar_b)
        _write_skin(tmp_path, "skin-a", avatar_a)

        result = discover_skins(tmp_path)
        assert result[0][1].name == "Alpha"
        assert result[1][1].name == "Bravo"

    def test_excludes_folder_without_skin_json(self, tmp_path: Path, caplog):
        (tmp_path / "empty-folder").mkdir()
        _write_skin(tmp_path, "oracle", _MINIMAL_ORACLE)

        with caplog.at_level(logging.WARNING):
            result = discover_skins(tmp_path)

        assert len(result) == 1
        assert "empty-folder" in caplog.text

    def test_excludes_invalid_json(self, tmp_path: Path, caplog):
        d = tmp_path / "bad"
        d.mkdir()
        (d / "skin.json").write_text("NOT JSON", encoding="utf-8")
        _write_skin(tmp_path, "oracle", _MINIMAL_ORACLE)

        with caplog.at_level(logging.WARNING):
            result = discover_skins(tmp_path)

        assert len(result) == 1
        assert "bad" in caplog.text

    def test_excludes_invalid_config(self, tmp_path: Path, caplog):
        bad_config = {
            "type": "oracle",
            "name": "Bad",
            "rendering": {"shape": "square", "border": False, "shadow": False, "glow_on_hold": False},
            "events": {"idle": {"animation": "x.gif"}},
        }
        _write_skin(tmp_path, "bad-oracle", bad_config)
        _write_skin(tmp_path, "oracle", _MINIMAL_ORACLE)

        with caplog.at_level(logging.WARNING):
            result = discover_skins(tmp_path)

        assert len(result) == 1
        assert "bad-oracle" in caplog.text

    def test_nonexistent_directory(self, tmp_path: Path, caplog):
        with caplog.at_level(logging.WARNING):
            result = discover_skins(tmp_path / "nope")

        assert result == []
        assert "does not exist" in caplog.text

    def test_empty_directory(self, tmp_path: Path):
        result = discover_skins(tmp_path)
        assert result == []

    def test_ignores_files_in_avatars_dir(self, tmp_path: Path):
        (tmp_path / "readme.txt").write_text("hi")
        _write_skin(tmp_path, "oracle", _MINIMAL_ORACLE)

        result = discover_skins(tmp_path)
        assert len(result) == 1

    def test_accepts_string_path(self, tmp_path: Path):
        _write_skin(tmp_path, "oracle", _MINIMAL_ORACLE)
        result = discover_skins(str(tmp_path))
        assert len(result) == 1

    def test_real_avatars_dir(self):
        """Smoke test against the actual shipped avatars directory."""
        project_root = Path(__file__).resolve().parents[2]
        avatars = project_root / "assets" / "avatars"
        if not avatars.is_dir():
            pytest.skip("assets/avatars not found")

        result = discover_skins(avatars)
        assert len(result) >= 1
        # Oracle should be first
        assert result[0][1].type == "oracle"


# ---------------------------------------------------------------------------
# get_skin_by_name tests
# ---------------------------------------------------------------------------


class TestGetSkinByName:
    def test_returns_valid_skin(self, tmp_path: Path):
        _write_skin(tmp_path, "oracle", _MINIMAL_ORACLE)

        result = get_skin_by_name(tmp_path, "oracle")
        assert result is not None
        folder, config = result
        assert folder == "oracle"
        assert config.type == "oracle"
        assert config.name == "Oracle"

    def test_returns_none_for_missing_folder(self, tmp_path: Path):
        result = get_skin_by_name(tmp_path, "nonexistent")
        assert result is None

    def test_returns_none_for_invalid_json(self, tmp_path: Path):
        d = tmp_path / "bad"
        d.mkdir()
        (d / "skin.json").write_text("{invalid", encoding="utf-8")

        result = get_skin_by_name(tmp_path, "bad")
        assert result is None

    def test_returns_none_for_invalid_config(self, tmp_path: Path):
        bad_config = {
            "type": "oracle",
            "name": "Bad",
            "rendering": {"shape": "square", "border": False, "shadow": False, "glow_on_hold": False},
            "events": {"idle": {"animation": "x.gif"}},
        }
        _write_skin(tmp_path, "bad-oracle", bad_config)

        result = get_skin_by_name(tmp_path, "bad-oracle")
        assert result is None

    def test_accepts_string_path(self, tmp_path: Path):
        _write_skin(tmp_path, "oracle", _MINIMAL_ORACLE)
        result = get_skin_by_name(str(tmp_path), "oracle")
        assert result is not None
