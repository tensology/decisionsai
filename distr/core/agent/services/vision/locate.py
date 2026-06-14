"""
Fast text-based locate using pytesseract.

For LOCATE and MOUSE_ACTION intents, use OCR to find text on screen
without calling the vision LLM (~100-300ms vs 1-5s).
"""

import logging
from typing import Optional, List, Dict, Any

logger = logging.getLogger(__name__)

_pytesseract_available = None


def _check_pytesseract() -> bool:
    """Lazy check if pytesseract is available."""
    global _pytesseract_available
    if _pytesseract_available is not None:
        return _pytesseract_available
    try:
        import pytesseract
        pytesseract.get_tesseract_version()
        _pytesseract_available = True
        return True
    except Exception as e:
        logger.debug("pytesseract not available: %s", e)
        _pytesseract_available = False
        return False


def locate_text(
    image_path: str,
    search_text: str,
    fuzzy: bool = True,
) -> Optional[Dict[str, Any]]:
    """
    Find text on an image using OCR. Returns center coordinates and bounding box.

    Supports both single-word and multi-word phrase matching by building
    line-level text from adjacent OCR boxes.

    Args:
        image_path: Path to screenshot image
        search_text: Text to find (e.g., "Submit", "Save", "offline by default")
        fuzzy: If True, match substrings and normalize case

    Returns:
        Dict with x, y (center), left, top, width, height, matched_text, confidence,
        or None if not found
    """
    if not _check_pytesseract():
        return None
    if not search_text or not search_text.strip():
        return None

    try:
        import pytesseract
        from PIL import Image

        img = Image.open(image_path)
        data = pytesseract.image_to_data(img, output_type=pytesseract.Output.DICT)

        search_lower = search_text.strip().lower()
        search_words = search_lower.split()
        is_multi_word = len(search_words) > 1

        best = None
        best_score = 0.0

        n_boxes = len(data['text'])

        # ── Single-word matching (original fast path) ──
        if not is_multi_word:
            for i in range(n_boxes):
                word = (data['text'][i] or "").strip()
                if not word:
                    continue
                word_lower = word.lower()
                conf = float(data['conf'][i] or 0)
                if conf < 0:
                    continue  # pytesseract uses -1 for invalid
                conf_pct = conf / 100.0

                if fuzzy:
                    if search_lower in word_lower or word_lower in search_lower:
                        score = conf_pct * (1.0 + 0.5 * (len(search_lower) / max(len(word_lower), 1)))
                        if score > best_score:
                            best_score = score
                            best = i
                else:
                    if word_lower == search_lower:
                        score = conf_pct
                        if score > best_score:
                            best_score = score
                            best = i

            if best is not None:
                left = data['left'][best]
                top = data['top'][best]
                width = data['width'][best]
                height = data['height'][best]
                return {
                    "x": left + width // 2,
                    "y": top + height // 2,
                    "left": left,
                    "top": top,
                    "width": width,
                    "height": height,
                    "matched_text": data['text'][best],
                    "confidence": best_score,
                }
            return None

        # ── Multi-word phrase matching ──
        # Group OCR boxes into lines (same block_num + line_num), then
        # search for the phrase within each line's concatenated text.
        # Build lines: group by (block_num, par_num, line_num)
        lines: Dict[tuple, List[int]] = {}
        for i in range(n_boxes):
            word = (data['text'][i] or "").strip()
            if not word:
                continue
            conf = float(data['conf'][i] or 0)
            if conf < 0:
                continue
            key = (data['block_num'][i], data['par_num'][i], data['line_num'][i])
            lines.setdefault(key, []).append(i)

        best_phrase_match = None
        best_phrase_score = 0.0

        for _key, indices in lines.items():
            # Build the full line text and track word positions
            line_words = []
            for idx in indices:
                w = (data['text'][idx] or "").strip()
                if w:
                    line_words.append((idx, w))

            if not line_words:
                continue

            line_text = " ".join(w for _, w in line_words).lower()

            # Check if the search phrase appears in this line
            pos = line_text.find(search_lower)
            if pos == -1 and fuzzy:
                # Try matching each search word individually in sequence
                remaining = line_text
                all_found = True
                for sw in search_words:
                    p = remaining.find(sw)
                    if p == -1:
                        all_found = False
                        break
                    remaining = remaining[p + len(sw):]
                if not all_found:
                    continue
                pos = line_text.find(search_words[0])

            if pos == -1:
                continue

            # Find which OCR boxes correspond to the matched phrase
            # Walk through line_words, accumulating character positions
            char_offset = 0
            match_start_idx = None
            match_end_idx = None
            for wi, (idx, w) in enumerate(line_words):
                word_start = char_offset
                word_end = char_offset + len(w)
                # Check overlap with the match position
                if word_start <= pos < word_end or (pos <= word_start < pos + len(search_lower)):
                    if match_start_idx is None:
                        match_start_idx = wi
                    match_end_idx = wi
                char_offset = word_end + 1  # +1 for space

            if match_start_idx is None:
                continue

            # Compute bounding box spanning all matched words
            matched_indices = [line_words[j][0] for j in range(match_start_idx, match_end_idx + 1)]
            min_left = min(data['left'][i] for i in matched_indices)
            min_top = min(data['top'][i] for i in matched_indices)
            max_right = max(data['left'][i] + data['width'][i] for i in matched_indices)
            max_bottom = max(data['top'][i] + data['height'][i] for i in matched_indices)
            avg_conf = sum(float(data['conf'][i] or 0) for i in matched_indices) / len(matched_indices) / 100.0

            score = avg_conf * (1.0 + 0.3 * len(search_words))
            if score > best_phrase_score:
                best_phrase_score = score
                matched_text = " ".join(data['text'][i] for i in matched_indices)
                best_phrase_match = {
                    "x": (min_left + max_right) // 2,
                    "y": (min_top + max_bottom) // 2,
                    "left": min_left,
                    "top": min_top,
                    "width": max_right - min_left,
                    "height": max_bottom - min_top,
                    "matched_text": matched_text,
                    "confidence": best_phrase_score,
                }

        return best_phrase_match

    except Exception as e:
        logger.warning("pytesseract locate failed: %s", e)
        return None


