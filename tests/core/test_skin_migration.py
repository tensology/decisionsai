"""Unit tests for skin migration edge cases and dynamic discovery.

Validates: Requirements 13.1, 13.2, 13.3
"""

import json
from pathlib import Path

import pytest

from distr.core.skin_migration import migrate_selected_oracle
from distr.core.skin_discovery import discover_skins


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
    "name": "TestAvatar",
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
# Migration edge cases
# ---------------------------------------------------------------------------


class TestMigrateSelectedOracle:
    """Requirement 13.1: GIF filenames map to 'oracle'."""

    def test_0_gif_maps_to_oracle(self):
        assert migrate_selected_oracle("0.gif") == "oracle"

    def test_1_gif_maps_to_oracle(self):
        assert migrate_selected_oracle("1.gif") == "oracle"

    def test_12_gif_maps_to_oracle(self):
        assert migrate_selected_oracle("12.gif") == "oracle"

    """Requirement 13.2: Folder names pass through unchanged."""

    def test_clippy_passes_through(self):
        assert migrate_selected_oracle("clippy") == "clippy"

    def test_cupidon_passes_through(self):
        assert migrate_selected_oracle("cupidon") == "cupidon"

    def test_custom_skin_passes_through(self):
        assert migrate_selected_oracle("some-custom-skin") == "some-custom-skin"

    """Requirement 13.3: Empty/None defaults to 'oracle'."""

    def test_empty_string_defaults_to_oracle(self):
        assert migrate_selected_oracle("") == "oracle"

    def test_none_defaults_to_oracle(self):
        assert migrate_selected_oracle(None) == "oracle"


# ---------------------------------------------------------------------------
# Dynamic discovery with added/removed folders
# ---------------------------------------------------------------------------


class TestDynamicDiscovery:
    """Verify discover_skins() reflects filesystem changes dynamically."""

    def test_discovers_initial_skins(self, tmp_path: Path):
        _write_skin(tmp_path, "oracle", _MINIMAL_ORACLE)
        _write_skin(tmp_path, "alpha", {**_MINIMAL_AVATAR, "name": "Alpha"})

        result = discover_skins(tmp_path)
        names = [name for name, _ in result]

        assert len(result) == 2
        assert "oracle" in names
        assert "alpha" in names

    def test_new_folder_appears_on_rescan(self, tmp_path: Path):
        _write_skin(tmp_path, "oracle", _MINIMAL_ORACLE)

        result1 = discover_skins(tmp_path)
        assert len(result1) == 1

        # Add a new skin folder
        _write_skin(tmp_path, "new-skin", {**_MINIMAL_AVATAR, "name": "NewSkin"})

        result2 = discover_skins(tmp_path)
        names2 = [name for name, _ in result2]

        assert len(result2) == 2
        assert "new-skin" in names2

    def test_removed_skin_json_disappears_on_rescan(self, tmp_path: Path):
        _write_skin(tmp_path, "oracle", _MINIMAL_ORACLE)
        _write_skin(tmp_path, "removable", {**_MINIMAL_AVATAR, "name": "Removable"})

        result1 = discover_skins(tmp_path)
        assert len(result1) == 2

        # Remove the skin.json from the removable folder
        (tmp_path / "removable" / "skin.json").unlink()

        result2 = discover_skins(tmp_path)
        names2 = [name for name, _ in result2]

        assert len(result2) == 1
        assert "removable" not in names2
        assert "oracle" in names2
