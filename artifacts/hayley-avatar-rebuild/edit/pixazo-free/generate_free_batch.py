from __future__ import annotations

import base64
import json
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from distr.core.third_party_keys import pixazo_api_key


ROOT = Path(__file__).resolve().parents[2]
SOURCE_DIR = ROOT / "final-images-v2"
OUTPUT_DIR = Path(__file__).resolve().parent / "raw"
SUBMIT_URL = "https://gateway.pixazo.ai/ltx-video/v1/image-to-video"
STATUS_URL = "https://gateway.pixazo.ai/v2/requests/status"

MOTIONS = {
    "dictation": "subtle speech and listening movement with restrained mouth motion",
    "file-drop-success": "a subtle celebratory movement while the check-mark page stays perfectly readable and fixed",
    "hands-free-listening": "gentle breathing, one blink, and a tiny attentive head adjustment while the raised hand stays fixed",
    "idle": "gentle breathing and one soft blink while the heart-hands shape stays locked",
    "idle-extension": (
        "gentle continuous breathing and two soft blinks separated in time while the heart-hands shape stays locked. "
        "Keep the movement subtle and continuous for the entire shot"
    ),
    "ptt-active": "subtle breathing and a small alert expression change while the speaking gesture stays fixed",
    "recording-action": "a tiny recording emphasis movement without altering the pose or adding symbols",
    "running-action": "subtle typing movement only while the laptop stays rigid and undistorted",
    "running-step-runner": "subtle focused movement while the clipboard and icon stay fixed",
    "snippet-copied": "a subtle confirmation movement while the copy icon and thumbs-up stay fixed",
    "thinking": "gentle breathing, one blink, and a tiny eye and head adjustment while the chin-hand pose stays fixed",
    "ticket-dictation": "subtle speaking movement while the card and pointing hand stay fixed",
    "tts-response": "restrained mouth movement and one blink while the conversational pose stays fixed",
    "talking": (
        "continuous rapid speech articulation for the entire shot. Cycle the lips and jaw repeatedly through many "
        "small, distinct talking shapes at a lively conversational pace, including quick closed, narrow, rounded, "
        "and open phoneme positions. Never hold the mouth open and never settle back into a silent smile before the "
        "final frame. Keep the torso, head, shoulders, forearms, wrists, fingers, and heart-hands idle pose stationary "
        "throughout. The hands must not open, separate, gesture, or move toward the viewer. Allow only one soft blink"
    ),
    "headphones-listening": "gentle breathing, one happy blink, and an extremely small rhythmic listening movement while the hand, ear cup, face, and headphones stay fixed",
    "needs-attention": "subtle breathing and a small alert eye movement while the raised palm and fingers stay fixed",
}

SOURCE_STEMS = {
    "idle-extension": "idle",
    "talking": "idle",
}

NUM_FRAMES = {
    "idle-extension": 121,
}


def headers(key: str) -> dict[str, str]:
    return {
        "Ocp-Apim-Subscription-Key": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "Cache-Control": "no-cache",
        "User-Agent": "DecisionsAI/1.0",
    }


def submit(key: str, stem: str, source: Path) -> tuple[str, str]:
    prompt = (
        "Fixed camera. Preserve the exact illustrated Hayley character, face, eyes, long platinum hair, hands, props, "
        "dark clothing, colors, linework, proportions, framing, and silhouette. "
        f"Animate only {MOTIONS[stem]}. Keep all anatomy and props structurally stable. "
        "No zoom, pan, crop, shake, background, text, watermark, new object, new limb, facial drift, hand drift, "
        "clothing drift, or prop drift. Return cleanly toward the starting pose."
    )
    data_url = "data:image/png;base64," + base64.b64encode(source.read_bytes()).decode("ascii")
    num_frames = NUM_FRAMES.get(stem, 73)
    body = {
        "prompt": prompt,
        "image_url": data_url,
        "strength": 1,
        "negative": (
            "camera movement, zoom, pan, crop, shake, background, text, watermark, extra limbs, extra fingers, "
            "deformed hands, morphing, face drift, hair drift, clothing drift, prop drift"
        ),
        "aspect": "1:1",
        "num_frames": num_frames,
        "frame_rate": 24,
        "steps": 8,
        "cfg": 3.0,
    }
    request = urllib.request.Request(
        SUBMIT_URL,
        data=json.dumps(body).encode(),
        headers=headers(key),
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=90) as response:
        result = json.loads(response.read())
    request_id = result.get("request_id") or result.get("id")
    if not request_id:
        raise RuntimeError(f"No request id returned for {stem}: {result}")
    return str(request_id), prompt


def wait_and_download(key: str, stem: str, request_id: str) -> bytes:
    for poll in range(80):
        request = urllib.request.Request(f"{STATUS_URL}/{request_id}", headers=headers(key))
        with urllib.request.urlopen(request, timeout=30) as response:
            result = json.loads(response.read())
        status = str(result.get("status") or "").upper()
        print(f"{stem}: poll {poll + 1}: {status}", flush=True)
        if status == "COMPLETED":
            output = result.get("output") or {}
            urls = output.get("media_url") or output.get("media_urls") or []
            if isinstance(urls, str):
                urls = [urls]
            if not urls:
                raise RuntimeError(f"{stem} completed without a media URL")
            media_request = urllib.request.Request(urls[0], headers=headers(key))
            with urllib.request.urlopen(media_request, timeout=120) as response:
                return response.read()
        if status in {"FAILED", "ERROR"}:
            raise RuntimeError(f"{stem} failed: {result.get('error') or result}")
        time.sleep(5)
    raise TimeoutError(f"{stem} timed out")


def main() -> None:
    key = pixazo_api_key().strip()
    if not key:
        raise RuntimeError("Pixazo API key unavailable")
    requested = {arg for arg in sys.argv[1:] if not arg.startswith("--")}
    force = "--force" in sys.argv[1:]
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    for stem in MOTIONS:
        if requested and stem not in requested:
            continue
        video_path = OUTPUT_DIR / f"{stem}.mp4"
        metadata_path = OUTPUT_DIR / f"{stem}.json"
        if not force and video_path.exists() and metadata_path.exists():
            metadata = json.loads(metadata_path.read_text())
            if metadata.get("schema_version") == 2:
                print(f"{stem}: already complete, skipping", flush=True)
                continue
        source = SOURCE_DIR / f"{SOURCE_STEMS.get(stem, stem)}.png"
        request_id, prompt = submit(key, stem, source)
        print(f"{stem}: submitted {request_id}", flush=True)
        payload = wait_and_download(key, stem, request_id)
        video_path.write_bytes(payload)
        metadata_path.write_text(
            json.dumps(
                {
                    "request_id": request_id,
                    "source": str(source),
                    "prompt": prompt,
                    "model": "ltx-video",
                    "endpoint": "/ltx-video/v1/image-to-video",
                    "free_tier": True,
                    "schema_version": 2,
                    "parameters": {
                        "aspect": "1:1",
                        "num_frames": NUM_FRAMES.get(stem, 73),
                        "frame_rate": 24,
                        "steps": 8,
                        "cfg": 3.0,
                        "strength": 1,
                    },
                    "status": "COMPLETED",
                    "media_bytes": len(payload),
                },
                indent=2,
            )
            + "\n"
        )
        print(f"{stem}: saved {len(payload)} bytes", flush=True)


if __name__ == "__main__":
    main()
