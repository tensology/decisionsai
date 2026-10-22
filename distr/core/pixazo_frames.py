"""Pixazo first/last-frame (start+end image) video generation.

Docs (checked 2026-10-22):
  Seedance 2.5  https://www.pixazo.ai/models/seedance  POST /seedance-2-5/v1/first-last-frame-to-video
                content[] of image_url items (URL or data: URI), role first_frame/last_frame,
                duration 4-30 or -1, ratio "adaptive", resolution 480p/720p.
  Wan 3.0       https://www.pixazo.ai/models/wan  POST /wan-3-0-video/v1/first-last-frame-to-video
                prompt + media[{type: first_frame|last_frame, url}], duration, resolution.
  Veo 3.1 Fast  https://www.pixazo.ai/models/veo  POST /veo31f/v1/veo-3.1-fast/generate
                prompt + image + last_frame (HTTP(S) URLs, same dimensions), duration 4/6/8.
All return request_id/polling_url compatible with pixazo_client.pixazo_wait_for_media.
"""

from __future__ import annotations

import base64
import json
import mimetypes
import os
import urllib.request
from typing import Any, Optional

from distr.core.pixazo_client import GATEWAY_BASE, _auth_headers, pixazo_wait_for_media

FRAME_MODELS: dict[str, dict[str, Any]] = {
    "seedance-2.5-frames": {
        "name": "Seedance 2.5 (first/last frame)",
        "url": f"{GATEWAY_BASE}/seedance-2-5/v1/first-last-frame-to-video",
        "style": "seedance",
        "durations": list(range(4, 31)),
        "local_files": True,  # accepts base64 data URIs (<30 MB each)
    },
    "wan-3.0-frames": {
        "name": "Wan 3.0 (first/last frame)",
        "url": f"{GATEWAY_BASE}/wan-3-0-video/v1/first-last-frame-to-video",
        "style": "wan",
        "durations": list(range(2, 31)),
        "local_files": False,
    },
    "veo-3.1-fast-frames": {
        "name": "Veo 3.1 Fast (first/last frame)",
        "url": f"{GATEWAY_BASE}/veo31f/v1/veo-3.1-fast/generate",
        "style": "veo",
        "durations": [4, 6, 8],
        "local_files": False,
    },
}
DEFAULT_FRAME_MODEL = "seedance-2.5-frames"


def _image_ref(value: str, *, allow_local: bool) -> str:
    v = (value or "").strip()
    if v.startswith(("https://", "data:image/")):
        return v
    if v.startswith("http://"):
        raise ValueError("Pixazo requires HTTPS image URLs")
    if not os.path.isfile(v):
        raise ValueError(f"Frame image not found: {v}")
    if not allow_local:
        raise ValueError("This model needs public HTTPS frame URLs; use seedance-2.5-frames for local files")
    if os.path.getsize(v) > 30 * 1024 * 1024:
        raise ValueError("Frame image exceeds 30 MB")
    mime = mimetypes.guess_type(v)[0] or "image/png"
    with open(v, "rb") as fh:
        return f"data:{mime.lower()};base64,{base64.b64encode(fh.read()).decode()}"


def _clamp_duration(spec: dict[str, Any], duration: Optional[int]) -> Optional[int]:
    if duration is None:
        return None
    allowed = spec["durations"]
    return min(allowed, key=lambda d: abs(d - int(duration)))


def build_frames_body(model: str, prompt: str, start_image: str, end_image: str, duration: Optional[int] = None) -> tuple[str, dict[str, Any]]:
    spec = FRAME_MODELS.get(model)
    if not spec:
        raise ValueError(f"Unknown frame model {model!r}; choose one of {sorted(FRAME_MODELS)}")
    first = _image_ref(start_image, allow_local=spec["local_files"])
    last = _image_ref(end_image, allow_local=spec["local_files"])
    dur = _clamp_duration(spec, duration)
    style = spec["style"]
    if style == "seedance":
        content: list[dict[str, Any]] = []
        if prompt.strip():
            content.append({"type": "text", "text": prompt.strip()})
        content.append({"type": "image_url", "image_url": {"url": first}, "role": "first_frame"})
        content.append({"type": "image_url", "image_url": {"url": last}, "role": "last_frame"})
        body: dict[str, Any] = {"content": content, "ratio": "adaptive"}
    elif style == "wan":
        body = {"prompt": prompt.strip() or "Smooth transition", "ratio": "adaptive",
                "media": [{"type": "first_frame", "url": first}, {"type": "last_frame", "url": last}]}
    else:
        body = {"prompt": prompt.strip() or "Smooth transition", "image": first, "last_frame": last}
    if dur is not None:
        body["duration"] = dur
    return spec["url"], body


def pixazo_frames_to_video_urls(api_key: str, model: str, prompt: str, start_image: str, end_image: str,
                                duration: Optional[int] = None, timeout_sec: float = 600) -> list[str]:
    url, body = build_frames_body(model, prompt, start_image, end_image, duration)
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers=_auth_headers(api_key), method="POST")
    with urllib.request.urlopen(req, timeout=120) as response:
        raw = response.read().decode("utf-8", errors="replace")
    submitted = json.loads(raw) if raw else {}
    rid = submitted.get("request_id") or submitted.get("job_id")
    if not rid:
        raise RuntimeError("Pixazo frames submit returned no request_id")
    return pixazo_wait_for_media(api_key, str(rid), timeout_sec=timeout_sec)
