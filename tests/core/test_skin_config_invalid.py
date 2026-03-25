# Feature: oracle-skins-system, Property 3: Invalid JSON produces descriptive errors
# Validates: Requirements 2.2, 1.14
"""Property-based test: any string that is not valid JSON, or valid JSON that
violates the SkinConfig schema at the parse level, causes parse() to raise
ValueError with a non-empty descriptive message."""

import json

from hypothesis import given, settings, assume
from hypothesis import strategies as st

from distr.core.skin_config import parse

# ---------------------------------------------------------------------------
# Helpers — minimal valid building blocks
# ---------------------------------------------------------------------------

_VALID_RENDERING = {
    "shape": "round",
    "border": True,
    "shadow": True,
    "glow_on_hold": True,
}

_VALID_EVENT_RESPONSE = {
    "animation": "idle.webm",
    "show_player": False,
    "show_chat_bubble": False,
    "glow": False,
    "glow_color": [0, 0, 0],
    "glow_speed": 1000,
    "glow_style": "breathing",
    "tray_icon": "default",
}


def _valid_config() -> dict:
    """Return a minimal valid skin config dict."""
    return {
        "type": "oracle",
        "name": "Test",
        "rendering": dict(_VALID_RENDERING),
        "events": {"idle": dict(_VALID_EVENT_RESPONSE)},
    }


def _is_not_valid_json(s: str) -> bool:
    """Return True if *s* is NOT parseable as valid JSON at all."""
    try:
        json.loads(s)
        return False
    except (json.JSONDecodeError, ValueError):
        return True


# ---------------------------------------------------------------------------
# Strategy: invalid JSON strings
# ---------------------------------------------------------------------------


@st.composite
def invalid_json_strategy(draw):
    """Generate strings that are either not valid JSON or valid JSON that
    violates the SkinConfig schema in ways that parse() detects."""

    kind = draw(st.sampled_from([
        "random_text",
        "truncated_json",
        "non_object_toplevel",
        "missing_type",
        "missing_name",
        "missing_rendering",
        "missing_events",
        "rendering_not_object",
        "rendering_missing_field",
        "events_not_object",
        "event_response_not_object",
        "event_missing_animation",
        "invalid_glow_color_length",
    ]))

    # --- Completely invalid JSON ---
    if kind == "random_text":
        text = draw(st.text(min_size=1, max_size=80).filter(_is_not_valid_json))
        return text

    if kind == "truncated_json":
        cfg = _valid_config()
        full = json.dumps(cfg)
        cut = draw(st.integers(min_value=1, max_value=max(1, len(full) - 2)))
        truncated = full[:cut]
        assume(_is_not_valid_json(truncated))
        return truncated

    # --- Valid JSON but not an object ---
    if kind == "non_object_toplevel":
        value = draw(st.one_of(
            st.lists(st.integers(), max_size=3),
            st.text(min_size=0, max_size=20),
            st.integers(),
            st.floats(allow_nan=False, allow_infinity=False),
            st.just(None),
            st.booleans(),
        ))
        return json.dumps(value)

    # --- Valid JSON objects with schema violations caught by parse() ---
    cfg = _valid_config()

    if kind == "missing_type":
        del cfg["type"]

    elif kind == "missing_name":
        del cfg["name"]

    elif kind == "missing_rendering":
        del cfg["rendering"]

    elif kind == "missing_events":
        del cfg["events"]

    elif kind == "rendering_not_object":
        # rendering is not a dict — use a non-dict JSON value
        cfg["rendering"] = draw(st.one_of(
            st.just("round"),
            st.just(42),
            st.just([1, 2]),
            st.just(True),
            st.just(None),
        ))

    elif kind == "rendering_missing_field":
        # Remove one of the required rendering sub-fields
        field_to_remove = draw(st.sampled_from(
            ["shape", "border", "shadow", "glow_on_hold"]
        ))
        del cfg["rendering"][field_to_remove]

    elif kind == "events_not_object":
        # events is not a dict
        cfg["events"] = draw(st.one_of(
            st.just("idle"),
            st.just(42),
            st.just(["idle"]),
            st.just(True),
            st.just(None),
        ))

    elif kind == "event_response_not_object":
        # An event response that is not a dict
        cfg["events"]["idle"] = draw(st.one_of(
            st.just("idle.webm"),
            st.just(42),
            st.just(["idle.webm"]),
            st.just(True),
            st.just(None),
        ))

    elif kind == "event_missing_animation":
        resp = dict(_VALID_EVENT_RESPONSE)
        del resp["animation"]
        cfg["events"]["idle"] = resp

    elif kind == "invalid_glow_color_length":
        # glow_color with wrong number of elements (0, 1, 2, 4, 5, 6)
        bad_len = draw(st.integers(min_value=0, max_value=6).filter(lambda n: n != 3))
        resp = dict(_VALID_EVENT_RESPONSE)
        resp["glow_color"] = [0] * bad_len
        cfg["events"]["idle"] = resp

    return json.dumps(cfg)


# ---------------------------------------------------------------------------
# Property test
# ---------------------------------------------------------------------------


@settings(max_examples=100)
@given(bad_input=invalid_json_strategy())
def test_invalid_json_produces_descriptive_error(bad_input: str) -> None:
    """**Validates: Requirements 2.2, 1.14**

    For any string that is not valid JSON or that is valid JSON but violates
    the SkinConfig schema, parse() should raise ValueError with a non-empty
    descriptive message.
    """
    try:
        parse(bad_input)
    except ValueError as exc:
        msg = str(exc)
        assert msg, "ValueError message must not be empty"
        assert len(msg) > 0, "ValueError message must be descriptive"
        return  # Expected path — error was raised

    # If we reach here, parse() did not raise — this should never happen
    # given our strategy only generates inputs that parse() rejects.
    assert False, (
        f"parse() did not raise ValueError for input that should be invalid:\n"
        f"{bad_input!r}"
    )
