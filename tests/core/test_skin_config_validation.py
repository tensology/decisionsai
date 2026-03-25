# Feature: oracle-skins-system, Property 2: SkinConfig structural validation
# Validates: Requirements 1.2, 1.3, 1.4, 1.5, 1.6, 1.7, 1.8, 1.9, 1.10, 1.11, 1.12, 1.13, 12.11
"""Property-based test: for any valid SkinConfig object, validate() returns
an empty list — confirming that all structural constraints hold."""

from hypothesis import given, settings
from hypothesis import strategies as st

from distr.core.skin_config import (
    EVENT_HOOKS,
    GLOW_STYLES,
    EventResponse,
    RenderingConfig,
    SkinConfig,
    validate,
)

# ---------------------------------------------------------------------------
# Hypothesis strategies (reused from test_skin_config_roundtrip.py)
# ---------------------------------------------------------------------------

_animation_strategy = st.text(
    alphabet=st.sampled_from("abcdefghijklmnopqrstuvwxyz0123456789-_."),
    min_size=1,
    max_size=30,
).filter(lambda s: s.strip() != "")

_tray_icon_strategy = st.text(
    alphabet=st.sampled_from("abcdefghijklmnopqrstuvwxyz0123456789-_."),
    min_size=1,
    max_size=20,
).filter(lambda s: s.strip() != "")


def event_response_strategy() -> st.SearchStrategy[EventResponse]:
    """Generate random EventResponse objects with all 8 fields (valid values)."""
    return st.builds(
        EventResponse,
        animation=_animation_strategy,
        show_player=st.booleans(),
        show_chat_bubble=st.booleans(),
        glow=st.booleans(),
        glow_color=st.tuples(
            st.integers(min_value=0, max_value=255),
            st.integers(min_value=0, max_value=255),
            st.integers(min_value=0, max_value=255),
        ),
        glow_speed=st.integers(min_value=1, max_value=10000),
        glow_style=st.sampled_from(GLOW_STYLES),
        tray_icon=_tray_icon_strategy,
    )


def skin_config_strategy() -> st.SearchStrategy[SkinConfig]:
    """Generate random valid SkinConfig objects."""
    skin_type = st.sampled_from(["oracle", "avatar"])

    name = st.text(
        alphabet=st.sampled_from(
            "abcdefghijklmnopqrstuvwxyz ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_"
        ),
        min_size=1,
        max_size=30,
    ).filter(lambda s: s.strip() != "")

    # Events: always include "idle", plus a random subset of other hooks
    other_hooks = [h for h in EVENT_HOOKS if h != "idle"]
    events = st.fixed_dictionaries(
        {"idle": event_response_strategy()},
        optional={h: event_response_strategy() for h in other_hooks},
    )

    # Transitions: optional dict mapping "hook1-hook2" keys to animation filenames
    transition_keys = st.lists(
        st.tuples(
            st.sampled_from(EVENT_HOOKS),
            st.sampled_from(EVENT_HOOKS),
        ).map(lambda pair: f"{pair[0]}-{pair[1]}"),
        max_size=5,
        unique=True,
    )
    transitions = transition_keys.flatmap(
        lambda keys: st.fixed_dictionaries({k: _animation_strategy for k in keys})
        if keys
        else st.just({})
    )

    @st.composite
    def _build(draw):
        t = draw(skin_type)
        if t == "oracle":
            rendering = RenderingConfig(
                shape="round", border=True, shadow=True, glow_on_hold=True
            )
        else:
            rendering = RenderingConfig(
                shape="square", border=False, shadow=False, glow_on_hold=False
            )
        return SkinConfig(
            type=t,
            name=draw(name),
            rendering=rendering,
            events=draw(events),
            transitions=draw(transitions),
        )

    return _build()


# ---------------------------------------------------------------------------
# Property test
# ---------------------------------------------------------------------------


@settings(max_examples=100)
@given(config=skin_config_strategy())
def test_skin_config_structural_validation(config: SkinConfig) -> None:
    """**Validates: Requirements 1.2, 1.3, 1.4, 1.5, 1.6, 1.7, 1.8, 1.9, 1.10, 1.11, 1.12, 1.13, 12.11**

    For any valid SkinConfig object, validate() returns an empty list,
    confirming all structural constraints hold:
    - type is "oracle" or "avatar"
    - name is a non-empty string
    - events contains an "idle" key
    - Every EventResponse has all 8 fields with correct types
    - glow_color is a 3-element tuple of ints 0-255
    - glow_speed is a positive integer
    - glow_style is one of "breathing", "pulse", "fade", "flash"
    - tray_icon is a non-empty string
    - oracle rendering: shape="round", border=True, shadow=True, glow_on_hold=True
    - avatar rendering: shape="square", border=False, shadow=False, glow_on_hold=False
    """
    errors = validate(config)
    assert errors == [], f"Expected no validation errors for valid config, got: {errors}"
