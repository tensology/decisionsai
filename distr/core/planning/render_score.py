"""How closely a generated page matches the real one.

Two reads of each page (headless Firefox, same viewports), reported separately so a good number on one cannot hide a bad one:

- ``boxes``      overlap of where content sits (text, images, fields, buttons, filled bars) on a 20px grid, from the
                 computed boxes of both pages. This is the layout score.
- ``structure``  correlation of blurred greyscale thumbnails of the two screenshots: whole-page tone and composition.
- ``height``     the shorter page's height over the taller one's.
- ``ink``        overlap of where the two screenshots have ink (anything off the page's own background) on the same
                 grid; ``ink_near`` allows 20px of slack and is reported only. It is the one measure that also works on a stencil.
- ``colour``     mean colour distance on a coarse grid. Reported, not part of ``layout``: colour belongs to the base template.

``layout`` = mean(boxes, ink, structure, height); for a drawn stencil, mean(ink, structure, height). All values are 0..1.
"""
from __future__ import annotations

from typing import Any

import numpy as np
from PIL import Image, ImageFilter

CELL = 20


def _content_boxes(read: dict[str, Any], stencil: bool = False) -> list[tuple[float, float, float, float]]:
    """Where content sits. In a drawn stencil the content is the grey bars and crossed boxes (``<i>`` and gradient fills)."""
    out = []
    for r in (read or {}).get("records") or []:
        x, y, w, h = r.get("r") or (0, 0, 0, 0)
        if w < 4 or h < 4 or r.get("t") == "body":
            continue
        if stencil:
            if r.get("t") == "i" or r.get("bi") or r.get("it") or (r.get("t") == "button"):
                out.append((x, y, w, h))
            continue
        leaf = r.get("tb") or r.get("media") or r.get("it") or (r.get("t") in ("button", "a") and r.get("bg"))
        bar = r.get("bg") and w >= 200 and 8 <= h <= 400 and r.get("t") in ("header", "nav", "footer", "section", "div", "main", "aside")
        if leaf or bar:
            out.append((x, y, w, h))
    return out


