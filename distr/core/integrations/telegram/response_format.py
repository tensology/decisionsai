"""Response format controller for Telegram messages.

Determines whether the agent replies with text or voice based on:
- input message type (text vs voice)
- persistent text_only_override setting
- auto_match_mode setting

Precedence: text_only_override (highest) → auto_match_mode → default voice (lowest).
The safe default is auto-match: typed Telegram messages receive text replies,
while voice replies require a voice input or an explicit voice preference.

Mode phrases: "text only" forces text for every reply; "use voice" and
"auto"/"match"/"mirror" both restore the mirror default (voice in → voice,
text in → text) and clear any stuck text-only override.
"""

import re
from typing import Literal, Optional, Tuple

ResponseFormat = Literal["text", "voice"]
InputType = Literal["text", "voice"]

# Phrases that signal the user wants text-only responses.
# NOTE: short generic ones like "no voice"/"no audio" were removed — they fire
# inside ordinary sentences ("I have no voice today") and stick the mode on.
_TEXT_PREFERENCE_PHRASES = [
    "just respond in text",
    "respond in text",
    "send your response back as text",
    "send response as text",
    "respond with text",
    "respond as text",
    "reply with text",
    "reply as text",
    "send text",
    "text only",
    "text response",
    "text reply",
    "dont use voice",
    "don't use voice",
]

# Phrases that signal the user wants voice responses (mirror mode: voice notes
# keep getting voice while typed messages still get text).
_VOICE_PREFERENCE_PHRASES = [
    "send your response back as voice",
    "send response as voice",
    "respond with voice",
    "respond as voice",
    "reply with voice",
    "reply as voice",
    "send voice",
    "use voice",
    "voice response",
    "voice reply",
    "send audio",
    "use audio",
]

# Phrases that explicitly ask to go back to matching the input format.
# Matched on whole words so "automatic"/"automatically" do not trigger.
_AUTO_PREFERENCE_PHRASES = [
    "auto",
    "auto match",
    "auto-match",
    "automatic",
    "match my input",
    "match the input",
    "mirror",
    "mirror my input",
    "auto mode",
    "mirror mode",
    "match mode",
]


def determine_response_format(
    input_type: InputType,
    text_only_override: bool,
    auto_match_mode: bool,
) -> ResponseFormat:
    """Determine whether to respond with text or voice.

    Precedence: text_only_override (highest) → auto_match_mode → default voice (lowest).
    """
    if text_only_override:
        return "text"
    if auto_match_mode:
        return input_type  # mirror the input
    return "voice"  # legacy default


def detect_mode_switch_intent(
    message_text: str,
) -> Optional[Literal["text_only", "voice", "auto"]]:
    """Detect if the user's message asks to switch response mode.

    Returns ``"text_only"`` (always reply as text), ``"voice"`` (mirror the
    input — voice notes get voice, typed messages get text), ``"auto"`` (same
    mirror mode), or ``None`` when there is no mode request.
    """
    text_lower = (message_text or "").lower()
    if not text_lower:
        return None

    # Voice/text phrases keep substring matching. Voice wins if both appear
    # (the more specific request).
    if any(phrase in text_lower for phrase in _VOICE_PREFERENCE_PHRASES):
        return "voice"
    if any(phrase in text_lower for phrase in _TEXT_PREFERENCE_PHRASES):
        return "text_only"
    # Auto/match phrases match on whole words so "automatic" in an ordinary
    # sentence does not flip the mode.
    for phrase in _AUTO_PREFERENCE_PHRASES:
        if re.search(rf"(?<![a-z0-9]){re.escape(phrase)}(?![a-z0-9])", text_lower):
            return "auto"
    return None


def load_response_format_settings(settings: dict) -> Tuple[bool, bool]:
    """Extract text_only_override and auto_match_mode from a settings dict.

    Returns ``(text_only_override, auto_match_mode)`` with safe defaults
    (``False`` and ``True`` respectively).
    """
    text_only = settings.get("telegram_text_only_override", False)
    auto_match = settings.get("telegram_auto_match_mode", True)
    return bool(text_only), bool(auto_match)
