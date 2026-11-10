import json
import subprocess
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

from tests.core.test_incoming_media import store
from distr.core.db import Chat
from distr.core.incoming import media, transcription


def test_transcription_worker_has_a_real_deadline_and_reads_only_result_json(tmp_path, monkeypatch):
    calls = []
    def run(command, **kwargs):
        calls.append((command, kwargs))
        Path(command[-1]).write_text(json.dumps({'transcript': 'Worker result.'}))
    monkeypatch.setattr(transcription.subprocess, 'run', run)
    assert transcription._transcribe(tmp_path / 'source.mp4') == 'Worker result.'
    command, options = calls[0]
    assert command[1:3] == ['-m', 'distr.core.incoming.transcription']
    assert options['timeout'] == transcription.TRANSCRIPTION_TIMEOUT_SECONDS
    assert options['stdout'] == options['stderr'] == subprocess.DEVNULL
    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], kwargs['timeout'])
    monkeypatch.setattr(transcription.subprocess, 'run', timeout)
    with pytest.raises(RuntimeError, match='original media remains available'):
        transcription._transcribe(tmp_path / 'source.mp4')


def test_transcription_lock_wait_is_bounded_without_starting_another_model(store, tmp_path, monkeypatch):
    _, chat_id, record = attach(store, tmp_path)
    lock = Mock()
    lock.acquire.return_value = False
    monkeypatch.setattr(transcription, '_transcription_lock', lock)
    decoder = Mock()
    monkeypatch.setattr(transcription, '_transcribe', decoder)
    report = transcription.prepare_thread_transcripts(chat_id)
    assert report['failed'] == [{'id': record['id'], 'name': record['name']}]
    lock.acquire.assert_called_once_with(timeout=transcription.TRANSCRIPTION_LOCK_TIMEOUT_SECONDS)
    lock.release.assert_not_called()
    decoder.assert_not_called()


def attach(store, tmp_path, *, transcript=''):
    sessions, chat_id = store
    path = tmp_path / '.decisions/workspaces/projects/7/attachments/source.mp4'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b'original video bytes')
    record = media._record(path, 'OPAQUE-NAME.mp4', 'video/mp4', 'video', transcript=transcript)
    media.register_thread_attachments(chat_id, [record])
    return sessions, chat_id, record


def test_transcription_is_cached_without_overwriting_thread_or_original(store, tmp_path, monkeypatch):
    sessions, chat_id, record = attach(store, tmp_path)
    with sessions() as db:
        before = db.get(Chat, chat_id).params
    transcribe = Mock(return_value='The client wants the vehicle details.')
    monkeypatch.setattr(transcription, '_transcribe', transcribe)
    result = transcription.transcribe_thread_attachment(chat_id, record['id'])
    assert result['transcript'] == 'The client wants the vehicle details.'
    assert result['cached'] is False
    assert transcription.transcribe_thread_attachment(chat_id, record['id'])['cached'] is True
    transcribe.assert_called_once()
    assert Path(record['path']).read_bytes() == b'original video bytes'
    with sessions() as db:
        assert db.get(Chat, chat_id).params == before
    assert media.public_thread_attachments(chat_id)[0]['transcript'] == result['transcript']
    inputs = media.required_thread_attachments(chat_id)
    assert len(inputs) == 2
    assert inputs[1]['mime_type'] == 'text/plain'
    assert Path(inputs[1]['path']).read_text() == result['transcript']


def test_existing_transcript_is_preserved(store, tmp_path, monkeypatch):
    _, chat_id, record = attach(store, tmp_path, transcript='Existing source transcript.')
    transcribe = Mock(side_effect=AssertionError('must preserve existing transcript'))
    monkeypatch.setattr(transcription, '_transcribe', transcribe)
    assert transcription.transcribe_thread_attachment(chat_id, record['id'])['transcript'] == 'Existing source transcript.'
    inputs = media.required_thread_attachments(chat_id)
    assert Path(inputs[1]['path']).read_text() == 'Existing source transcript.'
    transcribe.assert_not_called()


def test_parallel_opening_generates_only_one_transcript(store, tmp_path, monkeypatch):
    _, chat_id, record = attach(store, tmp_path)
    transcribe = Mock(return_value='One transcript.')
    monkeypatch.setattr(transcription, '_transcribe', transcribe)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: transcription.transcribe_thread_attachment(chat_id, record['id']), range(2)))
    assert transcribe.call_count == 1
    assert all(item['transcript'] == 'One transcript.' for item in results)


def test_empty_speech_is_cached_and_failure_can_retry(store, tmp_path, monkeypatch):
    _, chat_id, record = attach(store, tmp_path)
    transcribe = Mock(side_effect=[RuntimeError('decoder failed'), ''])
    monkeypatch.setattr(transcription, '_transcribe', transcribe)
    with pytest.raises(RuntimeError):
        transcription.transcribe_thread_attachment(chat_id, record['id'])
    assert not transcription.transcript_path(record).exists()
    assert transcription.transcribe_thread_attachment(chat_id, record['id'])['status'] == 'empty'
    assert transcription.transcribe_thread_attachment(chat_id, record['id'])['cached'] is True
    assert transcribe.call_count == 2


