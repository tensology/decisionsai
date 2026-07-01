"""Stage short-lived public files on www.decisionsai.net for third-party APIs (e.g. Pixazo VoxCPM)."""

from __future__ import annotations

import json
import logging
import mimetypes
import os
import secrets
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from distr.core.integrations.relay_auth import relay_auth_headers, relay_public_base

logger = logging.getLogger(__name__)

RELAY_REFERENCE_META = "relay_reference.json"
# ponytail: Pixazo needs a public URL; relay TTL is 2h — refresh from local wav before expiry.
RELAY_REFRESH_BUFFER_SEC = 300


def _encode_multipart(fields: dict[str, str], files: dict[str, tuple[str, bytes, str]]) -> tuple[bytes, str]:
    boundary = secrets.token_hex(16)
    chunks: list[bytes] = []
    for name, value in fields.items():
        chunks.append(f"--{boundary}\r\n".encode())
        chunks.append(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode())
        chunks.append(f"{value}\r\n".encode())
    for name, (filename, data, mime) in files.items():
        chunks.append(f"--{boundary}\r\n".encode())
        chunks.append(
            f'Content-Disposition: form-data; name="{name}"; filename="{filename}"\r\n'.encode()
        )
        chunks.append(f"Content-Type: {mime}\r\n\r\n".encode())
        chunks.append(data)
        chunks.append(b"\r\n")
    chunks.append(f"--{boundary}--\r\n".encode())
    return b"".join(chunks), boundary


def upload_pixazo_voice_reference(
    local_path: str,
    *,
    label: str = "pixazo-voxcpm",
) -> dict[str, Any]:
    """Upload a reference .wav to the Decisions relay; returns download_url + expires_at."""
    path = Path(local_path)
    if not path.is_file():
        raise FileNotFoundError(f"Reference audio not found: {local_path}")
    if path.suffix.lower() != ".wav":
        raise ValueError("Pixazo VoxCPM reference must be a .wav file")

    content = path.read_bytes()
    max_mb = 25
    if len(content) > max_mb * 1024 * 1024:
        raise ValueError(f"Reference audio exceeds {max_mb} MB")

    mime = mimetypes.guess_type(path.name)[0] or "audio/wav"
    body, boundary = _encode_multipart(
        {"purpose": label},
        {"file": (path.name, content, mime)},
    )
    url = f"{relay_public_base()}/api/media/voice-reference/upload/"
    headers = {
        "Content-Type": f"multipart/form-data; boundary={boundary}",
        "Content-Length": str(len(body)),
    }
    auth = relay_auth_headers(force_refresh=True)
    if not auth:
        raise RuntimeError(
            "Decisions relay auth is not available. "
            "RELAY_INTERNAL_TOKEN in the project .env should match www.decisionsai.net "
            "(or device identity at ~/.decisions/device_identity.json must be registered)."
        )
    headers.update(auth)

    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=60) as response:
            raw = response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace") if exc.fp else str(exc)
        raise RuntimeError(f"Relay upload failed ({exc.code}): {detail}") from exc

    data = json.loads(raw) if raw else {}
    download_url = (data.get("download_url") or "").strip()
    if not download_url:
        raise RuntimeError(f"Relay upload returned no download_url: {data!r}")
    return data


def write_relay_reference_meta(audio_dir: str, record: dict[str, Any]) -> None:
    meta_path = os.path.join(audio_dir, RELAY_REFERENCE_META)
    with open(meta_path, "w", encoding="utf-8") as handle:
        json.dump(record, handle)


def read_relay_reference_meta(audio_dir: str) -> Optional[dict[str, Any]]:
    meta_path = os.path.join(audio_dir or "", RELAY_REFERENCE_META)
    if not os.path.isfile(meta_path):
        return None
    try:
        with open(meta_path, encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _parse_expires_at(value: str) -> Optional[datetime]:
    raw = (value or "").strip()
    if not raw:
        return None
    try:
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        return datetime.fromisoformat(raw)
    except ValueError:
        return None


def ensure_pixazo_reference_url(
    local_wav_path: str,
    audio_dir: str,
    *,
    label: str,
    force_refresh: bool = False,
) -> str:
    """Return a public relay URL, re-uploading when the staged file is missing or near expiry."""
    meta = read_relay_reference_meta(audio_dir)
    now = datetime.now(timezone.utc)
    if meta and not force_refresh:
        expires = _parse_expires_at(str(meta.get("expires_at") or ""))
        download_url = str(meta.get("download_url") or "").strip()
        if download_url and expires and expires > now:
            remaining = (expires - now).total_seconds()
            if remaining > RELAY_REFRESH_BUFFER_SEC:
                return download_url

    record = upload_pixazo_voice_reference(local_wav_path, label=label)
    write_relay_reference_meta(audio_dir, record)
    url = str(record.get("download_url") or "").strip()
    if not url:
        raise RuntimeError("Relay staging failed: no download_url")
    logger.info("Staged Pixazo reference on relay (expires %s)", record.get("expires_at"))
    return url
