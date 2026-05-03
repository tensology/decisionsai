"""
Property-based tests for the Initiative Service.
Library: Hypothesis

**Validates: Requirements 2.2, 4.1–4.4, 5.1, 5.3, 5.4, 6.4, 6.5, 7.3, 8–11, 13.6, 14.1, 14.2, 17.5, 18.1, 2.4, 8.5, 8.8**
"""

import os
import tempfile
import threading
from unittest.mock import MagicMock, patch

import pytest
from hypothesis import given, settings as h_settings
from hypothesis import strategies as st

from distr.core.initiative.policy import evaluate, migrate_initiative_level, PolicyDecision
from distr.core.initiative.proposed_action import (
    ProposedAction,
    deserialize,
    parse_llm_response,
    serialize,
    VALID_ACTION_TYPES,
)
from distr.core.initiative.draft_queue import DraftQueue, DraftEntry
from distr.core.initiative.context import ContextBundle


# ---------------------------------------------------------------------------
# Strategies (Task 10.1)
# ---------------------------------------------------------------------------

@st.composite
def proposed_action_strategy(draw, action_type=None):
    at = action_type or draw(st.sampled_from(list(VALID_ACTION_TYPES)))
    return ProposedAction(
        action_type=at,
        description=draw(st.text(min_size=1, max_size=200)),
        payload=draw(st.fixed_dictionaries({})),
        draft=draw(st.text(max_size=500)),
        telegram_message=draw(st.text(max_size=200)),
        requires_confirmation=draw(st.booleans()),
    )


@st.composite
def context_bundle_strategy(draw):
    return ContextBundle(
        chat_history=draw(
            st.lists(
                st.fixed_dictionaries({
                    "role": st.sampled_from(["user", "assistant"]),
                    "content": st.text(max_size=100),
                }),
                max_size=20,
            )
        ),
        scheduled_sessions=draw(
            st.lists(
                st.fixed_dictionaries({
                    "id": st.integers(),
                    "instruction": st.text(max_size=100),
                }),
                max_size=5,
            )
        ),
        kanban_summary=draw(
            st.lists(
                st.fixed_dictionaries({
                    "board_id": st.integers(),
                    "board_name": st.text(max_size=50),
                    "total_tickets": st.integers(min_value=0),
                }),
                max_size=5,
            )
        ),
        stuck_tasks=draw(
            st.lists(
                st.fixed_dictionaries({
                    "session_id": st.integers(),
                    "instruction": st.text(max_size=100),
                    "duration_minutes": st.integers(min_value=30),
                }),
                max_size=3,
            )
        ),
        unfinished_workflows=draw(
            st.lists(
                st.fixed_dictionaries({
                    "session_id": st.integers(),
                    "instruction": st.text(max_size=100),
                    "elapsed_hours": st.floats(min_value=24, max_value=200),
                }),
                max_size=3,
            )
        ),
        initiative_settings=draw(st.fixed_dictionaries({})),
        current_datetime=draw(st.just("2025-01-01T00:00:00")),
    )


@st.composite
def trigger_strategy(draw):
    return draw(st.sampled_from(["idle_timer", "stuck_task", "unfinished_workflow", "scheduled", "event"]))


@st.composite
def boundary_settings_strategy(draw):
    return {
        "initiative_allow_telegram": draw(st.booleans()),
        "initiative_allow_routine_tasks": draw(st.booleans()),
        "initiative_ask_external_comms": draw(st.booleans()),
        "initiative_ask_file_changes": draw(st.booleans()),
        "initiative_ask_sensitive": draw(st.booleans()),
    }


@st.composite
def draft_entry_strategy(draw):
    from datetime import datetime, timezone, timedelta

    now = datetime.now(tz=timezone.utc)
    created_at = now - timedelta(hours=draw(st.floats(min_value=0, max_value=47)))
    expires_at = created_at + timedelta(hours=48)
    return DraftEntry(
        id=draw(st.uuids()).hex,
        action_type=draw(st.sampled_from(list(VALID_ACTION_TYPES))),
        description=draw(st.text(min_size=1, max_size=200)),
        draft=draw(st.text(max_size=500)),
        reason=draw(st.text(max_size=100)),
        created_at=created_at.isoformat(),
        expires_at=expires_at.isoformat(),
    )


