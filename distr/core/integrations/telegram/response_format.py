"""Response format controller for Telegram messages.

Determines whether the agent replies with text or voice based on:
- input message type (text vs voice)
- persistent text_only_override setting
- auto_match_mode setting

Precedence: text_only_override (highest) → auto_match_mode → default voice (lowest).
"""

from typing import Literal, Optional, Tuple

ResponseFormat = Literal["text", "voice"]
InputType = Literal["text", "voice"]

# Phrases that signal the user wants text-only responses.
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
    "no voice",
    "no audio",
    "dont use voice",
    "don't use voice",
]

# Phrases that signal the user wants voice responses.
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
) -> Optional[Literal["text_only", "voice"]]:
    """Detect if the user's message contains a mode-switch phrase.

    Returns ``"text_only"`` to enable the text override, ``"voice"`` to
    disable it, or ``None`` if no intent is detected.
    """
    text_lower = message_text.lower()

    wants_text = any(phrase in text_lower for phrase in _TEXT_PREFERENCE_PHRASES)
    wants_voice = any(phrase in text_lower for phrase in _VOICE_PREFERENCE_PHRASES)

    # If both are detected, voice takes precedence (user is explicitly
    # requesting voice, which is the more specific action).
    if wants_voice:
        return "voice"
    if wants_text:
        return "text_only"
    return None


def load_response_format_settings(settings: dict) -> Tuple[bool, bool]:
    """Extract text_only_override and auto_match_mode from a settings dict.

    Returns ``(text_only_override, auto_match_mode)`` with safe defaults
    (``False`` and ``True`` respectively).
    """
    text_only = settings.get("telegram_text_only_override", False)
    auto_match = settings.get("telegram_auto_match_mode", True)
    return bool(text_only), bool(auto_match)