def test_route_enforces_thread_scope_and_reports_failure_without_paths(store, tmp_path, monkeypatch):
    _, chat_id, record = attach(store, tmp_path)
    from distr.gui.web.routes.development.threads import register_routes
    app, router = FastAPI(), APIRouter()
    register_routes(router, None)
    app.include_router(router)
    client = TestClient(app)
    url = f'/workflows/studio/tasks/{chat_id}/attachments/{record["id"]}/transcript'
    monkeypatch.setattr(transcription, '_transcribe', Mock(return_value='A voice note.'))
    assert client.post(url).json()['transcript'] == 'A voice note.'
    assert client.post(url.replace(f'/tasks/{chat_id}/', '/tasks/999999/')).status_code == 404
    transcription.transcript_path(record).unlink()
    monkeypatch.setattr(transcription, '_transcribe', Mock(side_effect=RuntimeError('/private/path/token')))
    response = client.post(url)
    assert response.status_code == 503
    assert '/private' not in response.text


def test_image_cannot_be_sent_to_transcription_and_changed_bytes_are_rejected(store, tmp_path, monkeypatch):
    _, chat_id, record = attach(store, tmp_path)
    record.update(name='image.png', mime_type='image/png')
    media.register_thread_attachments(chat_id, [record])
    transcribe = Mock()
    monkeypatch.setattr(transcription, '_transcribe', transcribe)
    with pytest.raises(ValueError):
        transcription.transcribe_thread_attachment(chat_id, record['id'])
    Path(record['path']).write_bytes(b'changed')
    with pytest.raises(FileNotFoundError):
        transcription.transcribe_thread_attachment(chat_id, record['id'])
    transcribe.assert_not_called()


@pytest.mark.parametrize('name', ['voice.oga', 'VOICE.OGA'])
@pytest.mark.parametrize('mime', ['', 'application/octet-stream', 'application/ogg'])
def test_oga_is_audio_without_platform_mime_registration(name, mime, monkeypatch):
    monkeypatch.setattr(transcription.mimetypes, 'guess_type', lambda name: (None, None))
    assert transcription.is_playable({'name': name, 'mime_type': mime})
    assert transcription.is_playable({'path': f'/managed/{name}', 'mime_type': mime})
    assert media._media_mime(name, mime) == 'audio/ogg'


def test_oga_decodes_with_ffmpeg_and_prepares_transcript_before_dispatch(store, tmp_path, monkeypatch):
    import shutil
    import wave
    ffmpeg = shutil.which('ffmpeg')
    if not ffmpeg:
        pytest.skip('ffmpeg unavailable')
    _, chat_id = store
    source = tmp_path / '.decisions/workspaces/projects/7/attachments/VOICE.OGA'
    source.parent.mkdir(parents=True)
    subprocess.run([ffmpeg, '-nostdin', '-y', '-loglevel', 'error', '-f', 'lavfi',
                    '-i', 'sine=frequency=440:sample_rate=48000', '-t', '0.25', '-c:a', 'libopus',
                    '-f', 'ogg', str(source)], check=True, timeout=20)
    original = source.read_bytes()
    record = media._record(source, source.name, 'audio/ogg', 'oga')
    # Reproduce an older manifest that already stored generic MIME metadata.
    record['mime_type'] = 'application/octet-stream'
    media.register_thread_attachments(chat_id, [record])
    calls = []
    def model(wav):
        with wave.open(wav, 'rb') as audio:
            assert audio.getframerate() == 16000
            assert audio.getnchannels() == 1
            assert audio.getsampwidth() == 2
            assert audio.getnframes() > 0
        calls.append(wav)
        return 'Decoded OGA transcript.'
    monkeypatch.setattr('distr.core.audio.voice_cloning.transcribe_audio_file', model)
    monkeypatch.setattr(transcription, '_transcribe', transcription._transcribe_in_process)
    assert transcription.prepare_thread_transcripts(chat_id) == {'ready': 1, 'empty': 0, 'failed': []}
    inputs = media.required_thread_attachments(chat_id)
    assert inputs[0]['mime_type'] == 'audio/ogg'
    assert Path(inputs[1]['path']).read_text() == 'Decoded OGA transcript.'
    assert transcription.prepare_thread_transcripts(chat_id)['ready'] == 1
    assert len(calls) == 1
    assert source.read_bytes() == original
    from distr.gui.web.routes.development.threads import register_routes
    app, router = FastAPI(), APIRouter()
    register_routes(router, None)
    app.include_router(router)
    response = TestClient(app).get(f'/workflows/studio/tasks/{chat_id}/attachments/{record["id"]}')
    assert response.headers['content-type'] == 'audio/ogg'
    assert response.headers['content-disposition'].startswith('inline')
    assert response.content == original
