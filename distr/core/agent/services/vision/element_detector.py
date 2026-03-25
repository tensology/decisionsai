"""
UI Element Detector using OpenCV contour analysis.

Detects interactive UI elements (buttons, inputs, icons, panels) from screenshots
by analyzing visual structure: edges, contours, and rectangular regions.

This complements pytesseract OCR (text-only) by finding non-text elements
and providing bounding boxes + coordinates for all detected UI regions.

Used by the screenshot analyzer to enrich vision LLM prompts with structured
element data, and to resolve positional references like "the button at the
bottom right" without relying solely on the LLM guessing coordinates.
"""

import logging
from typing import List, Dict, Any, Optional, Tuple

logger = logging.getLogger(__name__)

_cv2_available: Optional[bool] = None


def _check_cv2() -> bool:
    global _cv2_available
    if _cv2_available is not None:
        return _cv2_available
    try:
        import cv2  # noqa: F401
        _cv2_available = True
    except ImportError:
        logger.debug("opencv-python not available — element detection disabled")
        _cv2_available = False
    return _cv2_available


def detect_elements(
    image_path: str,
    min_area: int = 400,
    max_area_ratio: float = 0.5,
    merge_distance: int = 10,
) -> List[Dict[str, Any]]:
    """
    Detect UI elements in a screenshot using edge/contour analysis.

    Returns a list of element dicts sorted top-to-bottom, left-to-right:
        {
            "id": int,
            "x": int,          # center x
            "y": int,          # center y
            "left": int,
            "top": int,
            "width": int,
            "height": int,
            "area": int,
            "aspect_ratio": float,
            "region": str,     # "top-left", "center", "bottom-right", etc.
            "kind": str,       # heuristic: "button", "icon", "input", "panel", "element"
        }
    """
    if not _check_cv2():
        return []

    try:
        import cv2
        import numpy as np

        img = cv2.imread(image_path)
        if img is None:
            logger.warning("element_detector: could not read image %s", image_path)
            return []

        img_h, img_w = img.shape[:2]
        img_area = img_h * img_w

        # Convert to grayscale and apply adaptive threshold for edge detection
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        # Bilateral filter preserves edges while reducing noise
        gray = cv2.bilateralFilter(gray, 9, 75, 75)

        # Canny edge detection
        edges = cv2.Canny(gray, 50, 150)

        # Dilate edges to close small gaps in element borders
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
        edges = cv2.dilate(edges, kernel, iterations=1)

        # Find contours
        contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        raw_boxes: List[Tuple[int, int, int, int]] = []
        for cnt in contours:
            x, y, w, h = cv2.boundingRect(cnt)
            area = w * h
            if area < min_area:
                continue
            if area > img_area * max_area_ratio:
                continue
            # Filter out very thin lines
            if w < 8 or h < 8:
                continue
            raw_boxes.append((x, y, w, h))

        # Merge overlapping / nearby boxes
        merged = _merge_boxes(raw_boxes, merge_distance)

        elements: List[Dict[str, Any]] = []
        for idx, (x, y, w, h) in enumerate(merged):
            cx = x + w // 2
            cy = y + h // 2
            ar = round(w / max(h, 1), 2)
            elements.append({
                "id": idx,
                "x": cx,
                "y": cy,
                "left": x,
                "top": y,
                "width": w,
                "height": h,
                "area": w * h,
                "aspect_ratio": ar,
                "region": _classify_region(cx, cy, img_w, img_h),
                "kind": _classify_kind(w, h, ar),
            })

        # Sort top-to-bottom, left-to-right
        elements.sort(key=lambda e: (e["top"], e["left"]))
        # Re-index after sort
        for i, el in enumerate(elements):
            el["id"] = i

        logger.info("element_detector: found %d elements in %s", len(elements), image_path)
        return elements

    except Exception as e:
        logger.warning("element_detector failed: %s", e)
        return []


def _merge_boxes(
    boxes: List[Tuple[int, int, int, int]],
    distance: int,
) -> List[Tuple[int, int, int, int]]:
    """Merge overlapping or nearby bounding boxes using union-find."""
    if not boxes:
        return []

    n = len(boxes)
    parent = list(range(n))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(i: int, j: int) -> None:
        ri, rj = find(i), find(j)
        if ri != rj:
            parent[ri] = rj

    for i in range(n):
        x1, y1, w1, h1 = boxes[i]
        for j in range(i + 1, n):
            x2, y2, w2, h2 = boxes[j]
            # Check if boxes overlap or are within merge_distance
            if (
                x1 - distance <= x2 + w2
                and x2 - distance <= x1 + w1
                and y1 - distance <= y2 + h2
                and y2 - distance <= y1 + h1
            ):
                union(i, j)

    groups: Dict[int, List[int]] = {}
    for i in range(n):
        root = find(i)
        groups.setdefault(root, []).append(i)

    merged: List[Tuple[int, int, int, int]] = []
    for indices in groups.values():
        min_x = min(boxes[i][0] for i in indices)
        min_y = min(boxes[i][1] for i in indices)
        max_x = max(boxes[i][0] + boxes[i][2] for i in indices)
        max_y = max(boxes[i][1] + boxes[i][3] for i in indices)
        merged.append((min_x, min_y, max_x - min_x, max_y - min_y))

    return merged


