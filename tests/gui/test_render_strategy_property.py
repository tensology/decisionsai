# Feature: oracle-skins-system, Property 14: Render strategy selection matches skin type
# Validates: Requirements 3.1, 3.2, 3.3, 3.6, 4.1
"""Property-based test: for any valid SkinConfig, create_renderer returns
OracleRenderer when type is "oracle" and AvatarRenderer when type is "avatar"."""

from hypothesis import given, settings
from hypothesis import strategies as st

from distr.core.skin_config import (
    EVENT_HOOKS,
    GLOW_STYLES,
    EventResponse,
    RenderingConfig,
    SkinConfig,
)
from distr.gui.oracle.render_strategy import (
    AvatarRenderer,
    OracleRenderer,
    create_renderer,
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

    other_hooks = [h for h in EVENT_HOOKS if h != "idle"]
    events = st.fixed_dictionaries(
        {"idle": event_response_strategy()},
        optional={h: event_response_strategy() for h in other_hooks},
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
            transitions={},
        )

    return _build()


# ---------------------------------------------------------------------------
# Property test
# ---------------------------------------------------------------------------


@settings(max_examples=100)
@given(config=skin_config_strategy())
def test_render_strategy_matches_skin_type(config: SkinConfig) -> None:
    """**Validates: Requirements 3.1, 3.2, 3.3, 3.6, 4.1**

    For any valid SkinConfig, create_renderer returns OracleRenderer when
    type is "oracle" (round shape, border, shadow, glow-on-hold) and
    AvatarRenderer when type is "avatar" (square shape, no border/shadow).
    """
    renderer = create_renderer(config)

    if config.type == "oracle":
        assert isinstance(renderer, OracleRenderer), (
            f"Expected OracleRenderer for type 'oracle', got {type(renderer).__name__}"
        )
        assert renderer.border == config.rendering.border
        assert renderer.shadow == config.rendering.shadow
        assert renderer.glow_on_hold == config.rendering.glow_on_hold
    else:
        assert isinstance(renderer, AvatarRenderer), (
            f"Expected AvatarRenderer for type 'avatar', got {type(renderer).__name__}"
        )
