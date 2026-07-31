"""Pixazo gateway client — submit async jobs and poll for media URLs."""

from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.request
from typing import Any, Optional

logger = logging.getLogger(__name__)

GATEWAY_BASE = "https://gateway.pixazo.ai"
STATUS_URL = f"{GATEWAY_BASE}/v2/requests/status"
GENERIC_GENERATE_URL = f"{GATEWAY_BASE}/v1/generate"

# ponytail: static catalog; upgrade path is Pixazo catalog API when they ship a list endpoint.
PIXAZO_MODELS: list[dict[str, Any]] = [
    {
        "id": "flux-pro",
        "name": "Flux Pro",
        "modality": "image",
        "output_modalities": ["image"],
        "submit_url": GENERIC_GENERATE_URL,
        "body_style": "generic",
    },
    {
        "id": "sdxl-turbo",
        "name": "SDXL Turbo",
        "modality": "image",
        "output_modalities": ["image"],
        "submit_url": f"{GATEWAY_BASE}/sdxlTurbo/v2/getData",
        "body_style": "prompt_only",
    },
    {
        "id": "sdxl-base",
        "name": "SDXL Base 1.0 (free)",
        "modality": "image",
        "output_modalities": ["image"],
        "submit_url": f"{GATEWAY_BASE}/sdxl/v2/getData",
        "body_style": "prompt_only",
    },
    {
        "id": "gpt-image-2",
        "name": "GPT Image 2",
        "modality": "image",
        "output_modalities": ["image"],
        "submit_url": GENERIC_GENERATE_URL,
        "body_style": "generic",
    },
    {
        "id": "nano-banana-2",
        "name": "Nano Banana 2",
        "modality": "image",
        "output_modalities": ["image"],
        "submit_url": GENERIC_GENERATE_URL,
        "body_style": "generic",
    },
    {
        "id": "seedance-2",
        "name": "Seedance 2",
        "modality": "video",
        "output_modalities": ["video"],
        "submit_url": GENERIC_GENERATE_URL,
        "body_style": "generic",
    },
    {
        "id": "veo-3.1",
        "name": "Veo 3.1",
        "modality": "video",
        "output_modalities": ["video"],
        "submit_url": GENERIC_GENERATE_URL,
        "body_style": "generic",
    },
    {
        "id": "sora-2",
        "name": "Sora 2",
        "modality": "video",
        "output_modalities": ["video"],
        "submit_url": GENERIC_GENERATE_URL,
        "body_style": "generic",
    },
    {
        "id": "elevenlabs-tts",
        "name": "ElevenLabs TTS",
        "modality": "audio",
        "output_modalities": ["audio"],
        "submit_url": GENERIC_GENERATE_URL,
        "body_style": "generic",
    },
    {
        "id": "vibevoice",
        "name": "VibeVoice TTS",
        "modality": "audio",
        "output_modalities": ["audio"],
        "submit_url": GENERIC_GENERATE_URL,
        "body_style": "generic",
    },
    {
        "id": "voxcpm",
        "name": "VoxCPM 2 (free)",
        "modality": "audio",
        "output_modalities": ["audio"],
        "submit_url": f"{GATEWAY_BASE}/voxcpm/v1/text-to-speech",
        "body_style": "voxcpm_tts",
    },
]

VOXCPM_TTS_URL = f"{GATEWAY_BASE}/voxcpm/v1/text-to-speech"
VOXCPM_CLONE_URL = f"{GATEWAY_BASE}/voxcpm/v1/voice-cloning"
VOXCPM_DIT_STEPS_MIN = 4
VOXCPM_DIT_STEPS_MAX = 30
VOXCPM_DIT_STEPS_DEFAULT = 6
VOXCPM_REQUEST_TIMEOUT_SEC = 300


def pixazo_dit_steps_from_settings(settings: dict | None = None) -> int:
    """Diffusion steps for Pixazo VoxCPM TTS — lower is faster (VoxCPM docs: 4–30)."""
    try:
        steps = int((settings or {}).get("pixazo_dit_steps") or VOXCPM_DIT_STEPS_DEFAULT)
    except (TypeError, ValueError):
        steps = VOXCPM_DIT_STEPS_DEFAULT
    return max(VOXCPM_DIT_STEPS_MIN, min(VOXCPM_DIT_STEPS_MAX, steps))


