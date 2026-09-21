"""Fish Audio REST client — TTS + voice library. No SDK."""

from __future__ import annotations

import logging
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any
from urllib.parse import urlencode

import httpx
import msgpack
import websockets

from distr.core.agent.services.tts.fishaudio_config import (
    DEFAULT_FISHAUDIO_AGENT,
    DEFAULT_FISHAUDIO_TTS_MODEL,
    DEFAULT_FISHAUDIO_VOICE,
    resolve_fishaudio_tts_model,
)

logger = logging.getLogger(__name__)

FISHAUDIO_API = "https://api.fish.audio"
FISHAUDIO_TTS_LIVE = "wss://api.fish.audio/v1/tts/live"
_TTS_TIMEOUT = 45.0
_LIST_TIMEOUT = 15.0
_CLONE_TIMEOUT = 60.0
_CLONE_AUDIO_EXTS = {".wav", ".mp3", ".m4a", ".ogg", ".flac", ".webm", ".opus"}


def _auth_headers(api_key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {(api_key or '').strip()}"}


def _headers(api_key: str, *, model: str | None = None) -> dict[str, str]:
    headers = {
        **_auth_headers(api_key),
        "Content-Type": "application/json",
    }
    if model:
        headers["model"] = model
    return headers


def _raise_for_status(resp: httpx.Response, action: str) -> None:
    if resp.is_success:
        return
    detail = (resp.text or "").strip()[:240]
    if resp.status_code in (401, 403):
        raise ValueError("Fish Audio rejected the request. Check the API key.")
    if resp.status_code == 402:
        raise ValueError("Fish Audio requires a paid plan for this request.")
    if resp.status_code == 429:
        raise ValueError("Fish Audio is rate limiting requests. Wait a moment and try again.")
    suffix = f": {detail}" if detail else ""
    raise ValueError(f"Fish Audio {action} failed (HTTP {resp.status_code}){suffix}")


def _tts_request(
    api_key: str,
    text: str,
    *,
    reference_id: str = "",
    model: str | None = None,
    speed: float = 1.0,
    audio_format: str = "wav",
    sample_rate: int = 44100,
    latency: str = "normal",
) -> tuple[str, dict[str, str], dict[str, Any]]:
    key = (api_key or "").strip()
    if not key:
        raise ValueError("Fish Audio API key is required")
    spoken = (text or "").strip()
    if not spoken:
        raise ValueError("Fish Audio TTS requires text")
    body: dict[str, Any] = {
        "text": spoken,
        "format": audio_format,
        "normalize": True,
        "latency": latency,
        "prosody": {"speed": max(0.5, min(2.0, float(speed)))},
    }
    if audio_format in {"wav", "pcm"}:
        body["sample_rate"] = int(sample_rate)
    voice = (reference_id or "").strip()
    if voice and voice.lower() not in {"default", "fish", "fish audio"}:
        body["reference_id"] = voice
    return (
        f"{FISHAUDIO_API}/v1/tts",
        _headers(key, model=resolve_fishaudio_tts_model(model)),
        body,
    )


def synthesize_audio(
    api_key: str,
    text: str,
    *,
    reference_id: str = "",
    model: str | None = None,
    speed: float = 1.0,
    audio_format: str = "wav",
    sample_rate: int = 44100,
    latency: str = "normal",
) -> bytes:
    """POST /v1/tts and return raw audio bytes."""
    url, headers, body = _tts_request(
        api_key,
        text,
        reference_id=reference_id,
        model=model,
        speed=speed,
        audio_format=audio_format,
        sample_rate=sample_rate,
        latency=latency,
    )
    with httpx.Client(timeout=_TTS_TIMEOUT) as client:
        resp = client.post(url, headers=headers, json=body)
    _raise_for_status(resp, "TTS")
    if not resp.content:
        raise ValueError("Fish Audio TTS returned no audio")
    return resp.content


async def iter_pcm_audio(
    api_key: str,
    text: str,
    *,
    reference_id: str = "",
    model: str | None = None,
    speed: float = 1.0,
    sample_rate: int = 44100,
    latency: str = "low",
):
    """Yield s16le PCM from the live TTS WebSocket. HTTP chunked still buffers the full clip."""
    _, headers, body = _tts_request(
        api_key,
        text,
        reference_id=reference_id,
        model=model,
        speed=speed,
        audio_format="pcm",
        sample_rate=sample_rate,
        latency=latency,
    )
    spoken = body["text"]
    body["text"] = ""
    extra_headers = {
        "Authorization": headers["Authorization"],
        "model": headers["model"],
    }
    async with websockets.connect(
        FISHAUDIO_TTS_LIVE,
        additional_headers=extra_headers,
        max_size=None,
        open_timeout=_TTS_TIMEOUT,
        close_timeout=5,
    ) as ws:
        await ws.send(msgpack.packb({"event": "start", "request": body}, use_bin_type=True))
        await ws.send(msgpack.packb({"event": "text", "text": spoken}, use_bin_type=True))
        await ws.send(msgpack.packb({"event": "flush"}, use_bin_type=True))
        await ws.send(msgpack.packb({"event": "stop"}, use_bin_type=True))
        try:
            async for raw in ws:
                if not isinstance(raw, (bytes, bytearray, memoryview)):
                    continue
                msg = msgpack.unpackb(raw, raw=False)
                if not isinstance(msg, dict):
                    continue
                event = msg.get("event")
                if event == "audio":
                    audio = msg.get("audio")
                    if isinstance(audio, memoryview):
                        audio = audio.tobytes()
                    elif isinstance(audio, bytearray):
                        audio = bytes(audio)
                    if isinstance(audio, bytes) and audio:
                        yield audio
                elif event == "finish":
                    if msg.get("reason") == "error":
                        raise ValueError(str(msg.get("message") or "Fish Audio live TTS failed"))
                    break
                elif event == "error":
                    detail = {k: msg.get(k) for k in ("message", "code", "reason", "status") if msg.get(k) is not None}
                    raise ValueError(str(detail or "Fish Audio live TTS failed"))
        except websockets.exceptions.ConnectionClosed:
            return


def clone_audio_paths(audio_files: list[str]) -> list[str]:
    """Drop sidecar json; keep Fish-supported sample clips."""
    out = []
    for path in audio_files or []:
        ext = os.path.splitext(path)[1].lower()
        if ext in _CLONE_AUDIO_EXTS and os.path.isfile(path):
            out.append(path)
    return out


def create_voice_model(
    api_key: str,
    title: str,
    audio_paths: list[str],
    *,
    description: str = "",
    visibility: str = "private",
) -> dict[str, Any]:
    """POST /model — persistent studio clone. Returns the JSON model payload."""
    key = (api_key or "").strip()
    if not key:
        raise ValueError("Fish Audio API key is required")
    clips = clone_audio_paths(audio_paths)
    if not clips:
        raise ValueError("Fish Audio cloning needs at least one audio sample")
    name = (title or "").strip() or "Custom Voice"
    data = {
        "type": "tts",
        "title": name,
        "train_mode": "fast",
        "visibility": visibility or "private",
    }
    if (description or "").strip():
        data["description"] = description.strip()
    opened = []
    files = []
    try:
        for path in clips[:20]:
            fh = open(path, "rb")
            opened.append(fh)
            files.append(("voices", (os.path.basename(path), fh, "application/octet-stream")))
        with httpx.Client(timeout=_CLONE_TIMEOUT) as client:
            resp = client.post(
                f"{FISHAUDIO_API}/model",
                headers=_auth_headers(key),
                data=data,
                files=files,
            )
        _raise_for_status(resp, "voice clone")
        payload = resp.json() if resp.content else {}
        if not isinstance(payload, dict):
            payload = {}
        vid = str(payload.get("_id") or payload.get("id") or "").strip()
        if not vid:
            raise ValueError("Fish Audio clone returned no voice id")
        payload["_id"] = vid
        return payload
    finally:
        for fh in opened:
            fh.close()


def delete_voice_model(api_key: str, voice_id: str) -> None:
    """DELETE /model/{id}. Missing models are ignored."""
    key = (api_key or "").strip()
    vid = (voice_id or "").strip()
    if not key or not vid:
        return
    with httpx.Client(timeout=_LIST_TIMEOUT) as client:
        resp = client.delete(
            f"{FISHAUDIO_API}/model/{vid}",
            headers=_auth_headers(key),
        )
    if resp.status_code in (404, 405):
        return
    _raise_for_status(resp, "voice delete")


def _list_models(api_key: str, params: dict[str, Any]) -> list[dict]:
    query = urlencode(params, doseq=True)
    url = f"{FISHAUDIO_API}/model?{query}"
    with httpx.Client(timeout=_LIST_TIMEOUT) as client:
        resp = client.get(url, headers=_headers(api_key))
    _raise_for_status(resp, "voice list")
    payload = resp.json() if resp.content else {}
    items = payload.get("items") if isinstance(payload, dict) else None
    if not isinstance(items, list):
        return []
    voices = []
    for item in items:
        if not isinstance(item, dict):
            continue
        vid = str(item.get("_id") or item.get("id") or "").strip()
        title = str(item.get("title") or vid).strip()
        if not vid:
            continue
        languages = item.get("languages") if isinstance(item.get("languages"), list) else []
        voices.append({
            "id": vid,
            "name": title,
            "owned": bool(params.get("self")),
            "languages": [str(x) for x in languages if x],
        })
    return voices


def _voice_label(entry: dict) -> str:
    name = (entry.get("name") or entry.get("id") or "").strip()
    langs = [str(x).lower() for x in (entry.get("languages") or []) if x]
    if langs and langs != ["en"]:
        return f"{name} ({', '.join(langs)})"
    return name


def _extend_unique(out: list[dict], seen: set[str], entries: list[dict], *, custom: bool = False) -> None:
    for entry in entries:
        vid = entry["id"]
        if vid in seen:
            continue
        seen.add(vid)
        row = {
            "id": vid,
            "name": _voice_label(entry),
            "provider_voice_id": vid,
        }
        if custom:
            row["name"] = f"⭐ {_voice_label(entry)}"
            row["custom"] = True
            row["custom_source"] = "fishaudio_api"
        out.append(row)


def list_voices(api_key: str) -> list[dict]:
    """Owned clones, then English/public library, then licensed packs."""
    key = (api_key or "").strip()
    if not key:
        return []
    queries = (
        ("owned", {"self": "true", "page_size": "50"}),
        ("english", {"language": "en", "page_size": "40", "sort_by": "score"}),
        ("popular", {"page_size": "30", "sort_by": "score"}),
        ("licensed", {"licensed": "true", "page_size": "50", "sort_by": "task_count"}),
    )
    buckets: dict[str, list[dict]] = {name: [] for name, _ in queries}

    def _fetch(name: str, params: dict[str, Any]) -> tuple[str, list[dict]]:
        try:
            return name, _list_models(key, params)
        except Exception as exc:
            logger.warning("Fish Audio %s-voice list failed: %s", name, exc)
            return name, []

    # ponytail: four independent GETs; ThreadPoolExecutor, asyncio if this is ever called from a running loop
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(_fetch, name, params) for name, params in queries]
        for fut in as_completed(futures):
            name, rows = fut.result()
            buckets[name] = rows
    seen: set[str] = set()
    out: list[dict] = []
    _extend_unique(out, seen, buckets["owned"], custom=True)
    _extend_unique(out, seen, buckets["english"])
    _extend_unique(out, seen, buckets["popular"])
    _extend_unique(out, seen, buckets["licensed"])
    if DEFAULT_FISHAUDIO_VOICE not in seen:
        out.insert(0, {"id": DEFAULT_FISHAUDIO_VOICE, "name": DEFAULT_FISHAUDIO_AGENT})
    return out


def probe_api_key(api_key: str) -> tuple[bool, str]:
    """Auth-only probe for Settings → API Keys validate."""
    key = (api_key or "").strip()
    if not key:
        return False, "API key is required"
    try:
        with httpx.Client(timeout=_LIST_TIMEOUT) as client:
            resp = client.get(
                f"{FISHAUDIO_API}/model?page_size=1",
                headers=_headers(key),
            )
        if resp.status_code in (401, 403):
            return False, "Invalid API key"
        if resp.status_code == 200:
            return True, ""
        if resp.status_code == 429:
            return True, ""
        return False, f"HTTP Error: {resp.status_code}"
    except Exception as exc:
        return False, str(exc)
