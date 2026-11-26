"""Visual tier: periodic multi-screen capture, summarised to text for the assessor.

Opt-in only (``initiative_awareness_depth == "visual"``). Deliberately not a
rolling visual record of the desk:

- captures are written to a dedicated directory, summarised, then **deleted
  immediately** — the assessor sees a description, not the pixels;
- anything left behind by a crash is purged after ``MAX_CAPTURE_AGE_S``;
- the whole module is a no-op unless the setting says otherwise.

Everything fails to empty: no capture, no vision model, or any error means the
assessor simply gets no visual context, never a wrong one.
"""

from __future__ import annotations

import glob
import logging
import os
import time
from typing import Any

logger = logging.getLogger(__name__)

DEPTH_VISUAL = "visual"
CAPTURE_DIR_NAME = "initiative_visual"
DEFAULT_INTERVAL_S = 900
MAX_CAPTURE_AGE_S = 3600.0
MAX_IMAGES = 4
_PROMPT = (
    "These are screenshots of the operator's monitors. In at most three short "
    "sentences, describe what they are working on and what is on screen. Name the "
    "apps and the concrete subject (ticket, file, document, page). Do not speculate "
    "about their feelings, do not give advice, and do not mention screenshots or "
    "that you are an AI. If the images are unreadable, reply with an empty string."
)


def is_enabled(settings: dict[str, Any] | None) -> bool:
    """True only when the operator explicitly selected the visual depth."""
    try:
        depth = str((settings or {}).get("initiative_awareness_depth") or "").strip().lower()
    except Exception:
        return False
    return depth == DEPTH_VISUAL


def capture_interval_s(settings: dict[str, Any] | None) -> int:
    try:
        value = int((settings or {}).get("initiative_visual_capture_seconds", DEFAULT_INTERVAL_S))
    except (TypeError, ValueError):
        value = DEFAULT_INTERVAL_S
    return max(60, value)


def capture_dir() -> str:
    from distr.core.paths import DB_DIR

    return os.path.join(DB_DIR, CAPTURE_DIR_NAME)


def _stamp_path() -> str:
    return os.path.join(capture_dir(), ".last_capture")


def due(settings: dict[str, Any] | None, *, now: float | None = None) -> bool:
    """True when enough time has passed since the last capture (or there was none)."""
    now = time.time() if now is None else now
    try:
        with open(_stamp_path(), "r", encoding="utf-8") as fh:
            last = float(fh.read().strip())
    except Exception:
        return True
    return (now - last) >= capture_interval_s(settings)


def _mark_captured(*, now: float | None = None) -> None:
    now = time.time() if now is None else now
    try:
        os.makedirs(capture_dir(), exist_ok=True)
        with open(_stamp_path(), "w", encoding="utf-8") as fh:
            fh.write(str(now))
    except Exception:
        logger.debug("visual_context: could not write capture stamp", exc_info=True)


def purge_captures(*, keep_s: float = MAX_CAPTURE_AGE_S, now: float | None = None) -> int:
    """Delete capture images older than ``keep_s``. Returns files removed."""
    now = time.time() if now is None else now
    removed = 0
    for path in glob.glob(os.path.join(capture_dir(), "*.png")):
        try:
            if (now - os.path.getmtime(path)) > keep_s:
                os.remove(path)
                removed += 1
        except OSError:
            continue
    return removed


def _describe(paths: list[str], settings: dict[str, Any]) -> str:
    """Summarise captures with the configured vision model. "" on any failure."""
    from distr.core.agent.tools.vision.screen_capture import image_to_base64
    from distr.core.agent.tools.vision.vision_api import resolve_vision_llm_config

    provider, model = resolve_vision_llm_config(settings)
    if not provider or not model:
        logger.info("visual_context: no vision model configured; skipping visual context")
        return ""

    images: list[str] = []
    mimes: list[str] = []
    for path in paths[:MAX_IMAGES]:
        try:
            b64, mime = image_to_base64(path)
        except Exception:
            continue
        if b64:
            images.append(b64)
            mimes.append(mime or "image/png")
    if not images:
        return ""

    try:
        from distr.core.agent.tools.vision.screenshot_analyzer import ScreenshotAnalyzerTool

        reply = ScreenshotAnalyzerTool._call_vision_api(
            provider, model, images, _PROMPT, False, image_mimes=mimes,
        )
    except Exception:
        logger.debug("visual_context: vision call failed", exc_info=True)
        return ""

    text = " ".join(str(reply or "").split())
    if not text or text.startswith("Error"):
        return ""
    return text[:600]


def capture_and_describe(settings: dict[str, Any] | None) -> str:
    """Capture every screen, summarise, and delete the images. "" when disabled/failed."""
    if not is_enabled(settings):
        return ""

    from distr.core.agent.tools.vision.screen_capture import capture_all_screens

    purge_captures()
    directory = capture_dir()
    try:
        os.makedirs(directory, exist_ok=True)
        paths = [p for p in (capture_all_screens(directory) or []) if p and os.path.exists(p)]
    except Exception:
        logger.debug("visual_context: capture failed", exc_info=True)
        paths = []
    if not paths:
        return ""

    _mark_captured()
    try:
        return _describe(paths, settings or {})
    finally:
        # The description is the artefact. The pixels are not kept.
        for path in paths:
            try:
                os.remove(path)
            except OSError:
                pass


def visual_digest_line(settings: dict[str, Any] | None) -> str:
    """One-line fragment for the situation digest, or "" when there is nothing."""
    text = capture_and_describe(settings)
    return f"on screen: {text}" if text else ""