def pixazo_models_for_modality(modality: str | None = None) -> list[dict[str, Any]]:
    """Return catalog entries, optionally filtered by image|video|audio."""
    if not modality:
        return [dict(m) for m in PIXAZO_MODELS]
    key = modality.strip().lower()
    return [dict(m) for m in PIXAZO_MODELS if m.get("modality") == key]


def pixazo_model_spec(model_id: str) -> Optional[dict[str, Any]]:
    mid = (model_id or "").strip().lower()
    for entry in PIXAZO_MODELS:
        if entry.get("id", "").lower() == mid:
            return dict(entry)
    return None


def _auth_headers(api_key: str) -> dict[str, str]:
    key = (api_key or "").strip()
    return {
        "Ocp-Apim-Subscription-Key": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "Cache-Control": "no-cache",
    }


def _build_submit_body(spec: dict[str, Any], model_id: str, prompt: str, extra: dict[str, Any]) -> dict[str, Any]:
    style = spec.get("body_style") or "generic"
    if style == "prompt_only":
        body = {"prompt": prompt}
    else:
        body = {"model": model_id, "prompt": prompt}
    for key, value in (extra or {}).items():
        if value is not None:
            body[key] = value
    return body


def pixazo_submit(
    api_key: str,
    model_id: str,
    prompt: str,
    *,
    extra: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Submit a generation job. Returns parsed JSON (includes request_id)."""
    spec = pixazo_model_spec(model_id) or {
        "submit_url": GENERIC_GENERATE_URL,
        "body_style": "generic",
    }
    url = spec.get("submit_url") or GENERIC_GENERATE_URL
    body = _build_submit_body(spec, model_id, prompt, extra or {})
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=_auth_headers(api_key), method="POST")
    with urllib.request.urlopen(req, timeout=60) as response:
        raw = response.read().decode("utf-8", errors="replace")
    return json.loads(raw) if raw else {}


def pixazo_poll_status(api_key: str, request_id: str) -> dict[str, Any]:
    """Poll universal status endpoint for a request_id."""
    rid = (request_id or "").strip()
    url = f"{STATUS_URL}/{rid}"
    req = urllib.request.Request(url, headers=_auth_headers(api_key), method="GET")
    with urllib.request.urlopen(req, timeout=30) as response:
        raw = response.read().decode("utf-8", errors="replace")
    return json.loads(raw) if raw else {}


def pixazo_wait_for_media(
    api_key: str,
    request_id: str,
    *,
    timeout_sec: float = 300,
    poll_sec: float = 5,
) -> list[str]:
    """Poll until COMPLETED or raise on failure/timeout. Returns media URLs."""
    deadline = time.time() + timeout_sec
    last_status = ""
    while time.time() < deadline:
        try:
            data = pixazo_poll_status(api_key, request_id)
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 403):
                raise RuntimeError("Pixazo API key rejected") from exc
            raise
        status = str(data.get("status") or "").upper()
        last_status = status or last_status
        if status == "COMPLETED":
            output = data.get("output") or {}
            urls = output.get("media_url") or output.get("media_urls") or []
            if isinstance(urls, str):
                urls = [urls]
            clean = [u for u in urls if u]
            if clean:
                return clean
            raise RuntimeError("Pixazo job completed but returned no media_url")
        if status in {"FAILED", "ERROR"}:
            err = data.get("error") or "Pixazo generation failed"
            raise RuntimeError(str(err))
        time.sleep(poll_sec)
    raise TimeoutError(f"Pixazo job timed out (last status: {last_status or 'unknown'})")


def pixazo_generate_media_urls(
    api_key: str,
    model_id: str,
    prompt: str,
    *,
    extra: Optional[dict[str, Any]] = None,
    timeout_sec: float = 300,
) -> list[str]:
    """Submit + poll; returns CDN URLs for generated media."""
    submitted = pixazo_submit(api_key, model_id, prompt, extra=extra)
    request_id = submitted.get("request_id") or submitted.get("job_id")
    if not request_id:
        raise RuntimeError(f"Pixazo submit returned no request_id: {submitted!r}")
    return pixazo_wait_for_media(api_key, str(request_id), timeout_sec=timeout_sec)


def download_url_to_bytes(url: str, timeout: int = 120) -> bytes:
    """Download media from Pixazo CDN URL."""
    from distr.gui.web.security import validate_safe_outbound_url

    safe_url = validate_safe_outbound_url(url)
    # ponytail: Pixazo R2 is behind Cloudflare; default Python urllib UA gets 403 (error 1010).
    req = urllib.request.Request(
        safe_url,
        method="GET",
        headers={
            "User-Agent": (
                "Mozilla/5.0 (compatible; DecisionsAI/1.0; "
                "+https://www.decisionsai.net)"
            ),
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return response.read()
    except urllib.error.HTTPError as exc:
        if exc.code == 403:
            raise RuntimeError(
                "Pixazo media download blocked (CDN returned 403). Retry in a moment."
            ) from exc
        raise


def _post_json(
    api_key: str,
    url: str,
    body: dict[str, Any],
    timeout: int = VOXCPM_REQUEST_TIMEOUT_SEC,
) -> dict[str, Any]:
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=_auth_headers(api_key), method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            raw = response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace") if exc.fp else str(exc)
        if exc.code in (401, 403):
            raise RuntimeError("Pixazo API key rejected") from exc
        raise RuntimeError(f"Pixazo request failed ({exc.code}): {detail}") from exc
    return json.loads(raw) if raw else {}


def voxcpm_text_to_speech_url(
    api_key: str,
    text: str,
    *,
    cfg_value: float = 2.0,
    dit_steps: int = VOXCPM_DIT_STEPS_DEFAULT,
) -> str:
    """Sync VoxCPM TTS — returns a CDN URL to a .wav file."""
    steps = max(VOXCPM_DIT_STEPS_MIN, min(VOXCPM_DIT_STEPS_MAX, int(dit_steps)))
    payload = {"text": text, "cfg_value": cfg_value, "dit_steps": steps}
    data = _post_json(api_key, VOXCPM_TTS_URL, payload)
    url = data.get("output") or data.get("audio_url") or data.get("url")
    if not url:
        raise RuntimeError(f"VoxCPM TTS returned no audio URL: {data!r}")
    return str(url)


def voxcpm_voice_cloning_url(
    api_key: str,
    text: str,
    reference_audio_url: str,
    *,
    prompt_text: str | None = None,
    dit_steps: int | None = None,
) -> str:
    """VoxCPM zero-shot clone — reference_audio_url must be publicly reachable by Pixazo."""
    ref = (reference_audio_url or "").strip()
    if not ref:
        raise RuntimeError("reference_audio_url is required for VoxCPM voice cloning")
    payload: dict[str, Any] = {"text": text, "reference_audio_url": ref}
    if prompt_text and str(prompt_text).strip():
        payload["prompt_text"] = str(prompt_text).strip()
    if dit_steps is not None:
        payload["dit_steps"] = max(VOXCPM_DIT_STEPS_MIN, min(VOXCPM_DIT_STEPS_MAX, int(dit_steps)))
    data = _post_json(api_key, VOXCPM_CLONE_URL, payload)
    url = data.get("audio_url") or data.get("url") or data.get("output")
    if not url:
        raise RuntimeError(f"VoxCPM voice cloning returned no audio URL: {data!r}")
    return str(url)


def voxcpm_synthesize_wav_bytes(
    api_key: str,
    text: str,
    *,
    voice_id: str = "voxcpm",
    reference_audio_url: str | None = None,
    prompt_text: str | None = None,
    dit_steps: int = VOXCPM_DIT_STEPS_DEFAULT,
) -> bytes:
    """Generate speech via VoxCPM and return raw WAV bytes."""
    steps = max(VOXCPM_DIT_STEPS_MIN, min(VOXCPM_DIT_STEPS_MAX, int(dit_steps)))
    vid = (voice_id or "voxcpm").strip().lower()
    if vid.startswith("custom_") and reference_audio_url:
        media_url = voxcpm_voice_cloning_url(
            api_key, text, reference_audio_url, prompt_text=prompt_text, dit_steps=steps,
        )
    else:
        media_url = voxcpm_text_to_speech_url(api_key, text, dit_steps=steps)
    return download_url_to_bytes(media_url, timeout=VOXCPM_REQUEST_TIMEOUT_SEC)