def build_ocr_context(image_path: str, max_lines: int = 40) -> str:
    """
    Run OCR on *image_path* and return a compact text summary suitable for
    injecting into a vision LLM prompt.

    Groups words into lines and returns them with approximate bounding-box
    coordinates so the LLM can cross-reference text positions with the image.

    Returns an empty string if pytesseract is unavailable or OCR fails.
    """
    if not _check_pytesseract():
        return ""
    try:
        import pytesseract
        from PIL import Image

        img = Image.open(image_path)
        data = pytesseract.image_to_data(img, output_type=pytesseract.Output.DICT)
        n = len(data['text'])

        # Group words into lines
        lines_map: Dict[tuple, List[int]] = {}
        for i in range(n):
            word = (data['text'][i] or '').strip()
            if not word:
                continue
            conf = float(data['conf'][i] or 0)
            if conf < 0:
                continue
            key = (data['block_num'][i], data['par_num'][i], data['line_num'][i])
            lines_map.setdefault(key, []).append(i)

        if not lines_map:
            return ""

        out = ["OCR text detected on screen:"]
        count = 0
        for _key in sorted(lines_map.keys()):
            indices = lines_map[_key]
            words = [(data['text'][i] or '').strip() for i in indices]
            line_text = ' '.join(w for w in words if w)
            if not line_text:
                continue
            # Bounding box for the whole line
            left = min(data['left'][i] for i in indices)
            top = min(data['top'][i] for i in indices)
            right = max(data['left'][i] + data['width'][i] for i in indices)
            bottom = max(data['top'][i] + data['height'][i] for i in indices)
            cx = (left + right) // 2
            cy = (top + bottom) // 2
            out.append(f'  "{line_text}" at ({cx},{cy}) box=({left},{top},{right},{bottom})')
            count += 1
            if count >= max_lines:
                out.append(f"  ... (truncated, {len(lines_map) - count} more lines)")
                break

        return '\n'.join(out) if count > 0 else ""
    except Exception as e:
        logger.warning("build_ocr_context failed: %s", e)
        return ""


