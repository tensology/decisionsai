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


# ---------------------------------------------------------------------------
# Live PCM streaming (audio leaves the desktop while the provider is still
# synthesising; the remote app schedules each chunk on the Web Audio clock).
# ---------------------------------------------------------------------------

REMOTE_PCM_FORMAT = "pcm_s16le"
REMOTE_PCM_FIRST_CHUNK_S = 0.04   # send the first audio as soon as this much exists
REMOTE_PCM_CHUNK_S = 0.12         # then ~120 ms frames (~7 KB base64 at 24 kHz)


def remote_pcm_mime(sample_rate: int, channels: int = 1) -> str:
    return f"audio/pcm; format=s16le; rate={int(sample_rate)}; channels={int(channels)}"


def iter_remote_pcm_messages(
    *,
    request_id: str,
    sample_rate: int,
    chunks: Iterator[bytes],
    first_chunk_s: float = REMOTE_PCM_FIRST_CHUNK_S,
    chunk_s: float = REMOTE_PCM_CHUNK_S,
) -> Iterator[dict]:
    """Yield start / chunk / end messages for a live mono s16le PCM stream.

    The start message is yielded only once the first audio exists, so the remote
    player is never opened for a stream that fails before producing anything.
    Provider chunks are re-framed to a steady size; the first frame is small so
    the first audio is not held back.
    """
    bytes_per_second = int(sample_rate) * 2
    first_bytes = max(2, int(bytes_per_second * first_chunk_s) // 2 * 2)
    frame_bytes = max(2, int(bytes_per_second * chunk_s) // 2 * 2)
    pending = bytearray()
    seq = 0
    sent = 0
    started = False

    def start_message() -> dict:
        return {
            "type": "remote_agent_audio_start",
            "request_id": request_id,
            "data": {
                "mime_type": remote_pcm_mime(sample_rate),
                "format": REMOTE_PCM_FORMAT,
                "sample_rate": int(sample_rate),
                "channels": 1,
                "streaming": True,
                "size_bytes": 0,
            },
        }

    def chunk_message(payload: bytes) -> dict:
        nonlocal seq, sent
        message = {
            "type": "remote_agent_audio_chunk",
            "request_id": request_id,
            "data": {
                "seq": seq,
                "data": base64.b64encode(payload).decode("ascii"),
                "size_bytes": len(payload),
            },
        }
        seq += 1
        sent += len(payload)
        return message

    for piece in chunks:
        if not piece:
            continue
        pending.extend(piece)
        while True:
            target = first_bytes if seq == 0 else frame_bytes
            if len(pending) < target:
                break
            payload = bytes(pending[:target])
            del pending[:target]
            if not started:
                started = True
                yield start_message()
            yield chunk_message(payload)
    if pending:
        usable = len(pending) - (len(pending) % 2)
        if usable:
            if not started:
                started = True
                yield start_message()
            yield chunk_message(bytes(pending[:usable]))
    if not started:
        raise ValueError("TTS stream produced no audio")
    yield {
        "type": "remote_agent_audio_end",
        "request_id": request_id,
        "data": {"chunks": seq, "size_bytes": sent, "format": REMOTE_PCM_FORMAT, "sample_rate": int(sample_rate)},
    }
