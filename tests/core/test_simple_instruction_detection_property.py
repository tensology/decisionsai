# Feature: workflow-step-runner-unification, Property 4: Simple instruction detection
"""
Property-based test verifying that `_is_simple_instruction()` correctly
classifies strings as simple (single-step) or complex (multi-step).

- Simple: non-empty, stripped length ≤ 80, contains no multi-step markers.
- Not simple: empty, stripped length > 80, or contains at least one marker.

**Validates: Requirements 3.2**
"""

from hypothesis import given, settings, assume
from hypothesis import strategies as st

from distr.core.workflow.service import _is_simple_instruction

# ---------------------------------------------------------------------------
# Constants (mirror the markers used in the implementation)
# ---------------------------------------------------------------------------

MULTI_STEP_MARKERS = [
    " and then ", " then ", " first ", " second ", " after that ",
    " next ", " finally ", " step 1", " step 2", " 1. ", " 2. ",
    " also ", " additionally ", " afterwards ", " once ", " when done",
    " and reply ", " and send ", " and open ", " and create ", " and check ",
    " and navigate ", " and click ", " and type ", " and save ",
]

# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

# Short printable strings that avoid all multi-step markers.
# We use from_regex to produce ASCII strings, then filter.
_short_no_marker_strategy = (
    st.text(
        alphabet=st.characters(whitelist_categories=("L", "N", "P", "Zs"),
                               whitelist_characters=" "),
        min_size=1,
        max_size=60,
    )
    .map(lambda s: s.strip())
    .filter(lambda s: 0 < len(s) <= 80)
    .filter(lambda s: "\n" not in s)
    .filter(lambda s: not any(m in s.lower() for m in MULTI_STEP_MARKERS))
)

# Strategy that produces a string guaranteed to contain at least one marker.
_marker_strategy = st.sampled_from(MULTI_STEP_MARKERS)

_with_marker_strategy = st.builds(
    lambda prefix, marker, suffix: prefix + marker + suffix,
    prefix=st.text(
        alphabet=st.characters(whitelist_categories=("L",), whitelist_characters=""),
        min_size=1,
        max_size=15,
    ),
    marker=_marker_strategy,
    suffix=st.text(
        alphabet=st.characters(whitelist_categories=("L",), whitelist_characters=""),
        min_size=1,
        max_size=15,
    ),
)

# Strategy for strings that exceed the 80-char threshold after stripping.
_long_string_strategy = st.text(
    alphabet=st.characters(whitelist_categories=("L", "N")),
    min_size=81,
    max_size=200,
)


# ---------------------------------------------------------------------------
# Property tests
# ---------------------------------------------------------------------------


@settings(max_examples=100, deadline=None)
@given(instruction=_short_no_marker_strategy)
def test_short_no_marker_strings_are_simple(instruction: str) -> None:
    """**Validates: Requirements 3.2**

    For any non-empty string that is ≤ 80 characters (stripped), contains no
    newlines, and contains none of the multi-step markers,
    _is_simple_instruction() SHALL return True."""
    assert _is_simple_instruction(instruction) is True, (
        f"Expected simple=True for {instruction!r} (len={len(instruction)})"
    )


@settings(max_examples=100, deadline=None)
@given(instruction=_with_marker_strategy)
def test_strings_with_markers_are_not_simple(instruction: str) -> None:
    """**Validates: Requirements 3.2**

    For any string that contains at least one multi-step marker,
    _is_simple_instruction() SHALL return False (regardless of length)."""
    # Only assert when the stripped length is within the short threshold,
    # so we isolate the marker-detection property from the length property.
    assume(0 < len(instruction.strip()) <= 80)
    assert _is_simple_instruction(instruction) is False, (
        f"Expected simple=False for {instruction!r} (contains marker)"
    )


@settings(max_examples=100, deadline=None)
@given(instruction=_long_string_strategy)
def test_long_strings_are_not_simple(instruction: str) -> None:
    """**Validates: Requirements 3.2**

    For any string whose stripped length exceeds 80 characters,
    _is_simple_instruction() SHALL return False."""
    assume(len(instruction.strip()) > 80)
    assert _is_simple_instruction(instruction) is False, (
        f"Expected simple=False for long string (len={len(instruction.strip())})"
    )


@settings(max_examples=100, deadline=None)
@given(instruction=st.just(""))
def test_empty_string_is_not_simple(instruction: str) -> None:
    """**Validates: Requirements 3.2**

    An empty string SHALL NOT be classified as simple."""
    assert _is_simple_instruction(instruction) is False, (
        "Expected simple=False for empty string"
    )
