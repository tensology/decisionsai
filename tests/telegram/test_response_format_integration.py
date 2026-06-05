# Feature: telegram-response-format
# Integration tests for the full response format flow
# Validates: Requirements 1.1, 1.2, 2.1, 2.2, 3.2, 4.1, 4.2, 5.5
"""Integration tests that exercise the full settings → format decision flow.

These tests combine ``determine_response_format``, ``detect_mode_switch_intent``,
and ``load_response_format_settings`` to verify end-to-end behaviour of the
ResponseFormatController without mocking any of the three functions.
"""

import pytest

from distr.core.integrations.telegram.response_format import (
    determine_response_format,
    detect_mode_switch_intent,
    load_response_format_settings,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_settings(
    text_only_override: bool = False,
    auto_match_mode: bool = True,
) -> dict:
    """Build a minimal settings dict with the two telegram keys."""
    return {
        "telegram_text_only_override": text_only_override,
        "telegram_auto_match_mode": auto_match_mode,
    }


def _apply_intent_to_settings(settings: dict, intent) -> dict:
    """Simulate persisting a mode-switch intent into the settings dict.

    This mirrors what ``_handle_telegram_message`` does: when an intent is
    detected it calls ``update_setting()`` to persist the preference.
    """
    if intent == "text_only":
        settings["telegram_text_only_override"] = True
        settings["telegram_auto_match_mode"] = True
    elif intent == "voice":
        settings["telegram_text_only_override"] = False
        settings["telegram_auto_match_mode"] = False
    return settings


# ---------------------------------------------------------------------------
# 1. Default and auto-match mode
#    Validates: Requirements 1.1, 2.1
# ---------------------------------------------------------------------------


class TestAutoMatchMode:
    """Verify auto_match_mode mirrors input type when text_only_override is off."""

    def test_default_text_input_produces_text_response(self):
        settings = _make_settings(text_only_override=False)
        text_only, auto_match = load_response_format_settings(settings)
        result = determine_response_format(
            input_type="text",
            text_only_override=text_only,
            auto_match_mode=auto_match,
        )
        assert result == "text"

    def test_text_input_produces_text_response(self):
        settings = _make_settings(text_only_override=False, auto_match_mode=True)
        text_only, auto_match = load_response_format_settings(settings)
        result = determine_response_format(
            input_type="text",
            text_only_override=text_only,
            auto_match_mode=auto_match,
        )
        assert result == "text"

    def test_voice_input_produces_voice_response(self):
        """Validates: Requirements 1.2, 2.2"""
        settings = _make_settings(text_only_override=False, auto_match_mode=True)
        text_only, auto_match = load_response_format_settings(settings)
        result = determine_response_format(
            input_type="voice",
            text_only_override=text_only,
            auto_match_mode=auto_match,
        )
        assert result == "voice"


# ---------------------------------------------------------------------------
# 2. Text-only override forces text response regardless of input type
#    Validates: Requirements 3.2, 5.2
# ---------------------------------------------------------------------------


class TestTextOnlyOverride:
    """Verify text_only_override always produces text, regardless of input."""

    def test_text_only_override_with_text_input(self):
        settings = _make_settings(text_only_override=True, auto_match_mode=True)
        text_only, auto_match = load_response_format_settings(settings)
        result = determine_response_format(
            input_type="text",
            text_only_override=text_only,
            auto_match_mode=auto_match,
        )
        assert result == "text"

    def test_text_only_override_with_voice_input(self):
        settings = _make_settings(text_only_override=True, auto_match_mode=True)
        text_only, auto_match = load_response_format_settings(settings)
        result = determine_response_format(
            input_type="voice",
            text_only_override=text_only,
            auto_match_mode=auto_match,
        )
        assert result == "text"

    def test_text_only_override_ignores_auto_match_disabled(self):
        settings = _make_settings(text_only_override=True, auto_match_mode=False)
        text_only, auto_match = load_response_format_settings(settings)
        result = determine_response_format(
            input_type="voice",
            text_only_override=text_only,
            auto_match_mode=auto_match,
        )
        assert result == "text"


# ---------------------------------------------------------------------------
# 3. Natural language "respond with text" → persists preference → text response
#    Validates: Requirements 4.1, 5.5
# ---------------------------------------------------------------------------


class TestNaturalLanguageTextSwitch:
    """Full cycle: detect intent → update settings → load → determine format."""

    def test_respond_with_text_enables_override(self):
        # Start with default settings (no override)
        settings = _make_settings(text_only_override=False, auto_match_mode=True)

        # User sends a text message containing a text-preference phrase
        user_message = "Hey, just respond in text please"
        intent = detect_mode_switch_intent(user_message)
        assert intent == "text_only"

        # Persist the intent (simulates update_setting call)
        _apply_intent_to_settings(settings, intent)

        # Reload and determine format — should be text even for voice input
        text_only, auto_match = load_response_format_settings(settings)
        assert text_only is True

        result = determine_response_format(
            input_type="voice",
            text_only_override=text_only,
            auto_match_mode=auto_match,
        )
        assert result == "text"

    def test_text_only_phrase_persists_across_subsequent_messages(self):
        settings = _make_settings(text_only_override=False, auto_match_mode=True)

        # First message: user requests text mode
        intent = detect_mode_switch_intent("text only")
        _apply_intent_to_settings(settings, intent)

        # Second message: normal voice message (no mode-switch phrase)
        second_intent = detect_mode_switch_intent("What's the weather like?")
        assert second_intent is None  # no change
        # Settings remain unchanged — override still active

        text_only, auto_match = load_response_format_settings(settings)
        result = determine_response_format(
            input_type="voice",
            text_only_override=text_only,
            auto_match_mode=auto_match,
        )
        assert result == "text"


# ---------------------------------------------------------------------------
# 4. Natural language "respond with voice" → clears override → voice-first
#    Validates: Requirements 4.2, 5.5
# ---------------------------------------------------------------------------


class TestNaturalLanguageVoiceSwitch:
    """Full cycle: enable text override → voice intent → revert to voice-first."""

    def test_respond_with_voice_clears_override(self):
        # Start with text_only_override already enabled
        settings = _make_settings(text_only_override=True)

        # User says "respond with voice"
        intent = detect_mode_switch_intent("respond with voice")
        assert intent == "voice"

        # Persist the intent
        _apply_intent_to_settings(settings, intent)

        # Reload — override should be cleared
        text_only, auto_match = load_response_format_settings(settings)
        assert text_only is False
        assert auto_match is False

        # Voice input should now produce voice response.
        result = determine_response_format(
            input_type="voice",
            text_only_override=text_only,
            auto_match_mode=auto_match,
        )
        assert result == "voice"

    def test_voice_switch_reverts_text_input_to_voice_first(self):
        # Override was on, user clears it
        settings = _make_settings(text_only_override=True)
        intent = detect_mode_switch_intent("use voice")
        _apply_intent_to_settings(settings, intent)

        text_only, auto_match = load_response_format_settings(settings)

        # Text input should now produce voice (default voice-first mode)
        result = determine_response_format(
            input_type="text",
            text_only_override=text_only,
            auto_match_mode=auto_match,
        )
        assert result == "voice"


# ---------------------------------------------------------------------------
# 5. Full round-trip cycle: text override → voice revert → explicit voice
#    Validates: Requirements 5.5
# ---------------------------------------------------------------------------


class TestFullCycle:
    """Simulate a multi-message conversation with mode switches."""

    def test_text_override_then_voice_revert_then_auto_match(self):
        settings = _make_settings(text_only_override=False)

        # Step 1: voice input, auto-match → voice response
        text_only, auto_match = load_response_format_settings(settings)
        assert determine_response_format("voice", text_only, auto_match) == "voice"

        # Step 2: user says "respond with text" → override enabled
        intent = detect_mode_switch_intent("respond with text")
        assert intent == "text_only"
        _apply_intent_to_settings(settings, intent)

        text_only, auto_match = load_response_format_settings(settings)
        assert determine_response_format("voice", text_only, auto_match) == "text"

        # Step 3: user says "respond with voice" → override cleared
        intent = detect_mode_switch_intent("respond with voice")
        assert intent == "voice"
        _apply_intent_to_settings(settings, intent)

        text_only, auto_match = load_response_format_settings(settings)
        # Voice input → voice response (auto-match restored)
        assert determine_response_format("voice", text_only, auto_match) == "voice"
        # Text input → voice response (voice-first restored)
        assert determine_response_format("text", text_only, auto_match) == "voice"
