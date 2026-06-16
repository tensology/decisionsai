"""Remote web audio streaming payload helpers."""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Iterator


REMOTE_AUDIO_CHUNK_SIZE = 64 * 1024
REMOTE_AUDIO_MIME = "audio/ogg; codecs=opus"


def iter_remote_audio_stream_messages(
    *,
    request_id: str,
    audio_path: str | Path,
    mime_type: str = REMOTE_AUDIO_MIME,
    chunk_size: int = REMOTE_AUDIO_CHUNK_SIZE,
) -> Iterator[dict]:
    """Yield WebSocket messages for one compressed remote audio stream."""
    path = Path(audio_path)
    size = path.stat().st_size
    filename = path.name
    yield {
        "type": "remote_agent_audio_start",
        "request_id": request_id,
        "data": {
            "mime_type": mime_type,
            "filename": filename,
            "size_bytes": size,
        },
    }

    seq = 0
    sent = 0
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(chunk_size)
            if not chunk:
                break
            sent += len(chunk)
            yield {
                "type": "remote_agent_audio_chunk",
                "request_id": request_id,
                "data": {
                    "seq": seq,
                    "data": base64.b64encode(chunk).decode("ascii"),
                    "size_bytes": len(chunk),
                },
            }
            seq += 1

    yield {
        "type": "remote_agent_audio_end",
        "request_id": request_id,
        "data": {
            "chunks": seq,
            "size_bytes": sent,
            "filename": filename,
        },
    }


def remote_audio_stopped_message(request_id: str, reason: str = "stopped") -> dict:
    return {
        "type": "remote_agent_audio_stopped",
        "request_id": request_id,
        "data": {"reason": reason},
    }
