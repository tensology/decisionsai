"""Tests for WhatsApp media path helpers."""

from __future__ import annotations

import importlib.util
from pathlib import Path


_PATHS_MODULE_FILE = (
    Path(__file__).resolve().parents[2] / "distr" / "core" / "integrations" / "whatsapp" / "paths.py"
)
_SPEC = importlib.util.spec_from_file_location("wa_paths_module", _PATHS_MODULE_FILE)
assert _SPEC and _SPEC.loader
wa_paths = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(wa_paths)


def test_media_path_for_database_returns_relative_under_db_dir(tmp_path) -> None:
    db_dir = tmp_path / "db"
    media_file = db_dir / "whatsapp_media" / "voice.ogg"
    media_file.parent.mkdir(parents=True, exist_ok=True)
    media_file.write_bytes(b"audio")

    old_db_dir = wa_paths.DB_DIR
    wa_paths.DB_DIR = str(db_dir)
    try:
        stored = wa_paths.media_path_for_database(str(media_file))
    finally:
        wa_paths.DB_DIR = old_db_dir

    assert stored == "whatsapp_media/voice.ogg"


def test_media_path_for_database_keeps_absolute_outside_db_dir(tmp_path) -> None:
    db_dir = tmp_path / "db"
    external_file = tmp_path / "external" / "image.png"
    external_file.parent.mkdir(parents=True, exist_ok=True)
    external_file.write_bytes(b"img")

    old_db_dir = wa_paths.DB_DIR
    wa_paths.DB_DIR = str(db_dir)
    try:
        stored = wa_paths.media_path_for_database(str(external_file))
    finally:
        wa_paths.DB_DIR = old_db_dir

    assert stored == str(external_file)


def test_resolve_whatsapp_media_disk_path_resolves_relative_existing(tmp_path) -> None:
    db_dir = tmp_path / "db"
    media_file = db_dir / "whatsapp_media" / "clip.m4a"
    media_file.parent.mkdir(parents=True, exist_ok=True)
    media_file.write_bytes(b"audio")

    old_db_dir = wa_paths.DB_DIR
    wa_paths.DB_DIR = str(db_dir)
    try:
        resolved = wa_paths.resolve_whatsapp_media_disk_path("whatsapp_media/clip.m4a")
    finally:
        wa_paths.DB_DIR = old_db_dir

    assert Path(resolved) == media_file.resolve()


def test_resolve_whatsapp_media_disk_path_falls_back_to_legacy_basename(tmp_path) -> None:
    db_dir = tmp_path / "db"
    media_file = db_dir / "whatsapp_media" / "legacy.mp3"
    media_file.parent.mkdir(parents=True, exist_ok=True)
    media_file.write_bytes(b"audio")

    old_db_dir = wa_paths.DB_DIR
    wa_paths.DB_DIR = str(db_dir)
    try:
        resolved = wa_paths.resolve_whatsapp_media_disk_path("/old/storage/path/legacy.mp3")
    finally:
        wa_paths.DB_DIR = old_db_dir

    assert Path(resolved) == media_file.resolve()
