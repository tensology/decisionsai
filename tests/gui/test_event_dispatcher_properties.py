# Feature: oracle-skins-system, Property 4, 5, 15: Event dispatcher properties
# Validates: Requirements 5.1, 5.10, 5.11, 5.12, 6.3
"""Property-based tests for EventHookDispatcher:
- Property 4: Event dispatch returns correct Event_Response
- Property 5: Transition lookup
- Property 15: TTS response reverts to previous state
"""

from hypothesis import given, settings
from hypothesis import strategies as st

from distr.core.skin_config import (
    EVENT_HOOKS,
    GLOW_STYLES,
    EventResponse,
    RenderingConfig,
    SkinConfig,
)
from distr.gui.oracle.event_dispatcher import EventHookDispatcher

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


_hook_strategy = st.sampled_from(EVENT_HOOKS)

_transition_key_strategy = st.tuples(
    st.sampled_from(EVENT_HOOKS),
    st.sampled_from(EVENT_HOOKS),
)


def skin_config_strategy() -> st.SearchStrategy[SkinConfig]:
    """Generate random valid SkinConfig objects with events and transitions."""
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

    transitions = st.dictionaries(
        keys=_transition_key_strategy.map(lambda pair: f"{pair[0]}-{pair[1]}"),
        values=_animation_strategy,
        max_size=15,
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
# Property 4: Event dispatch returns correct Event_Response
# ---------------------------------------------------------------------------


@settings(max_examples=100)
@given(config=skin_config_strategy(), hook=_hook_strategy)
def test_event_dispatch_returns_correct_response(config: SkinConfig, hook: str) -> None:
    """**Validates: Requirements 5.1, 5.10**

    For any valid SkinConfig and any Event_Hook string, the event dispatcher
    should return the corresponding Event_Response if defined, or None if not.
    The returned Event_Response must include all fields.
    """
    dispatcher = EventHookDispatcher()
    dispatcher.set_skin_config(config)

    response = dispatcher.get_event_response(hook)

    if hook in config.events:
        expected = config.events[hook]
        assert response is not None, (
            f"Expected Event_Response for hook '{hook}', got None"
        )
        # Verify all 8 fields match
        assert response.animation == expected.animation
        assert response.show_player == expected.show_player
        assert response.show_chat_bubble == expected.show_chat_bubble
        assert response.glow == expected.glow
        assert response.glow_color == expected.glow_color
        assert response.glow_speed == expected.glow_speed
        assert response.glow_style == expected.glow_style
        assert response.tray_icon == expected.tray_icon
    else:
        assert response is None, (
            f"Expected None for undefined hook '{hook}', got {response}"
        )


# ---------------------------------------------------------------------------
# Property 5: Transition lookup
# ---------------------------------------------------------------------------


@settings(max_examples=100)
@given(
    config=skin_config_strategy(),
    from_hook=_hook_strategy,
    to_hook=_hook_strategy,
)
def test_transition_lookup(config: SkinConfig, from_hook: str, to_hook: str) -> None:
    """**Validates: Requirements 5.11, 5.12**

    For any valid SkinConfig and any pair of Event_Hook strings, the transition
    lookup should return the animation filename if the key exists, or None.
    """
    dispatcher = EventHookDispatcher()
    dispatcher.set_skin_config(config)

    result = dispatcher.get_transition(from_hook, to_hook)
    key = f"{from_hook}-{to_hook}"

    if key in config.transitions:
        assert result == config.transitions[key], (
            f"Expected transition '{config.transitions[key]}' for key '{key}', "
            f"got '{result}'"
        )
    else:
        assert result is None, (
            f"Expected None for missing transition key '{key}', got '{result}'"
        )


# ---------------------------------------------------------------------------
# Property 15: TTS response reverts to previous state
# ---------------------------------------------------------------------------


@settings(max_examples=100)
@given(
    config=skin_config_strategy(),
    initial_hook=st.sampled_from([h for h in EVENT_HOOKS
                                  if h != "tts_response"
                                  and h not in ("ptt_active", "hands_free_listening", "dictation", "ticket_dictation")]),
)
def test_tts_response_reverts_to_previous_state(
    config: SkinConfig, initial_hook: str
) -> None:
    """**Validates: Requirements 6.3**

    For any sequence where the current hook is S, then tts_response fires,
    then TTS completes, the dispatcher should revert to S.
    """
    dispatcher = EventHookDispatcher()
    dispatcher.set_skin_config(config)

    # Set initial state
    dispatcher.fire_hook(initial_hook)
    assert dispatcher.get_current_hook() == initial_hook

    # Fire tts_response (temporary hook)
    dispatcher.fire_hook("tts_response")
    assert dispatcher.get_current_hook() == "tts_response"

    # Revert from tts_response — should go back to initial_hook
    dispatcher.revert_hook("tts_response")
    assert dispatcher.get_current_hook() == initial_hook, (
        f"Expected revert to '{initial_hook}' after tts_response, "
        f"got '{dispatcher.get_current_hook()}'"
    )


def test_repeated_hook_preserves_state_needed_for_revert() -> None:
    dispatcher = EventHookDispatcher()
    transitions = []
    dispatcher.event_hook_fired.connect(
        lambda current, previous: transitions.append((current, previous))
    )

    dispatcher.fire_hook("hands_free_listening", trigger="enable")
    dispatcher.fire_hook("hands_free_listening", trigger="stt_confirmation")
    dispatcher.revert_hook("hands_free_listening", trigger="disable")

    assert dispatcher.get_current_hook() == "idle"
    assert dispatcher.get_previous_hook() == "hands_free_listening"
    assert transitions == [
        ("hands_free_listening", "idle"),
        ("hands_free_listening", "hands_free_listening"),
        ("idle", "hands_free_listening"),
    ]
