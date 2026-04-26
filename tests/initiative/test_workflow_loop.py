"""
Workflow loop integration tests.

Simulates the core operator loop without Qt or a real DB:
  ticket enters "Current" lane
  → initiative cycle fires
  → context assembled
  → LLM proposes action
  → policy gate evaluates
  → dispatch: step runner / kanban / telegram / draft queue

All external dependencies (DB, LLM, Telegram, Qt) are mocked.

Run with:
    python tests/initiative/test_workflow_loop.py
"""
import sys
import os
import json
import tempfile
import threading
import uuid
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch, call
from dataclasses import dataclass, field

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../../")))

from distr.core.initiative.policy import PolicyDecision, evaluate
from distr.core.initiative.proposed_action import (
    ProposedAction,
    deserialize,
    parse_llm_response,
    serialize,
)
from distr.core.initiative.draft_queue import DraftQueue, DraftEntry
from distr.core.initiative.context import ContextAssembler, ContextBundle

PASS = "✅"
FAIL = "❌"
_failures = []


def check(label: str, condition: bool, detail: str = ""):
    if condition:
        print(f"  {PASS} {label}")
    else:
        msg = f"  {FAIL} {label}" + (f" — {detail}" if detail else "")
        print(msg)
        _failures.append(label)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_draft_entry(hours_until_expiry: float = 48.0) -> DraftEntry:
    now = datetime.now(tz=timezone.utc)
    return DraftEntry(
        id=str(uuid.uuid4()),
        action_type="routine_task",
        description="Test",
        draft="",
        reason="test",
        created_at=now.isoformat(),
        expires_at=(now + timedelta(hours=hours_until_expiry)).isoformat(),
    )


def _tmp_queue() -> tuple[DraftQueue, str]:
    fd, path = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    os.unlink(path)
    return DraftQueue(path=path), path