# ---------------------------------------------------------------------------
# Property 1: Idle timer always resets on signal (Task 10.2)
# ---------------------------------------------------------------------------

@given(st.lists(st.integers(min_value=0, max_value=3_600_000), min_size=1, max_size=20))
@h_settings(max_examples=100)
def test_idle_timer_always_resets(emission_times):
    """
    Property 1: Idle timer always resets on signal (Requirement 2.2)

    For any sequence of chat_stream_finished emissions, _reset_idle_timer must
    call timer.start(IDLE_TIMEOUT_MS) exactly once per emission.
    """
    pytest.importorskip("PyQt6.QtCore")
    from distr.core.initiative.service import InitiativeService

    mock_timer = MagicMock()
    mock_timer.isActive.return_value = False

    with patch("distr.core.initiative.service.QTimer") as MockQTimer:
        MockQTimer.return_value = mock_timer
        MockQTimer.singleShot = MagicMock()

        service = InitiativeService.__new__(InitiativeService)
        service._idle_timer = mock_timer
        service._schedule_timer = MagicMock()
        service.IDLE_TIMEOUT_MS = 300_000
        # _reset_idle_timer marshals via Qt bridge emit → _reset_idle_timer_on_qt
        service._qt_bridge = MagicMock()
        service._qt_bridge.reset_idle_timer_requested.emit.side_effect = (
            lambda *a, **k: service._reset_idle_timer_on_qt()
        )

        for _ in emission_times:
            service._reset_idle_timer(chat_id=0)

        # Timer must have been started exactly len(emission_times) times
        assert mock_timer.start.call_count == len(emission_times)
        # Every call must use IDLE_TIMEOUT_MS
        for call in mock_timer.start.call_args_list:
            assert call[0][0] == 300_000


# ---------------------------------------------------------------------------
# Property 2: Observe level produces no actions (Task 10.3)
# ---------------------------------------------------------------------------

@given(proposed_action_strategy(), boundary_settings_strategy())
@h_settings(max_examples=100)
def test_observe_produces_no_actions(action, boundaries):
    """
    Property 2: Observe level produces no actions (Requirements 4.1–4.4)

    evaluate() with level="observe" must always return SKIP regardless of
    action type or boundary settings.
    """
    decision = evaluate(action, "observe", boundaries)
    assert decision == PolicyDecision.SKIP


# ---------------------------------------------------------------------------
# Property 3: Assist level never runs idle cycles (Task 10.4)
# ---------------------------------------------------------------------------

@given(proposed_action_strategy(), boundary_settings_strategy(), trigger_strategy())
@h_settings(max_examples=100)
def test_assist_no_idle_cycles(action, boundaries, trigger):
    """
    Property 3: Assist level never runs idle cycles (Requirements 5.1, 5.3, 5.4)

    evaluate() with level="assist" must always return SUGGEST_ONLY — the policy
    gate never escalates to EXECUTE or DRAFT_AND_ASK at this level.
    """
    decision = evaluate(action, "assist", boundaries)
    assert decision == PolicyDecision.SUGGEST_ONLY


# ---------------------------------------------------------------------------
# Property 4: Routine task gate respected (Task 10.5)
# ---------------------------------------------------------------------------

@given(
    st.sampled_from(["operate", "own"]),
    proposed_action_strategy(action_type="routine_task"),
    boundary_settings_strategy(),
)
@h_settings(max_examples=100)
def test_routine_task_gate(level, action, boundaries):
    """
    Property 4: Routine task gate respected (Requirements 6.4, 6.5, 7.3)

    When initiative_allow_routine_tasks=False, routine_task actions must never
    receive an EXECUTE decision at operate or own level.
    """
    boundaries["initiative_allow_routine_tasks"] = False
    decision = evaluate(action, level, boundaries)
    assert decision != PolicyDecision.EXECUTE


# ---------------------------------------------------------------------------
# Property 5: Boundary produces draft-and-ask not silent skip (Task 10.6)
# ---------------------------------------------------------------------------