def _classify_region(cx: int, cy: int, img_w: int, img_h: int) -> str:
    """Classify center point into a screen region label."""
    col = "left" if cx < img_w / 3 else ("right" if cx > img_w * 2 / 3 else "center")
    row = "top" if cy < img_h / 3 else ("bottom" if cy > img_h * 2 / 3 else "middle")
    if row == "middle" and col == "center":
        return "center"
    if row == "middle":
        return col
    if col == "center":
        return row
    return f"{row}-{col}"


def _classify_kind(w: int, h: int, aspect_ratio: float) -> str:
    """Heuristic classification of element type based on dimensions."""
    area = w * h
    if area < 2500 and 0.6 < aspect_ratio < 1.6:
        return "icon"
    if 2.0 < aspect_ratio < 12.0 and h < 60:
        return "button"
    if 2.5 < aspect_ratio < 15.0 and 20 < h < 50:
        return "input"
    if area > 50000:
        return "panel"
    if 0.7 < aspect_ratio < 1.5 and area < 10000:
        return "icon"
    if aspect_ratio > 1.5 and h < 80:
        return "button"
    return "element"


def _is_text_search(description: str) -> bool:
    """Return True if the description looks like a search for specific text content
    rather than a positional/kind reference (e.g. 'offline by default' vs 'button at bottom right')."""
    import re
    desc = description.lower().strip()
    # If it contains positional or kind keywords AS WHOLE WORDS, it's structural.
    # Use word boundaries to avoid false positives like "top" in "autopilot".
    structural_words = [
        "top", "bottom", "left", "right", "upper", "lower", "center", "middle",
        "button", "btn", "icon", "input", "field", "panel", "window", "section",
        "search bar", "text box", "textbox", "pane", "card", "above", "below",
        "toggle", "switch", "slider", "checkbox", "menu", "tab", "toolbar",
    ]
    for w in structural_words:
        if re.search(r'\b' + re.escape(w) + r'\b', desc):
            return False
    return True


def _ocr_boxes_for_text(
    ocr_data: List[Dict[str, Any]],
    search_text: str,
) -> Optional[Dict[str, Any]]:
    """Search OCR word boxes for *search_text* (case-insensitive, multi-word aware).

    Returns a dict with x, y (center), left, top, width, height of the matched
    region, or None.
    """
    if not ocr_data or not search_text:
        return None
    search_lower = search_text.lower().strip()
    search_words = search_lower.split()

    # Build lines from OCR boxes (group by line_num if available, else by y-proximity)
    lines: Dict[Any, List[Dict[str, Any]]] = {}
    for box in ocr_data:
        text = (box.get("text") or "").strip()
        if not text:
            continue
        key = box.get("line_key") or box.get("line_num") or box.get("top", 0) // 20
        lines.setdefault(key, []).append(box)

    for _key, boxes in lines.items():
        boxes_sorted = sorted(boxes, key=lambda b: b.get("left", 0))
        line_text = " ".join((b.get("text") or "").strip() for b in boxes_sorted).lower()
        if search_lower not in line_text:
            # Try sequential word match
            remaining = line_text
            ok = True
            for sw in search_words:
                p = remaining.find(sw)
                if p == -1:
                    ok = False
                    break
                remaining = remaining[p + len(sw):]
            if not ok:
                continue

        # Found — compute bounding box over matched words
        min_left = min(b.get("left", 0) for b in boxes_sorted)
        min_top = min(b.get("top", 0) for b in boxes_sorted)
        max_right = max(b.get("left", 0) + b.get("width", 0) for b in boxes_sorted)
        max_bottom = max(b.get("top", 0) + b.get("height", 0) for b in boxes_sorted)
        return {
            "x": (min_left + max_right) // 2,
            "y": (min_top + max_bottom) // 2,
            "left": min_left,
            "top": min_top,
            "width": max_right - min_left,
            "height": max_bottom - min_top,
        }
    return None


