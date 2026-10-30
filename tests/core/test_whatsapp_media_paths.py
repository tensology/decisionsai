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

_MANAGER_FILE = (
    Path(__file__).resolve().parents[2]
    / "distr"
    / "core"
    / "integrations"
    / "whatsapp"
    / "manager.py"
)


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


def test_inbound_media_handler_does_not_transcribe_voice_inline() -> None:
    source = _MANAGER_FILE.read_text()
    start = source.index("    def _handle_inbound_media")
    end = source.index("    # \u2550", start)
    handler_source = source[start:end]

    assert "_schedule_voice_transcription" in handler_source
    assert "transcribe_audio_file(abs_saved)" not in handler_source


def test_burst_messages_keep_distinct_original_bytes_and_extensions(tmp_path, monkeypatch):
    monkeypatch.setattr(wa_paths, 'DB_DIR', str(tmp_path))
    a = wa_paths.store_whatsapp_media(b'%PDF-first original', message_id='first', media_type='document', mime_type='application/pdf')
    b = wa_paths.store_whatsapp_media(b'%PDF-second original', message_id='second', media_type='document', mime_type='application/pdf')
    assert a != b and Path(a).suffix == Path(b).suffix == '.pdf'
    assert Path(a).read_bytes() == b'%PDF-first original'
    assert Path(b).read_bytes() == b'%PDF-second original'
    repeated = wa_paths.store_whatsapp_media(b'%PDF-first original', message_id='first', media_type='document', mime_type='application/pdf')
    assert repeated == a
    changed = wa_paths.store_whatsapp_media(b'%PDF-updated original', message_id='first', media_type='document', mime_type='application/pdf')
    assert changed != a and Path(a).read_bytes() == b'%PDF-first original'


def test_failed_write_preserves_registered_file_and_cleans_partial(tmp_path, monkeypatch):
    import pytest
    monkeypatch.setattr(wa_paths, 'DB_DIR', str(tmp_path))
    args = dict(message_id='../../contact', media_type='../../document', mime_type='image/jpeg; charset=binary')
    path = wa_paths.store_whatsapp_media(b'original', **args)
    assert Path(path).parent == tmp_path / 'whatsapp_media'
    assert Path(path).suffix == '.jpg'
    def fail(*args):
        raise OSError('Simulated replace failure')
    monkeypatch.setattr(Path, 'replace', fail)
    with pytest.raises(OSError):
        wa_paths.store_whatsapp_media(b'new bytes', **args)
    assert Path(path).read_bytes() == b'original'
    assert not list(tmp_path.rglob('*.part'))
    with pytest.raises(ValueError, match='empty'):
        wa_paths.store_whatsapp_media(b'', **args)


def test_inbound_same_name_pdfs_register_their_own_bytes(tmp_path, monkeypatch):
    import base64
    from types import SimpleNamespace
    from unittest.mock import Mock
    from distr.core.integrations.whatsapp.manager import WhatsAppWebSocketManager
    from distr.core.integrations.whatsapp import paths

    monkeypatch.setattr('distr.core.paths.DB_DIR', str(tmp_path))
    monkeypatch.setattr(paths, 'DB_DIR', str(tmp_path))
    manager = SimpleNamespace(_update_media_info=Mock(), _mark_relay_processed_by_message_id=Mock(), _schedule_voice_transcription=Mock())
    originals = [b'%PDF-first original', b'%PDF-second original']
    for number, content in enumerate(originals):
        WhatsAppWebSocketManager._handle_inbound_media(manager, {'message_id': f'message-{number}', 'media_type': 'document',
            'mime_type': 'application/pdf', 'filename': 'duplicate.pdf', 'file_length': len(content),
            'data_b64': base64.b64encode(content).decode()})
    registrations = manager._update_media_info.call_args_list
    assert len(registrations) == 2
    assert registrations[0].args[1] != registrations[1].args[1]
    for call, content in zip(registrations, originals):
        assert (tmp_path / call.args[1]).read_bytes() == content