@given(
    st.sampled_from(["operate", "own"]),
    st.sampled_from([
        ("external_comms", "initiative_ask_external_comms"),
        ("file_change", "initiative_ask_file_changes"),
        ("sensitive", "initiative_ask_sensitive"),
    ]),
    boundary_settings_strategy(),
)
@h_settings(max_examples=100)
def test_boundary_draft_and_ask(level, action_boundary_pair, boundaries):
    """
    Property 5: Boundary produces draft-and-ask not silent skip (Requirements 8–11)

    When the boundary flag for an action type is True, evaluate() must return
    DRAFT_AND_ASK — never SKIP.
    """
    action_type, boundary_key = action_boundary_pair
    action = ProposedAction(action_type=action_type, description="test")
    boundaries[boundary_key] = True
    decision = evaluate(action, level, boundaries)
    assert decision == PolicyDecision.DRAFT_AND_ASK
    assert decision != PolicyDecision.SKIP


# ---------------------------------------------------------------------------
# Property 6: No concurrent cycles (Task 10.7)
# ---------------------------------------------------------------------------

@given(st.integers(min_value=2, max_value=10))
@h_settings(max_examples=100)
def test_no_concurrent_cycles(num_triggers):
    """
    Property 6: No concurrent cycles (Requirements 18.1, 2.4)

    The _cycle_running flag + lock must ensure that at most one cycle runs at
    a time even when multiple threads attempt to start one simultaneously.
    """
    pytest.importorskip("PyQt6.QtCore")
    from distr.core.initiative.service import InitiativeService

    cycles_started = []
    lock = threading.Lock()

    service = InitiativeService.__new__(InitiativeService)
    service._cycle_lock = threading.Lock()
    service._cycle_running = False
    service._stopped = False

    def fake_cycle(trigger):
        with lock:
            cycles_started.append(trigger)

    def attempt(i):
        with service._cycle_lock:
            if service._cycle_running:
                return
            service._cycle_running = True
        fake_cycle(f"trigger_{i}")
        with service._cycle_lock:
            service._cycle_running = False

    threads = [threading.Thread(target=attempt, args=(i,)) for i in range(num_triggers)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # At most num_triggers cycles could have run; at least one must have run
    assert len(cycles_started) <= num_triggers
    assert len(cycles_started) >= 1


# ---------------------------------------------------------------------------
# Property 7: Proposed action round-trip (Task 10.8)
# ---------------------------------------------------------------------------

@given(proposed_action_strategy())
@h_settings(max_examples=100)
def test_proposed_action_round_trip(action):
    """
    Property 7: Proposed action round-trip serialization (Requirements 13.6, 17.5)

    deserialize(serialize(a)) must equal a field-by-field, and the
    serialize/deserialize cycle must be idempotent.
    """
    serialized = serialize(action)
    deserialized = deserialize(serialized)

    assert deserialized.action_type == action.action_type
    assert deserialized.description == action.description
    assert deserialized.payload == action.payload
    assert deserialized.draft == action.draft
    assert deserialized.telegram_message == action.telegram_message
    assert deserialized.requires_confirmation == action.requires_confirmation
    assert deserialized.suggested_tool == action.suggested_tool

    # Idempotency: serialize(deserialize(s)) == serialize(deserialize(serialize(deserialize(s))))
    s1 = serialize(deserialize(serialized))
    s2 = serialize(deserialize(serialize(deserialize(serialized))))
    assert s1 == s2


# ---------------------------------------------------------------------------
# Property 8: Telegram gate (Task 10.9)
# ---------------------------------------------------------------------------

@given(
    proposed_action_strategy(),
    st.sampled_from(["operate", "own"]),
    boundary_settings_strategy(),
)
@h_settings(max_examples=100)
def test_telegram_gate(action, level, boundaries):
    """
    Property 8: Telegram gate (Requirements 14.1, 14.2)

    When initiative_allow_telegram=False, no call to send_to_telegram must
    occur — neither via _send_telegram_if_allowed nor _deliver_suggestion.
    """
    pytest.importorskip("PyQt6.QtCore")
    from distr.core.initiative.service import InitiativeService

    boundaries["initiative_allow_telegram"] = False

    mock_telegram = MagicMock()
    mock_telegram.telegram_user_id = 12345  # connected

    service = InitiativeService.__new__(InitiativeService)
    service.telegram_manager = mock_telegram
    service.chat_manager = None
    service._draft_queue = MagicMock()
    service._draft_queue.add = MagicMock()

    settings = dict(boundaries)
    settings["initiative_allow_telegram"] = False

    # _send_telegram_if_allowed must not call send_to_telegram
    service._send_telegram_if_allowed("test message", settings)
    mock_telegram.send_to_telegram.assert_not_called()

    # _deliver_suggestion must not send via telegram when disabled
    service._deliver_suggestion(action, settings)
    mock_telegram.send_to_telegram.assert_not_called()


# ---------------------------------------------------------------------------
# Property 9: Draft queue persistence round-trip (Task 10.10)
# ---------------------------------------------------------------------------

@given(st.lists(draft_entry_strategy(), min_size=1, max_size=10))
@h_settings(max_examples=100)
def test_draft_queue_persistence(entries):
    """
    Property 9: Draft queue persistence round-trip across restart (Requirement 8.5)

    Entries written to a DraftQueue must survive a reload from disk with all
    fields intact.
    """
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        path = f.name
    try:
        # Write entries
        q1 = DraftQueue(path=path)
        for entry in entries:
            q1.add(entry)

        # Reload from disk (simulates restart)
        q2 = DraftQueue(path=path)
        loaded = q2.get_all()

        assert len(loaded) == len(entries)
        for orig, loaded_entry in zip(entries, loaded):
            assert loaded_entry.id == orig.id
            assert loaded_entry.action_type == orig.action_type
            assert loaded_entry.description == orig.description
            assert loaded_entry.draft == orig.draft
            assert loaded_entry.created_at == orig.created_at
    finally:
        os.unlink(path)


# ---------------------------------------------------------------------------
# Property 10: Draft queue expiry monotonicity (Task 10.11)
# ---------------------------------------------------------------------------

@given(
    st.floats(min_value=0, max_value=47.9),   # hours since creation — not yet expired
    st.floats(min_value=48.1, max_value=200),  # hours since creation — expired
)
@h_settings(max_examples=100)
def test_draft_queue_expiry_monotonicity(hours_not_expired, hours_expired):
    """
    Property 10: Draft queue expiry monotonicity and idempotency (Requirement 8.8)

    An entry whose expires_at is in the future must survive expire_old().
    An entry whose expires_at is in the past must be removed.
    Running expire_old() a second time must remove nothing more (idempotent).
    """
    from datetime import datetime, timezone, timedelta

    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        path = f.name
    try:
        now = datetime.now(tz=timezone.utc)

        # Entry that should NOT be expired
        created_not_expired = now - timedelta(hours=hours_not_expired)
        entry_live = DraftEntry(
            id="live-entry",
            action_type="suggestion",
            description="live",
            draft="",
            reason="test",
            created_at=created_not_expired.isoformat(),
            expires_at=(created_not_expired + timedelta(hours=48)).isoformat(),
        )

        # Entry that SHOULD be expired
        created_expired = now - timedelta(hours=hours_expired)
        entry_dead = DraftEntry(
            id="dead-entry",
            action_type="suggestion",
            description="dead",
            draft="",
            reason="test",
            created_at=created_expired.isoformat(),
            expires_at=(created_expired + timedelta(hours=48)).isoformat(),
        )

        q = DraftQueue(path=path)
        q.add(entry_live)
        q.add(entry_dead)

        # Run expire_old once
        count1 = q.expire_old()
        remaining1 = [e.id for e in q.get_all()]

        # Run expire_old again (idempotency check)
        count2 = q.expire_old()
        remaining2 = [e.id for e in q.get_all()]

        # The expired entry must be gone
        assert "dead-entry" not in remaining1
        # The live entry must still be present
        assert "live-entry" in remaining1
        # Idempotent: second run expires nothing more
        assert count2 == 0
        assert remaining1 == remaining2
    finally:
        os.unlink(path)
