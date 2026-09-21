"""Fish Audio REST client — TTS + voice library. No SDK."""

from __future__ import annotations

import logging
import os
from typing import Any
from urllib.parse import urlencode

import httpx

from distr.core.agent.services.tts.fishaudio_config import (
    DEFAULT_FISHAUDIO_AGENT,
    DEFAULT_FISHAUDIO_TTS_MODEL,
    DEFAULT_FISHAUDIO_VOICE,
    resolve_fishaudio_tts_model,
)

logger = logging.getLogger(__name__)

FISHAUDIO_API = "https://api.fish.audio"
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


def synthesize_audio(
    api_key: str,
    text: str,
    *,
    reference_id: str = "",
    model: str | None = None,
    speed: float = 1.0,
    audio_format: str = "wav",
    sample_rate: int = 44100,
    latency: str = "balanced",
) -> bytes:
    """POST /v1/tts and return raw audio bytes."""
    key = (api_key or "").strip()
    if not key:
        raise ValueError("Fish Audio API key is required")
    spoken = (text or "").strip()
    if not spoken:
        raise ValueError("Fish Audio TTS requires text")
    wire_model = resolve_fishaudio_tts_model(model)
    body: dict[str, Any] = {
        "text": spoken,
        "format": audio_format,
        "normalize": True,
        "latency": latency,
        "prosody": {"speed": max(0.5, min(2.0, float(speed)))},
    }
    if audio_format == "wav":
        body["sample_rate"] = int(sample_rate)
    voice = (reference_id or "").strip()
    if voice and voice.lower() not in {"default", "fish", "fish audio"}:
        body["reference_id"] = voice
    with httpx.Client(timeout=_TTS_TIMEOUT) as client:
        resp = client.post(
            f"{FISHAUDIO_API}/v1/tts",
            headers=_headers(key, model=wire_model),
            json=body,
        )
    _raise_for_status(resp, "TTS")
    if not resp.content:
        raise ValueError("Fish Audio TTS returned no audio")
    return resp.content


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
    owned: list[dict] = []
    english: list[dict] = []
    popular: list[dict] = []
    licensed: list[dict] = []
    try:
        owned = _list_models(key, {"self": "true", "page_size": "50"})
    except Exception as exc:
        logger.warning("Fish Audio owned-voice list failed: %s", exc)
    try:
        english = _list_models(key, {"language": "en", "page_size": "40", "sort_by": "score"})
    except Exception as exc:
        logger.warning("Fish Audio English voice list failed: %s", exc)
    try:
        popular = _list_models(key, {"page_size": "30", "sort_by": "score"})
    except Exception as exc:
        logger.warning("Fish Audio popular voice list failed: %s", exc)
    try:
        licensed = _list_models(
            key,
            {"licensed": "true", "page_size": "50", "sort_by": "task_count"},
        )
    except Exception as exc:
        logger.warning("Fish Audio licensed voice list failed: %s", exc)
    seen: set[str] = set()
    out: list[dict] = []
    _extend_unique(out, seen, owned, custom=True)
    _extend_unique(out, seen, english)
    _extend_unique(out, seen, popular)
    _extend_unique(out, seen, licensed)
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