def find_element_by_description(
    elements: List[Dict[str, Any]],
    description: str,
    ocr_data: Optional[List[Dict[str, Any]]] = None,
) -> Optional[Dict[str, Any]]:
    """
    Find the best matching element for a natural-language description.

    Handles positional references like "bottom right button", "the icon at the top",
    "the large panel in the center", etc.

    When the description looks like a text search (e.g. "offline by default") and
    *ocr_data* is provided, cross-references OCR word boxes with detected elements
    to find the element that contains the target text.  If no OCR data is available
    and the description is purely textual, returns None to avoid false positives.

    Args:
        elements: Output from detect_elements()
        description: User's description (e.g. "button at the bottom right")
        ocr_data: Optional OCR word boxes to cross-reference text labels.
                  Each box should have at least: text, left, top, width, height.

    Returns:
        Best matching element dict, or None
    """
    if not elements:
        return None

    desc = description.lower().strip()
    text_search = _is_text_search(desc)

    # ── Text search with OCR cross-reference ──
    if text_search and ocr_data:
        ocr_hit = _ocr_boxes_for_text(ocr_data, desc)
        if ocr_hit:
            # Find the element whose bounding box best overlaps the OCR hit
            ocr_cx, ocr_cy = ocr_hit["x"], ocr_hit["y"]
            best_el = None
            best_dist = float("inf")
            for el in elements:
                # Check containment first
                if (
                    el["left"] <= ocr_cx <= el["left"] + el["width"]
                    and el["top"] <= ocr_cy <= el["top"] + el["height"]
                ):
                    dist = abs(el["x"] - ocr_cx) + abs(el["y"] - ocr_cy)
                    if dist < best_dist:
                        best_dist = dist
                        best_el = el
            if best_el:
                return best_el
            # No containing element — return a synthetic element from OCR coords
            return {
                "id": -1,
                "x": ocr_hit["x"],
                "y": ocr_hit["y"],
                "left": ocr_hit["left"],
                "top": ocr_hit["top"],
                "width": ocr_hit["width"],
                "height": ocr_hit["height"],
                "area": ocr_hit["width"] * ocr_hit["height"],
                "aspect_ratio": round(ocr_hit["width"] / max(ocr_hit["height"], 1), 2),
                "region": _classify_region(ocr_hit["x"], ocr_hit["y"],
                                           max(el["left"] + el["width"] for el in elements),
                                           max(el["top"] + el["height"] for el in elements)),
                "kind": "text",
            }

    # If the description is purely textual but we have no OCR data,
    # do NOT guess — return None so the pipeline falls through to
    # pytesseract or vision LLM which can actually read text.
    if text_search:
        return None

    # ── Positional / kind matching (original logic) ──
    region_keywords = {
        "top-left": ["top left", "upper left", "top-left"],
        "top": ["top", "upper", "above"],
        "top-right": ["top right", "upper right", "top-right"],
        "left": ["left", "left side"],
        "center": ["center", "middle", "centre"],
        "right": ["right", "right side"],
        "bottom-left": ["bottom left", "lower left", "bottom-left"],
        "bottom": ["bottom", "lower", "below"],
        "bottom-right": ["bottom right", "lower right", "bottom-right"],
    }

    kind_keywords = {
        "button": ["button", "btn"],
        "icon": ["icon", "symbol", "small"],
        "input": ["input", "field", "text box", "textbox", "search bar", "text field"],
        "panel": ["panel", "window", "section", "area", "pane", "card"],
    }

    target_region: Optional[str] = None
    target_kind: Optional[str] = None

    # Check compound regions first (e.g. "bottom right" before "bottom" or "right")
    compound_regions = ["top-left", "top-right", "bottom-left", "bottom-right"]
    for region, keywords in region_keywords.items():
        if region in compound_regions:
            for kw in keywords:
                if kw in desc:
                    target_region = region
                    break
        if target_region:
            break

    if not target_region:
        for region, keywords in region_keywords.items():
            if region not in compound_regions:
                for kw in keywords:
                    if kw in desc:
                        target_region = region
                        break
            if target_region:
                break

    for kind, keywords in kind_keywords.items():
        for kw in keywords:
            if kw in desc:
                target_kind = kind
                break
        if target_kind:
            break

    best: Optional[Dict[str, Any]] = None
    best_score = -1.0

    for el in elements:
        score = 0.0
        if target_region and el["region"] == target_region:
            score += 5.0
        if target_kind and el["kind"] == target_kind:
            score += 3.0
        # Slight preference for reasonably-sized elements (not tiny noise)
        if el["area"] > 1000:
            score += 0.5
        if score > best_score:
            best_score = score
            best = el

    if best is None or best_score <= 0:
        return None

    return best


def build_elements_description(
    elements: List[Dict[str, Any]],
    max_elements: int = 30,
) -> str:
    """
    Build a concise text description of detected elements for injection
    into the vision LLM prompt.
    """
    if not elements:
        return ""

    subset = elements[:max_elements]
    lines = [f"Detected {len(elements)} UI elements on screen:"]
    for el in subset:
        lines.append(
            f"  [{el['id']}] {el['kind']} at ({el['x']},{el['y']}) "
            f"size {el['width']}x{el['height']} region={el['region']}"
        )
    if len(elements) > max_elements:
        lines.append(f"  ... and {len(elements) - max_elements} more")
    return "\n".join(lines)
