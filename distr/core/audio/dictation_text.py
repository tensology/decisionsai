"""Pure-text helpers for dictation (no keyboard / pynput imports)."""


def trim_short_dictation_period(text: str, max_words_without_period: int = 5) -> str:
    """Drop a trailing full stop on brief dictation clips.

    STT often punctuates every utterance with a period. Short fragments (labels,
    field values, a few words) read better without one; longer dictated sentences keep it.
    """
    stripped = (text or "").strip()
    if not stripped:
        return stripped
    if len(stripped.split()) > max_words_without_period:
        return stripped
    if not stripped.endswith(".") or stripped.endswith("..."):
        return stripped
    # Keep decimals/version tokens like "3.14" or "v2.0".
    if len(stripped) >= 2 and stripped[-2].isdigit():
        return stripped
    return stripped[:-1].rstrip()
