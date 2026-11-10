"""On-demand, scoped transcripts for managed thread media."""
from __future__ import annotations

import mimetypes
import json
from contextlib import contextmanager
import os
import re
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading

_transcription_lock = threading.Lock()
TRANSCRIPTION_TIMEOUT_SECONDS = 120
TRANSCRIPTION_LOCK_TIMEOUT_SECONDS = 30


@contextmanager
def _transcription_slot():
    if not _transcription_lock.acquire(timeout=TRANSCRIPTION_LOCK_TIMEOUT_SECONDS):
        raise TimeoutError('Transcription is busy. The original media remains available.')
    try:
        yield
    finally:
        _transcription_lock.release()


def is_playable(record: dict) -> bool:
    mime = str(record.get('mime_type') or '').lower()
    if any(Path(str(record.get(key) or '')).suffix.lower() == '.oga' for key in ('name', 'path')):
        return True
    guessed = mimetypes.guess_type(str(record.get('name') or record.get('path') or ''))[0] or ''
    return any(value.startswith(('audio/', 'video/')) for value in (mime, guessed))


def transcript_path(record: dict) -> Path | None:
    path = Path(str(record.get('path') or '')).resolve()
    roots = [(Path.home() / '.decisions/workspaces/projects').resolve(),
             (Path.home() / '.decisions/workspaces/boards').resolve()]
    if not re.fullmatch(r'[a-f0-9]{64}', str(record.get('sha256') or '')) or not any(path.is_relative_to(root) for root in roots):
        return None
    cache = path.with_name(f"{path.name}.{record['sha256'][:16]}.transcript.txt").resolve()
    return cache if any(cache.is_relative_to(root) for root in roots) else None


def with_transcript(record: dict) -> dict:
    result = dict(record)
    if str(result.get('transcript') or '').strip():
        result['transcript_status'] = 'ready'
        return result
    path = transcript_path(result)
    if path and path.is_file():
        result['transcript'] = path.read_text(encoding='utf-8').strip()
        result['transcript_status'] = 'ready' if result['transcript'] else 'empty'
    return result


def _transcribe_in_process(path: Path) -> str:
    from distr.core.audio.voice_cloning import transcribe_audio_file

    ffmpeg = shutil.which('ffmpeg')
    if not ffmpeg:
        raise RuntimeError('Audio transcription needs ffmpeg to be installed.')
    with tempfile.TemporaryDirectory(prefix='decisions-transcript-') as directory:
        wav = Path(directory) / 'audio.wav'
        try:
            subprocess.run([ffmpeg, '-nostdin', '-y', '-loglevel', 'error', '-i', str(path),
                            '-vn', '-ar', '16000', '-ac', '1', str(wav)],
                           capture_output=True, check=True, timeout=300)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            raise RuntimeError('The audio track could not be prepared for transcription.') from exc
        return str(transcribe_audio_file(str(wav)) or '').strip()


def _transcribe(path: Path) -> str:
    # Native model calls cannot be cancelled safely in a Python thread. Isolate
    # the work so an unavailable/hung decoder has a real end-to-end deadline.
    with tempfile.TemporaryDirectory(prefix='decisions-transcript-worker-') as directory:
        output = Path(directory) / 'result.json'
        try:
            subprocess.run([sys.executable, '-m', 'distr.core.incoming.transcription',
                            str(path), str(output)], check=True,
                           timeout=TRANSCRIPTION_TIMEOUT_SECONDS,
                           cwd=Path(__file__).resolve().parents[3],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            raise RuntimeError('Transcription could not be completed in time. The original media remains available.') from exc
        return str(json.loads(output.read_text(encoding='utf-8'))['transcript']).strip()


def thread_preparation_timeout(attachments: list[dict]) -> int:
    """Allow one bounded preparation attempt per file plus normal thread creation."""
    count = sum(is_playable(item) for item in attachments)
    return 30 + count * (TRANSCRIPTION_TIMEOUT_SECONDS + TRANSCRIPTION_LOCK_TIMEOUT_SECONDS)


def cache_transcript(record: dict, text: str) -> Path:
    """Publish a complete companion without replacing an existing transcript."""
    cache = transcript_path(record)
    if cache is None:
        raise ValueError('The transcript is outside managed attachment storage.')
    with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=cache.parent,
                                     prefix='.transcript-', delete=False) as handle:
        temporary = Path(handle.name)
        handle.write(text)
    try:
        try:
            os.link(temporary, cache)
        except FileExistsError:
            pass
    finally:
        temporary.unlink(missing_ok=True)
    return cache


def prepare_thread_transcripts(chat_id: int) -> dict:
    """Finish available transcripts before first dispatch, without blocking on failures."""
    import logging
    from distr.core.incoming.media import thread_attachments

    report = {'ready': 0, 'empty': 0, 'failed': []}
    for record in thread_attachments(chat_id):
        if not is_playable(record):
            continue
        try:
            result = transcribe_thread_attachment(chat_id, record['id'])
            report[result['status']] += 1
        except Exception:
            # A decoder/model failure must not discard the original or prevent work.
            report['failed'].append({'id': record['id'], 'name': record.get('name') or 'Media'})
            logging.getLogger(__name__).warning('Could not prepare transcript for attachment %s in thread %s; submitting original media.', record.get('id'), chat_id, exc_info=True)
    return report


def transcribe_thread_attachment(chat_id: int, attachment_id: str) -> dict:
    from distr.core.incoming.media import resolve_thread_attachment

    with _transcription_slot():
        path, record = resolve_thread_attachment(chat_id, attachment_id)
        if not is_playable(record):
            raise ValueError('Transcription is available for audio and video files.')
        existing = with_transcript(record)
        if existing.get('transcript_status') in ('ready', 'empty'):
            if existing.get('transcript'):
                cache_transcript(record, existing['transcript'])
            return {'transcript': existing.get('transcript', ''),
                    'status': existing['transcript_status'], 'cached': True}
        text = _transcribe(path)
        # Scope and original-byte integrity must still hold after a long job.
        _, current = resolve_thread_attachment(chat_id, attachment_id)
        if current.get('sha256') != record.get('sha256'):
            raise ValueError('The attachment changed during transcription. Open it again to retry.')
        current = with_transcript(current)
        if current.get('transcript_status') in ('ready', 'empty'):
            return {'transcript': current.get('transcript', ''),
                    'status': current['transcript_status'], 'cached': True}
        cache = cache_transcript(current, text)
        text = cache.read_text(encoding='utf-8').strip()
        return {'transcript': text, 'status': 'ready' if text else 'empty', 'cached': False}


if __name__ == '__main__':
    Path(sys.argv[2]).write_text(json.dumps({'transcript': _transcribe_in_process(Path(sys.argv[1]))}), encoding='utf-8')
