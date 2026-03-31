# Feature: telegram-response-format
# Property 2: Known text-preference phrases always return "text_only"
# Property 3: Known voice-preference phrases always return "voice"
# Property 4: Messages without mode-switch phrases return None
# Validates: Requirements 4.1, 4.2
"""Property-based tests for detect_mode_switch_intent.

Verifies that the phrase-matching logic correctly classifies user messages
as text-preference, voice-preference, or no preference.
"""

from hypothesis import given, settings, assume
from hypothesis import strategies as st

from distr.core.integrations.telegram.response_format import (
    _TEXT_PREFERENCE_PHRASES,
    _VOICE_PREFERENCE_PHRASES,
    detect_mode_switch_intent,
)

# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

_text_phrase_strategy = st.sampled_from(_TEXT_PREFERENCE_PHRASES)
_voice_phrase_strategy = st.sampled_from(_VOICE_PREFERENCE_PHRASES)

# Surrounding text that won't accidentally contain any known phrase.
# We use short alphanumeric tokens separated by spaces.
_safe_word = st.from_regex(r"[a-z]{1,4}", fullmatch=True)
_safe_padding = st.lists(_safe_word, min_size=0, max_size=3).map(" ".join)

# Strategy for messages guaranteed to contain NO known phrase.
_all_phrases = _TEXT_PREFERENCE_PHRASES + _VOICE_PREFERENCE_PHRASES

def _contains_any_phrase(text: str) -> bool:
    lowered = text.lower()
    return any(phrase in lowered for phrase in _all_phrases)

_no_phrase_strategy = st.text(
    alphabet=st.characters(whitelist_categories=("L", "N", "Zs"), max_codepoint=127),
    min_size=0,
    max_size=60,
).filter(lambda t: not _contains_any_phrase(t))


# ---------------------------------------------------------------------------
# Property 2: Known text-preference phrases always return "text_only"
# ---------------------------------------------------------------------------


@settings(max_examples=200)
@given(
    phrase=_text_phrase_strategy,
    prefix=_safe_padding,
    suffix=_safe_padding,
)
def test_text_preference_phrases_return_text_only(
    phrase: str,
    prefix: str,
    suffix: str,
) -> None:
    """**Validates: Requirements 4.1**

    Any message containing a known text-preference phrase (and no
    voice-preference phrase) must return "text_only"."""

    message = f"{prefix} {phrase} {suffix}".strip()
    # Skip if padding accidentally introduced a voice phrase
    assume(not any(vp in message.lower() for vp in _VOICE_PREFERENCE_PHRASES))

    result = detect_mode_switch_intent(message)
    assert result == "text_only", (
        f"Expected 'text_only' for message containing text phrase {phrase!r}, "
        f"got {result!r} (full message: {message!r})"
    )


# ---------------------------------------------------------------------------
# Property 3: Known voice-preference phrases always return "voice"
# ---------------------------------------------------------------------------


@settings(max_examples=200)
@given(
    phrase=_voice_phrase_strategy,
    prefix=_safe_padding,
    suffix=_safe_padding,
)
def test_voice_preference_phrases_return_voice(
    phrase: str,
    prefix: str,
    suffix: str,
) -> None:
    """**Validates: Requirements 4.2**

    Any message containing a known voice-preference phrase must return
    "voice" (voice takes precedence even if text phrases are also present)."""

    message = f"{prefix} {phrase} {suffix}".strip()

    result = detect_mode_switch_intent(message)
    assert result == "voice", (
        f"Expected 'voice' for message containing voice phrase {phrase!r}, "
        f"got {result!r} (full message: {message!r})"
    )


# ---------------------------------------------------------------------------
# Property 4: Messages without mode-switch phrases return None
# ---------------------------------------------------------------------------


@settings(max_examples=200)
@given(message=_no_phrase_strategy)
def test_no_mode_switch_phrases_return_none(message: str) -> None:
    """**Validates: Requirements 4.1, 4.2**

    Messages that contain none of the known text or voice preference
    phrases must return None (no mode switch detected)."""

    result = detect_mode_switch_intent(message)
    assert result is None, (
        f"Expected None for message without any known phrase, "
        f"got {result!r} (message: {message!r})"
    )