def _mask(boxes, width: int, height: int):
    cols, rows = max(1, int(np.ceil(width / CELL))), max(1, int(np.ceil(height / CELL)))
    mask = np.zeros((rows, cols), dtype=bool)
    for x, y, w, h in boxes:
        x0, x1 = int(max(0, x) // CELL), int(min(width, x + w) // CELL) + 1
        y0, y1 = int(max(0, y) // CELL), int(min(height, y + h) // CELL) + 1
        mask[y0:y1, x0:x1] = True
    return mask


def boxes_score(real: dict[str, Any], made: dict[str, Any], stencil: bool = False) -> float:
    width = int(max(real.get("pw") or real.get("vw") or 1, made.get("pw") or made.get("vw") or 1))
    height = int(max(real.get("ph") or 1, made.get("ph") or 1))
    a, b = _mask(_content_boxes(real), width, height), _mask(_content_boxes(made, stencil), width, height)
    union = (a | b).sum()
    return float((a & b).sum() / union) if union else 0.0


def _thumb(path: str, width: int = 48) -> np.ndarray:
    image = Image.open(path).convert("L")
    scale = width / image.width
    image = image.filter(ImageFilter.GaussianBlur(radius=max(2, image.width / 160))).resize((width, max(1, int(image.height * scale))), Image.BILINEAR)
    return np.asarray(image, dtype=np.float32)


def _dark(read: dict[str, Any] | None) -> bool | None:
    """Whether the page's own background is dark, from the probe's page colour."""
    bg = (read or {}).get("bg")
    if isinstance(bg, (list, tuple)) and len(bg) >= 3 and (len(bg) < 4 or (bg[3] or 0) > 0.5):
        return (0.2126 * bg[0] + 0.7152 * bg[1] + 0.0722 * bg[2]) < 110
    return None


def structure_score(real_png: str, made_png: str, real_dark: bool | None = None, made_dark: bool | None = None) -> float:
    a, b = _thumb(real_png), _thumb(made_png)
    if a.shape[0] != b.shape[0]:                 # a taller or shorter page is scored by ``height``; here compare composition
        rows_n = min(a.shape[0], b.shape[0])
        pick = lambda m: m[np.linspace(0, m.shape[0] - 1, rows_n).round().astype(int)]
        a, b = pick(a), pick(b)
    rows = a.shape[0]
    real_dark = bool(np.median(a) < 110) if real_dark is None else real_dark
    made_dark = bool(np.median(b) < 110) if made_dark is None else made_dark
    if real_dark != made_dark:                   # one page is dark and the other light: compare composition, not polarity
        a, b = (255 - a, b) if real_dark else (a, 255 - b)
    if rows < 2 or a.std() < 1e-6 or b.std() < 1e-6:
        return 0.0
    return float(max(0.0, np.corrcoef(a.ravel(), b.ravel())[0, 1]))


def colour_score(real_png: str, made_png: str) -> float:
    def grid(path):
        image = Image.open(path).convert("RGB")
        return np.asarray(image.resize((24, max(1, int(image.height * 24 / image.width))), Image.BOX), dtype=np.float32)

    a, b = grid(real_png), grid(made_png)
    rows = min(a.shape[0], b.shape[0])
    return float(max(0.0, 1 - np.abs(a[:rows] - b[:rows]).mean() / 255 * 2))


def _ink(path: str):
    """Which 20px cells of a screenshot hold something: a cell whose tones vary (text, a bar, a border, a picture)
    or a flat fill unlike the page's usual tone (a dark bar, a button)."""
    gray = np.asarray(Image.open(path).convert("L"), dtype=np.int16)
    rows, cols = gray.shape[0] // CELL, gray.shape[1] // CELL
    if not rows or not cols:
        return np.zeros((1, 1), dtype=bool)
    cells = gray[: rows * CELL, : cols * CELL].reshape(rows, CELL, cols, CELL)
    spread = cells.max(axis=(1, 3)) - cells.min(axis=(1, 3))
    level = cells.mean(axis=(1, 3))
    return (spread > 24) | (np.abs(level - np.median(level)) > 40)      # edges and text, and fills unlike the page's usual tone


def _grow(mask):
    out = mask.copy()
    out[1:] |= mask[:-1]; out[:-1] |= mask[1:]; out[:, 1:] |= mask[:, :-1]; out[:, :-1] |= mask[:, 1:]
    return out


def ink_score(real_png: str, made_png: str) -> tuple[float, float]:
    """Overlap of where each screenshot has ink: strict, and allowing one cell (20px) of slack. Works on a stencil and a render alike."""
    a, b = _ink(real_png), _ink(made_png)
    rows, cols = min(a.shape[0], b.shape[0]), min(a.shape[1], b.shape[1])
    a, b = a[:rows, :cols], b[:rows, :cols]
    union = (a | b).sum()
    strict = float((a & b).sum() / union) if union else 0.0
    near = float(((a & _grow(b)).sum() + (b & _grow(a)).sum()) / max(1, a.sum() + b.sum()))
    return strict, near


def score(real_read: dict[str, Any], made_read: dict[str, Any], real_png: str, made_png: str, stencil: bool = False) -> dict[str, float]:
    """``layout`` = mean of ``ink`` (strict), ``structure`` and ``height``, plus ``boxes`` when both sides are real DOM
    (a drawn stencil has no content elements to compare). ``ink_near`` is reported on its own, never averaged in."""
    boxes = None if stencil else boxes_score(real_read, made_read)
    structure = structure_score(real_png, made_png, _dark(real_read), False if stencil else _dark(made_read))
    ink, ink_near = ink_score(real_png, made_png)
    rh, mh = real_read.get("ph") or 1, made_read.get("ph") or 1
    height = float(min(rh, mh) / max(rh, mh))
    parts = [v for v in (boxes, ink, structure, height) if v is not None]
    out = {"ink": round(ink, 3), "ink_near": round(ink_near, 3), "structure": round(structure, 3), "height": round(height, 3),
           "colour": round(colour_score(real_png, made_png), 3), "layout": round(sum(parts) / len(parts), 3)}
    if boxes is not None:
        out = {"boxes": round(boxes, 3), **out}
    return out