def _make_settings(level: str = "operate", **overrides) -> dict:
    base = {
        "initiative_level": level,
        "initiative_allow_telegram": True,
        "initiative_allow_routine_tasks": True,
        "initiative_ask_external_comms": False,
        "initiative_ask_file_changes": False,
        "initiative_ask_sensitive": False,
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Simulated dispatch — mirrors what InitiativeService._dispatch_action does
# without requiring Qt or a real DB session.
# ---------------------------------------------------------------------------

def simulate_dispatch(action: ProposedAction, settings: dict, draft_queue: DraftQueue,
                      telegram_mock: MagicMock) -> dict:
    """
    Evaluate policy and route the action.
    Returns a dict describing what happened: {dispatched, drafted, telegram_sent, skipped, suggested}.
    """
    level = settings.get("initiative_level", "observe")
    boundaries = {k: v for k, v in settings.items() if k.startswith("initiative_")}
    decision = evaluate(action, level, boundaries)

    result = dict(dispatched=False, drafted=False, telegram_sent=False, skipped=False, suggested=False)

    if decision == PolicyDecision.SKIP:
        result["skipped"] = True

    elif decision == PolicyDecision.SUGGEST_ONLY:
        result["suggested"] = True

    elif decision == PolicyDecision.EXECUTE:
        result["dispatched"] = True
        # Telegram notification if allowed
        if settings.get("initiative_allow_telegram") and action.telegram_message:
            telegram_mock(action.telegram_message)
            result["telegram_sent"] = True

    elif decision == PolicyDecision.DRAFT_AND_ASK:
        entry = DraftEntry(
            id=str(uuid.uuid4()),
            action_type=action.action_type,
            description=action.description,
            draft=action.draft,
            reason="boundary gate",
            created_at=datetime.now(tz=timezone.utc).isoformat(),
            expires_at=(datetime.now(tz=timezone.utc) + timedelta(hours=48)).isoformat(),
        )
        draft_queue.add(entry)
        result["drafted"] = True
        if settings.get("initiative_allow_telegram"):
            telegram_mock(f"[Initiative] Draft queued: {action.description}")
            result["telegram_sent"] = True

    return result


# ---------------------------------------------------------------------------
# 1. Ticket enters "Current" lane → routine_task dispatched at operate level
# ---------------------------------------------------------------------------

def test_ticket_to_dispatch():
    print("\n[1] ticket enters Current → routine_task dispatched (operate)")
    q, path = _tmp_queue()
    telegram = MagicMock()
    try:
        action = ProposedAction(
            action_type="routine_task",
            description="Process ticket: Fix login bug",
            payload={"runner_type": "kanban", "board_id": 1},
            telegram_message="[Initiative] Starting kanban check-in",
        )
        settings = _make_settings("operate", initiative_allow_routine_tasks=True)
        result = simulate_dispatch(action, settings, q, telegram)

        check("dispatched", result["dispatched"])
        check("not drafted", not result["drafted"])
        check("telegram sent", result["telegram_sent"])
        check("draft queue empty", len(q.get_all()) == 0)
    finally:
        if os.path.exists(path):
            os.unlink(path)


# ---------------------------------------------------------------------------
# 2. observe level — nothing happens regardless of ticket
# ---------------------------------------------------------------------------

def test_observe_no_dispatch():
    print("\n[2] observe level — no dispatch, no draft, no telegram")
    q, path = _tmp_queue()
    telegram = MagicMock()
    try:
        for at in ["routine_task", "suggestion", "external_comms", "file_change", "sensitive"]:
            action = ProposedAction(action_type=at, description="test", telegram_message="msg")
            settings = _make_settings("observe")
            result = simulate_dispatch(action, settings, q, telegram)
            check(f"observe/{at} → skipped", result["skipped"])
            check(f"observe/{at} → no telegram", not result["telegram_sent"])

        check("draft queue still empty", len(q.get_all()) == 0)
        telegram.assert_not_called()
    finally:
        if os.path.exists(path):
            os.unlink(path)


# ---------------------------------------------------------------------------
# 3. Boundary gate → draft queued, not executed
# ---------------------------------------------------------------------------

def test_boundary_produces_draft():
    print("\n[3] boundary gate → draft queued, not executed")
    q, path = _tmp_queue()
    telegram = MagicMock()
    try:
        action = ProposedAction(
            action_type="external_comms",
            description="Send weekly summary to client",
            draft="Hi client, here is your weekly summary...",
            telegram_message="[Initiative] Draft ready for review",
        )
        settings = _make_settings("operate", initiative_ask_external_comms=True)
        result = simulate_dispatch(action, settings, q, telegram)

        check("not dispatched", not result["dispatched"])
        check("drafted", result["drafted"])
        check("draft queue has 1 entry", len(q.get_all()) == 1)
        check("draft content preserved", q.get_all()[0].draft == action.draft)
    finally:
        if os.path.exists(path):
            os.unlink(path)


# ---------------------------------------------------------------------------
# 4. Telegram gate — allow_telegram=False → no telegram calls
# ---------------------------------------------------------------------------

def test_telegram_gate():
    print("\n[4] telegram gate — allow_telegram=False → zero telegram calls")
    q, path = _tmp_queue()
    telegram = MagicMock()
    try:
        for at in ["routine_task", "suggestion", "external_comms"]:
            action = ProposedAction(
                action_type=at,
                description="test",
                telegram_message="[Initiative] should not send",
            )
            settings = _make_settings(
                "operate",
                initiative_allow_telegram=False,
                initiative_allow_routine_tasks=True,
                initiative_ask_external_comms=True,
            )
            simulate_dispatch(action, settings, q, telegram)

        telegram.assert_not_called()
        check("zero telegram calls", not telegram.called)
    finally:
        if os.path.exists(path):
            os.unlink(path)


# ---------------------------------------------------------------------------
# 5. No concurrent cycles — lock prevents re-entrancy
# ---------------------------------------------------------------------------

def test_no_concurrent_cycles():
    print("\n[5] no concurrent cycles — lock prevents re-entrancy")
    lock = threading.Lock()
    cycle_count = [0]
    max_concurrent = [0]
    active = [0]
    errors = []

    def run_cycle():
        acquired = lock.acquire(blocking=False)
        if not acquired:
            return  # already running
        try:
            active[0] += 1
            if active[0] > max_concurrent[0]:
                max_concurrent[0] = active[0]
            cycle_count[0] += 1
            import time
            time.sleep(0.05)  # simulate work
        finally:
            active[0] -= 1
            lock.release()

    threads = [threading.Thread(target=run_cycle) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    check("max concurrent cycles == 1", max_concurrent[0] <= 1, f"got {max_concurrent[0]}")
    check("at least one cycle ran", cycle_count[0] >= 1)


# ---------------------------------------------------------------------------
# 6. LLM response → full dispatch pipeline
# ---------------------------------------------------------------------------

def test_llm_response_to_dispatch():
    print("\n[6] LLM response → parse → policy → dispatch")
    q, path = _tmp_queue()
    telegram = MagicMock()
    try:
        llm_raw = json.dumps({
            "action_type": "routine_task",
            "description": "Run daily kanban check-in for Dev board",
            "payload": {"runner_type": "kanban", "board_id": 2},
            "draft": "",
            "telegram_message": "[Initiative] Starting daily kanban check-in",
            "requires_confirmation": False,
        })
        action = parse_llm_response(llm_raw)
        settings = _make_settings("operate", initiative_allow_routine_tasks=True)
        result = simulate_dispatch(action, settings, q, telegram)

        check("parsed correctly", action.action_type == "routine_task")
        check("dispatched", result["dispatched"])
        check("telegram sent", result["telegram_sent"])
    finally:
        if os.path.exists(path):
            os.unlink(path)


# ---------------------------------------------------------------------------
# 7. Draft queue persists across simulated restart
# ---------------------------------------------------------------------------

def test_draft_survives_restart():
    print("\n[7] draft queue persists across restart")
    fd, path = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    os.unlink(path)
    telegram = MagicMock()
    try:
        q1 = DraftQueue(path=path)
        action = ProposedAction(
            action_type="file_change",
            description="Update deployment config",
            draft="# new config",
            telegram_message="",
        )
        settings = _make_settings("operate", initiative_ask_file_changes=True,
                                  initiative_allow_telegram=False)
        simulate_dispatch(action, settings, q1, telegram)

        # Simulate restart
        q2 = DraftQueue(path=path)
        entries = q2.get_all()
        check("draft survives restart", len(entries) == 1, f"got {len(entries)}")
        check("description preserved", entries[0].description == action.description)
        check("draft content preserved", entries[0].draft == action.draft)
    finally:
        if os.path.exists(path):
            os.unlink(path)


# ---------------------------------------------------------------------------
# 8. Context assembler — mocked DB, no real connections needed
# ---------------------------------------------------------------------------

def test_context_assembler_with_mocks():
    print("\n[8] context assembler with mocked DB")
    assembler = ContextAssembler()
    settings = _make_settings("operate")

    mock_sessions = [{"id": 1, "instruction": "daily report", "enabled": True}]
    mock_kanban = [{"board_id": 1, "board_name": "Dev", "total_tickets": 5, "overdue_tickets": 1, "lanes": []}]
    mock_stuck = [{"session_id": 2, "instruction": "stuck task", "duration_minutes": 45}]
    mock_unfinished = []

    with patch.object(assembler, "_fetch_chat_history", return_value=[{"role": "user", "content": "hello"}]), \
         patch.object(assembler, "_fetch_scheduled_sessions", return_value=mock_sessions), \
         patch.object(assembler, "_fetch_kanban_summary", return_value=mock_kanban), \
         patch.object(assembler, "_fetch_stuck_tasks", return_value=mock_stuck), \
         patch.object(assembler, "_fetch_unfinished_workflows", return_value=mock_unfinished):

        bundle = assembler.build(settings)

    check("chat_history populated", len(bundle.chat_history) == 1)
    check("scheduled_sessions populated", len(bundle.scheduled_sessions) == 1)
    check("kanban_summary populated", len(bundle.kanban_summary) == 1)
    check("stuck_tasks populated", len(bundle.stuck_tasks) == 1)
    check("unfinished_workflows empty", len(bundle.unfinished_workflows) == 0)
    check("initiative_settings present", bundle.initiative_settings == settings)
    check("current_datetime is string", isinstance(bundle.current_datetime, str))


# ---------------------------------------------------------------------------
# 9. Context assembler — partial failure falls back gracefully
# ---------------------------------------------------------------------------

def test_context_assembler_partial_failure():
    print("\n[9] context assembler — partial DB failure → graceful fallback")
    assembler = ContextAssembler()
    settings = _make_settings("operate")

    with patch.object(assembler, "_fetch_chat_history", side_effect=Exception("DB down")), \
         patch.object(assembler, "_fetch_scheduled_sessions", return_value=[]), \
         patch.object(assembler, "_fetch_kanban_summary", side_effect=Exception("timeout")), \
         patch.object(assembler, "_fetch_stuck_tasks", return_value=[]), \
         patch.object(assembler, "_fetch_unfinished_workflows", return_value=[]):

        bundle = assembler.build(settings)

    check("chat_history empty on failure", bundle.chat_history == [])
    check("kanban_summary empty on failure", bundle.kanban_summary == [])
    check("bundle still returned", bundle is not None)


# ---------------------------------------------------------------------------
# 10. assist level — suggestions only, no execution
# ---------------------------------------------------------------------------

def test_assist_suggest_only():
    print("\n[10] assist level — all actions become suggestions")
    q, path = _tmp_queue()
    telegram = MagicMock()
    try:
        for at in ["routine_task", "external_comms", "file_change", "sensitive"]:
            action = ProposedAction(action_type=at, description="test", telegram_message="msg")
            settings = _make_settings("assist")
            result = simulate_dispatch(action, settings, q, telegram)
            check(f"assist/{at} → suggested", result["suggested"])
            check(f"assist/{at} → not dispatched", not result["dispatched"])

        check("draft queue empty", len(q.get_all()) == 0)
        telegram.assert_not_called()
    finally:
        if os.path.exists(path):
            os.unlink(path)


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    test_ticket_to_dispatch()
    test_observe_no_dispatch()
    test_boundary_produces_draft()
    test_telegram_gate()
    test_no_concurrent_cycles()
    test_llm_response_to_dispatch()
    test_draft_survives_restart()
    test_context_assembler_with_mocks()
    test_context_assembler_partial_failure()
    test_assist_suggest_only()

    print()
    if _failures:
        print(f"❌ {len(_failures)} failure(s): {_failures}")
        sys.exit(1)
    else:
        print("✅ All workflow loop tests passed")