def extract_search_target_from_prompt(prompt: str) -> Optional[str]:
    """
    Extract the text to search for from a locate-style prompt.

    E.g., "Where is the Submit button?" -> "Submit"
    "Find the Save button" -> "Save"
    "Click on Search" -> "Search"
    "move mouse to the word offline by default" -> "offline by default"
    "move mouse to the word offline by default just to the center of that" -> "offline by default"
    """
    import re
    if not prompt or not prompt.strip():
        return None
    text = prompt.strip()

    # Strip trailing noise phrases that don't contribute to the target
    noise_suffixes = [
        r'\s+just\s+to\s+the\s+center\s+of\s+(?:that|it|this).*',
        r'\s+to\s+the\s+center\s+of\s+(?:that|it|this).*',
        r'\s+right\s+in\s+the\s+(?:center|middle)\s+of\s+(?:that|it|this).*',
        r'\s+in\s+the\s+(?:center|middle).*',
        r'\s+and\s+(?:click|press|select|open)\s+(?:on\s+)?(?:it|that|this).*',
    ]
    for pattern in noise_suffixes:
        text = re.sub(pattern, '', text, flags=re.I)

    # "where is (the) X" / "find (the) X"
    m = re.search(r"(?:where\s+is|find|locate|show\s+me)\s+(?:the\s+)?(.+?)(?:\s+button|\s+link|\s+icon)?(?:\?|\.|$)", text, re.I)
    if m:
        return _clean_target(m.group(1))
    # "click (on) (the) X"
    m = re.search(r"(?:click|press)\s+(?:on\s+)?(?:the\s+)?(.+?)(?:\s+button|\s+link)?(?:\?|\.|$)", text, re.I)
    if m:
        return _clean_target(m.group(1))
    # "move to (the) X" / "go to (the) X"
    # Handle complex patterns like "move mouse over my screen to the word X"
    # Try the most specific pattern first (with "my screen to")
    m = re.search(
        r"(?:move\s+(?:the\s+)?(?:mouse\s+|mask\s+|cursor\s+)?(?:to|over)"
        r"(?:\s+(?:my|the|this)\s+screen)?"  # optional "my screen" / "the screen"
        r"\s+(?:to\s+)?(?:the\s+)?)"
        r"(.+?)(?:\?|\.|$)",
        text, re.I,
    )
    if m:
        return _clean_target(m.group(1))
    # Simpler fallback: "go to X", "hover over X"
    m = re.search(r"(?:go\s+to\s+(?:the\s+)?|hover\s+(?:over\s+)?(?:the\s+)?)(.+?)(?:\?|\.|$)", text, re.I)
    if m:
        return _clean_target(m.group(1))
    return None


def _clean_target(raw: str) -> Optional[str]:
    """Strip filler words from an extracted search target."""
    import re
    t = raw.strip()
    # Remove leading screen references ("my screen to the", "this screen to", etc.)
    t = re.sub(r'^(?:my|the|this)\s+screen\s+(?:to\s+)?(?:the\s+)?', '', t, flags=re.I)
    # Remove leading positional filler ("center of (the)", "middle of (the)")
    t = re.sub(r'^(?:the\s+)?(?:center|middle)\s+of\s+(?:the\s+)?', '', t, flags=re.I)
    # Remove leading "word" / "text" / "phrase" / "words" filler
    t = re.sub(r'^(?:the\s+)?(?:word|text|phrase|words|label|string)\s+', '', t, flags=re.I)
    # Remove trailing "button", "icon", "link" if still present
    t = re.sub(r'\s+(?:button|icon|link|tab|menu)$', '', t, flags=re.I)
    t = t.strip().strip('"').strip("'").strip()
    return t if t else None
