# Feature: telegram-response-format, Property 1: Text-only override always produces text response
# Validates: Requirements 5.1, 5.2, 3.2, 3.3
"""Property-based tests for determine_response_format.

Verifies the precedence truth table from the design document:
  text_only_override (highest) → auto_match_mode → default voice (lowest).
"""

from hypothesis import given, settings
from hypothesis import strategies as st

from distr.core.integrations.telegram.response_format import (
    InputType,
    determine_response_format,
)

# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

_input_type_strategy = st.sampled_from(["text", "voice"])


# ---------------------------------------------------------------------------
# Property 1: text_only_override=True → always "text"
# ---------------------------------------------------------------------------


@settings(max_examples=100)
@given(
    input_type=_input_type_strategy,
    auto_match_mode=st.booleans(),
)
def test_text_only_override_always_returns_text(
    input_type: InputType,
    auto_match_mode: bool,
) -> None:
    """**Validates: Requirements 5.1, 5.2, 3.2, 3.3**

    When text_only_override is True, the result is always "text"
    regardless of any combination of input_type and auto_match_mode."""

    result = determine_response_format(
        input_type=input_type,
        text_only_override=True,
        auto_match_mode=auto_match_mode,
    )
    assert result == "text", (
        f"Expected 'text' with text_only_override=True, "
        f"got {result!r} (input_type={input_type!r}, auto_match_mode={auto_match_mode!r})"
    )


# ---------------------------------------------------------------------------
# Property 2: text_only_override=False, auto_match_mode=True → mirrors input
# ---------------------------------------------------------------------------


@settings(max_examples=100)
@given(input_type=_input_type_strategy)
def test_auto_match_mirrors_input_type(input_type: InputType) -> None:
    """**Validates: Requirements 5.3, 2.1, 2.2**

    When text_only_override is False and auto_match_mode is True,
    the result matches the input_type."""

    result = determine_response_format(
        input_type=input_type,
        text_only_override=False,
        auto_match_mode=True,
    )
    assert result == input_type, (
        f"Expected {input_type!r} with auto_match_mode=True, got {result!r}"
    )


# ---------------------------------------------------------------------------
# Property 3: text_only_override=False, auto_match_mode=False → always "voice"
# ---------------------------------------------------------------------------


@settings(max_examples=100)
@given(input_type=_input_type_strategy)
def test_no_auto_match_defaults_to_voice(input_type: InputType) -> None:
    """**Validates: Requirements 5.4, 2.3**

    When text_only_override is False and auto_match_mode is False,
    the result is always "voice" regardless of input_type."""

    result = determine_response_format(
        input_type=input_type,
        text_only_override=False,
        auto_match_mode=False,
    )
    assert result == "voice", (
        f"Expected 'voice' with auto_match_mode=False, "
        f"got {result!r} (input_type={input_type!r})"
    )
