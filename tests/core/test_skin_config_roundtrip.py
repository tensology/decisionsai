# Feature: oracle-skins-system, Property 1: SkinConfig round-trip parsing
# Validates: Requirements 2.4, 2.3, 2.1
"""Property-based test: serializing a SkinConfig to JSON via to_json() and
parsing it back via parse() produces an equivalent SkinConfig object."""

from hypothesis import given, settings
from hypothesis import strategies as st

from distr.core.skin_config import (
    EVENT_HOOKS,
    GLOW_STYLES,
    EventResponse,
    RenderingConfig,
    SkinConfig,
    parse,
    to_json,
)

# ---------------------------------------------------------------------------
# Hypothesis strategies
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
        alphabet=st.sampled_from("abcdefghijklmnopqrstuvwxyz ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_"),
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
        lambda keys: st.fixed_dictionaries(
            {k: _animation_strategy for k in keys}
        )
        if keys
        else st.just({})
    )

    @st.composite
    def _build(draw):
        t = draw(skin_type)
        # Rendering must match the skin type
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
def test_skin_config_round_trip(config: SkinConfig) -> None:
    """**Validates: Requirements 2.4, 2.3, 2.1**

    For any valid SkinConfig, to_json() then parse() produces an equivalent object.
    """
    json_str = to_json(config)
    restored = parse(json_str)

    # Top-level fields
    assert restored.type == config.type
    assert restored.name == config.name
    assert restored.transitions == config.transitions

    # Rendering
    assert restored.rendering.shape == config.rendering.shape
    assert restored.rendering.border == config.rendering.border
    assert restored.rendering.shadow == config.rendering.shadow
    assert restored.rendering.glow_on_hold == config.rendering.glow_on_hold

    # Events — same keys
    assert set(restored.events.keys()) == set(config.events.keys())

    # Each EventResponse field-by-field
    for hook in config.events:
        orig = config.events[hook]
        rest = restored.events[hook]
        assert rest.animation == orig.animation, f"{hook}.animation mismatch"
        assert rest.show_player == orig.show_player, f"{hook}.show_player mismatch"
        assert rest.show_chat_bubble == orig.show_chat_bubble, f"{hook}.show_chat_bubble mismatch"
        assert rest.glow == orig.glow, f"{hook}.glow mismatch"
        assert tuple(rest.glow_color) == tuple(orig.glow_color), f"{hook}.glow_color mismatch"
        assert rest.glow_speed == orig.glow_speed, f"{hook}.glow_speed mismatch"
        assert rest.glow_style == orig.glow_style, f"{hook}.glow_style mismatch"
        assert rest.tray_icon == orig.tray_icon, f"{hook}.tray_icon mismatch"
