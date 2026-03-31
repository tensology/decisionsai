# Feature: telegram-response-format
# Property 5: Missing keys produce safe defaults (text_only_override=False, auto_match_mode=True)
# Validates: Requirements 6.3
"""Property-based tests for load_response_format_settings.

Verifies that the settings loader returns safe defaults when keys are
missing, correctly passes through boolean values, and coerces non-boolean
truthy/falsy values via ``bool()``.
"""

from hypothesis import given, settings
from hypothesis import strategies as st

from distr.core.integrations.telegram.response_format import (
    load_response_format_settings,
)

# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

# Arbitrary dict keys that are NOT the two telegram settings keys.
_safe_key = st.text(min_size=1, max_size=30).filter(
    lambda k: k not in ("telegram_text_only_override", "telegram_auto_match_mode")
)
_safe_value = st.one_of(st.integers(), st.text(max_size=20), st.booleans(), st.none())

# Dict with arbitrary keys but guaranteed to NOT contain the telegram keys.
_unrelated_settings = st.dictionaries(keys=_safe_key, values=_safe_value, max_size=5)

# Truthy / falsy non-boolean values for coercion tests.
_truthy_values = st.one_of(
    st.integers(min_value=1),
    st.text(min_size=1),
    st.just([1]),
)
_falsy_values = st.sampled_from([0, "", [], None, 0.0, False])


# ---------------------------------------------------------------------------
# Property 5: Missing keys produce safe defaults
# ---------------------------------------------------------------------------


@settings(max_examples=200)
@given(extra=_unrelated_settings)
def test_missing_keys_produce_safe_defaults(extra: dict) -> None:
    """**Validates: Requirements 6.3**

    When neither ``telegram_text_only_override`` nor
    ``telegram_auto_match_mode`` is present in the settings dict,
    the function returns ``(False, True)``."""

    text_only, auto_match = load_response_format_settings(extra)
    assert text_only is False, f"Expected text_only=False for missing key, got {text_only!r}"
    assert auto_match is True, f"Expected auto_match=True for missing key, got {auto_match!r}"


@settings(max_examples=100)
@given(extra=_unrelated_settings)
def test_missing_text_only_key_defaults_false(extra: dict) -> None:
    """**Validates: Requirements 6.3**

    When only ``telegram_auto_match_mode`` is present,
    ``text_only_override`` defaults to ``False``."""

    extra["telegram_auto_match_mode"] = True
    text_only, _ = load_response_format_settings(extra)
    assert text_only is False


@settings(max_examples=100)
@given(extra=_unrelated_settings)
def test_missing_auto_match_key_defaults_true(extra: dict) -> None:
    """**Validates: Requirements 6.3**

    When only ``telegram_text_only_override`` is present,
    ``auto_match_mode`` defaults to ``True``."""

    extra["telegram_text_only_override"] = False
    _, auto_match = load_response_format_settings(extra)
    assert auto_match is True


# ---------------------------------------------------------------------------
# Boolean pass-through
# ---------------------------------------------------------------------------


@settings(max_examples=100)
@given(
    text_only=st.booleans(),
    auto_match=st.booleans(),
)
def test_boolean_values_passed_through(text_only: bool, auto_match: bool) -> None:
    """**Validates: Requirements 6.3**

    When both keys are present with boolean values, the function
    returns those exact values."""

    result = load_response_format_settings(
        {
            "telegram_text_only_override": text_only,
            "telegram_auto_match_mode": auto_match,
        }
    )
    assert result == (text_only, auto_match)


# ---------------------------------------------------------------------------
# Truthy / falsy coercion
# ---------------------------------------------------------------------------


@settings(max_examples=100)
@given(truthy_val=_truthy_values)
def test_truthy_values_coerced_to_true(truthy_val) -> None:
    """**Validates: Requirements 6.3**

    Non-boolean truthy values for either key are coerced to ``True``
    via ``bool()``."""

    t, a = load_response_format_settings(
        {
            "telegram_text_only_override": truthy_val,
            "telegram_auto_match_mode": truthy_val,
        }
    )
    assert t is True, f"Expected True for truthy {truthy_val!r}, got {t!r}"
    assert a is True, f"Expected True for truthy {truthy_val!r}, got {a!r}"


@settings(max_examples=100)
@given(falsy_val=st.sampled_from([0, "", [], None, 0.0]))
def test_falsy_values_coerced_to_false(falsy_val) -> None:
    """**Validates: Requirements 6.3**

    Non-boolean falsy values for either key are coerced to ``False``
    via ``bool()``."""

    t, a = load_response_format_settings(
        {
            "telegram_text_only_override": falsy_val,
            "telegram_auto_match_mode": falsy_val,
        }
    )
    assert t is False, f"Expected False for falsy {falsy_val!r}, got {t!r}"
    assert a is False, f"Expected False for falsy {falsy_val!r}, got {a!r}"
