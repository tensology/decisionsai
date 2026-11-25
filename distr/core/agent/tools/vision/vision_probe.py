"""Live vision-capability probe.

Nothing else validates a *typed* cloud model for vision: the capability filter in
the settings UI only covers the Ollama browser list, and ``check_vision_model_support``
is a name regex that passes anything starting ``gpt-5``. So a model that cannot
see images (e.g. a text-only ``gpt-5.x`` variant) can be selected and then fails
at the first screenshot with an opaque refusal.

This probes the real behaviour: send a small solid-colour image and ask the model
to name the colour. A model that cannot see images refuses or answers wrongly.
"""

from __future__ import annotations

import base64
import io
import logging
import random
from typing import Any

logger = logging.getLogger(__name__)

# Colours a vision model reliably names; chosen at random so the answer cannot be
# guessed from the prompt alone.
_PROBE_COLOURS = {
    "red": (220, 40, 40),
    "green": (40, 180, 60),
    "blue": (40, 80, 220),
    "yellow": (240, 210, 40),
}

_PROBE_PROMPT = (
    "The image is a single solid colour. Reply with a JSON object "
    '{"colour": "<colour name>"} naming the colour you see. If you cannot see '
    'an image, reply with the JSON object {"colour": "NO_IMAGE"}.'
)


def _solid_colour_png(rgb: tuple[int, int, int], size: int = 96) -> str:
    """Return a base64 PNG of a solid ``rgb`` square (no Pillow dependency)."""
    import struct
    import zlib

    row = b"\x00" + bytes(rgb) * size
    raw = row * size

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    ihdr = struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0)
    png = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(raw, 9))
        + chunk(b"IEND", b"")
    )
    return base64.b64encode(png).decode("ascii")


def probe_vision_capability(provider: str, model: str) -> dict[str, Any]:
    """Return ``{ok, reason, model, reply}`` for whether ``model`` can see images.

    ``ok`` is True only when the model actually named the colour we sent.
    """
    from distr.core.agent.tools.vision.vision_api import require_vision_model

    provider = (provider or "").strip().lower()
    try:
        model = require_vision_model(provider, model)
    except ValueError as exc:
        return {"ok": False, "reason": str(exc), "model": "", "reply": ""}

    colour, rgb = random.choice(list(_PROBE_COLOURS.items()))
    image_b64 = _solid_colour_png(rgb)

    try:
        from distr.core.agent.tools.vision.screenshot_analyzer import ScreenshotAnalyzerTool

        reply = ScreenshotAnalyzerTool._call_vision_api(
            provider,
            model,
            [image_b64],
            _PROBE_PROMPT,
            False,
            image_mimes=["image/png"],
        )
    except Exception as exc:  # a probe must never raise into the settings request
        logger.warning("Vision probe failed for %s/%s: %s", provider, model, exc)
        return {"ok": False, "reason": f"Probe call failed: {exc}", "model": model, "reply": ""}

    text = " ".join(str(reply or "").split())
    lowered = text.lower()

    if text.startswith("Error:") or text.startswith("Error calling"):
        return {"ok": False, "reason": text, "model": model, "reply": text}
    if "no_image" in lowered:
        return {
            "ok": False,
            "reason": f"'{model}' reported that it cannot see an image.",
            "model": model,
            "reply": text[:240],
        }
    if colour in lowered:
        return {"ok": True, "reason": "", "model": model, "reply": text[:240]}
    return {
        "ok": False,
        "reason": f"'{model}' did not name the colour shown (sent {colour}).",
        "model": model,
        "reply": text[:240],
    }
