"""Thread timer segments end at the first idle point, not at the latest reply days later."""
from __future__ import annotations

from datetime import datetime, timedelta

from distr.core.db import Chat
from distr.core.db.workflow import DevelopmentTimeEntry, DevelopmentWorkItem
from distr.core.workflow import development_control as control
from distr.core.workflow.thread_time_bounds import settled_segment_end
from tests.core.test_development_control import development_db  # noqa: F401 - shared fixture

START = datetime(2026, 11, 4, 8, 7, 6)          # 10:07:06 SAST, naive UTC
DAILY = [(START + timedelta(days=d, hours=23 - 1, minutes=-5), START + timedelta(days=d, hours=23 - 1, minutes=-3))
         for d in range(1, 8)]                   # one automation turn each later morning


def test_daily_runs_do_not_extend_the_first_segment():
    turns = [(START, START + timedelta(seconds=1)), *DAILY]
    end = settled_segment_end(START, turns, now=START + timedelta(days=7), grace_seconds=2)
    assert end == START + timedelta(seconds=3)


def test_a_long_agent_turn_is_kept_whole():
    turns = [(START, START + timedelta(minutes=45))]
    end = settled_segment_end(START, turns, now=START + timedelta(hours=3), grace_seconds=2)
    assert end == START + timedelta(minutes=45, seconds=2)


def test_follow_up_prompt_inside_the_idle_gap_continues_the_segment():
    turns = [(START, START + timedelta(minutes=45)), (START + timedelta(minutes=55), START + timedelta(minutes=85))]
    end = settled_segment_end(START, turns, now=START + timedelta(hours=3), grace_seconds=0)
    assert end == START + timedelta(minutes=85)


def test_follow_up_after_a_long_idle_gap_is_not_the_same_segment():
    turns = [(START, START + timedelta(minutes=10)), (START + timedelta(hours=2), START + timedelta(hours=2, minutes=5))]
    end = settled_segment_end(START, turns, now=START + timedelta(hours=3), grace_seconds=0)
    assert end == START + timedelta(minutes=10)


def test_a_row_touched_much_later_does_not_stretch_its_turn():
    turns = [(START, START + timedelta(days=2))]
    end = settled_segment_end(START, turns, now=START + timedelta(days=3), grace_seconds=0)
    assert end == START


def test_never_past_now():
    end = settled_segment_end(START, [(START, START + timedelta(minutes=30))], now=START + timedelta(minutes=10))
    assert end == START + timedelta(minutes=10)


def _running_since(session, chat_id, started, last_activity=None):
    with session() as db:
        item = db.query(DevelopmentWorkItem).filter_by(chat_id=chat_id).one()
        item.time_paused = False
        item.time_started_at = started
        item.time_last_activity_at = last_activity or started
        item.time_accumulated_seconds = 0
        db.commit()


def _turn(session, chat_id, prompt_at, reply_at):
    with session() as db:
        row = Chat(parent_id=chat_id, input="Run the daily check", response="Done.",
                   created_date=prompt_at, modified_date=reply_at)
        db.add(row)
        db.commit()
        # onupdate must not move the reply stamp while seeding
        db.query(Chat).filter_by(id=row.id).update({"modified_date": reply_at}, synchronize_session=False)
        db.commit()


def test_unpolled_automation_thread_auto_pauses_at_its_first_reply(development_db, monkeypatch):  # noqa: F811
    session, chat_id = development_db["session"], development_db["chat_id"]
    now = START + timedelta(days=8)
    monkeypatch.setattr(control, "utc_now_naive", lambda: now)
    monkeypatch.setattr("distr.core.workflow.development_harness.development_execution_active", lambda _: False)
    _running_since(session, chat_id, START, last_activity=DAILY[-1][0])
    _turn(session, chat_id, START, START + timedelta(seconds=1))
    for prompt_at, reply_at in DAILY:
        _turn(session, chat_id, prompt_at, reply_at)
    state = control.thread_time_state(chat_id)
    assert state["paused"] is True
    with session() as db:
        rows = db.query(DevelopmentTimeEntry).filter_by(chat_id=chat_id).all()
    assert [(r.source, r.seconds) for r in rows] == [("auto_pause", 3)]
    assert rows[0].ended_at == START + timedelta(seconds=3)


def test_next_run_on_a_stale_running_timer_starts_a_new_segment(development_db, monkeypatch):  # noqa: F811
    session, chat_id = development_db["session"], development_db["chat_id"]
    now = DAILY[0][0]
    monkeypatch.setattr(control, "utc_now_naive", lambda: now)
    _running_since(session, chat_id, START)
    _turn(session, chat_id, START, START + timedelta(seconds=1))
    control.resume_thread_time(chat_id, _session_provider=session)
    with session() as db:
        rows = db.query(DevelopmentTimeEntry).filter_by(chat_id=chat_id).all()
        item = db.query(DevelopmentWorkItem).filter_by(chat_id=chat_id).one()
    assert [(r.source, r.started_at, r.ended_at) for r in rows] == [
        ("auto_pause", START, START + timedelta(seconds=3))]
    assert item.time_started_at == now and item.time_paused is False


def test_resume_inside_continuous_work_keeps_one_segment(development_db, monkeypatch):  # noqa: F811
    session, chat_id = development_db["session"], development_db["chat_id"]
    now = START + timedelta(minutes=12)
    monkeypatch.setattr(control, "utc_now_naive", lambda: now)
    _running_since(session, chat_id, START)
    _turn(session, chat_id, START, START + timedelta(minutes=10))
    control.resume_thread_time(chat_id, _session_provider=session)
    with session() as db:
        assert db.query(DevelopmentTimeEntry).filter_by(chat_id=chat_id).count() == 0
        assert db.query(DevelopmentWorkItem).filter_by(chat_id=chat_id).one().time_started_at == START


# ── Finished automation runs and the background sweep ─────────────────────────
import json as _json  # noqa: E402

from distr.core.db.automation import AutomationRun  # noqa: E402,F401 - registers the table
from distr.core.db.workflow import AutoWorkflowRun  # noqa: E402


def _idle_agent(monkeypatch, active=False):
    monkeypatch.setattr(
        "distr.core.workflow.development_harness.development_execution_active", lambda _: active
    )


def _entries(session, chat_id):
    with session() as db:
        return [(r.source, r.started_at, r.ended_at, r.seconds)
                for r in db.query(DevelopmentTimeEntry).filter_by(chat_id=chat_id).all()]


def _item(session, chat_id):
    with session() as db:
        return db.query(DevelopmentWorkItem).filter_by(chat_id=chat_id).one()


def test_sweep_pauses_an_unattended_finished_automation_timer_at_its_work_end(development_db, monkeypatch):  # noqa: F811
    session, chat_id = development_db["session"], development_db["chat_id"]
    now = START + timedelta(hours=7)               # nobody opened the thread for hours
    monkeypatch.setattr(control, "utc_now_naive", lambda: now)
    _idle_agent(monkeypatch)
    _running_since(session, chat_id, START)
    _turn(session, chat_id, START, START + timedelta(minutes=4))
    assert control.sweep_idle_thread_timers(_session_provider=session) == [chat_id]
    end = START + timedelta(minutes=4, seconds=2)
    assert _entries(session, chat_id) == [("auto_pause", START, end, 242)]
    item = _item(session, chat_id)
    assert item.time_paused is True and item.time_started_at is None


def test_sweep_pauses_a_tool_run_timer_with_no_reply_turn(development_db, monkeypatch):  # noqa: F811
    session, chat_id = development_db["session"], development_db["chat_id"]
    now = START + timedelta(hours=6, minutes=43)
    monkeypatch.setattr(control, "utc_now_naive", lambda: now)
    _idle_agent(monkeypatch)
    _running_since(session, chat_id, START)        # tool runs post to the root, no child turn
    assert control.sweep_idle_thread_timers(_session_provider=session) == [chat_id]
    assert _entries(session, chat_id) == [("auto_pause", START, START + timedelta(seconds=2), 2)]


def test_sweep_leaves_a_live_agent_alone(development_db, monkeypatch):  # noqa: F811
    session, chat_id = development_db["session"], development_db["chat_id"]
    monkeypatch.setattr(control, "utc_now_naive", lambda: START + timedelta(hours=2))
    _idle_agent(monkeypatch, active=True)
    _running_since(session, chat_id, START)
    assert control.sweep_idle_thread_timers(_session_provider=session) == []
    assert _item(session, chat_id).time_paused is False


def test_sweep_leaves_a_live_workflow_run_alone(development_db, monkeypatch):  # noqa: F811
    session, chat_id = development_db["session"], development_db["chat_id"]
    monkeypatch.setattr(control, "utc_now_naive", lambda: START + timedelta(hours=2))
    _idle_agent(monkeypatch)
    _running_since(session, chat_id, START)
    with session() as db:
        db.add(AutoWorkflowRun(workflow_id=1, chat_id=chat_id, status="waiting", started_at=START))
        db.commit()
    assert control.sweep_idle_thread_timers(_session_provider=session) == []
    assert _item(session, chat_id).time_paused is False


def test_sweep_does_not_race_a_run_that_just_started(development_db, monkeypatch):  # noqa: F811
    session, chat_id = development_db["session"], development_db["chat_id"]
    monkeypatch.setattr(control, "utc_now_naive", lambda: START + timedelta(minutes=1))
    _idle_agent(monkeypatch)
    _running_since(session, chat_id, START)
    assert control.sweep_idle_thread_timers(_session_provider=session) == []
    assert _item(session, chat_id).time_paused is False


def test_sweep_skips_when_execution_state_is_unreadable(development_db, monkeypatch):  # noqa: F811
    session, chat_id = development_db["session"], development_db["chat_id"]
    monkeypatch.setattr(control, "utc_now_naive", lambda: START + timedelta(hours=2))

    def boom(_):
        raise RuntimeError("state unavailable")

    monkeypatch.setattr("distr.core.workflow.development_harness.development_execution_active", boom)
    _running_since(session, chat_id, START)
    assert control.sweep_idle_thread_timers(_session_provider=session) == []


def test_finished_run_settles_the_timer_at_its_finish_time(development_db, monkeypatch):  # noqa: F811
    session, chat_id = development_db["session"], development_db["chat_id"]
    now = START + timedelta(seconds=30)
    monkeypatch.setattr(control, "utc_now_naive", lambda: now)
    _idle_agent(monkeypatch)
    _running_since(session, chat_id, START)
    finished = START + timedelta(seconds=5)
    assert control.settle_finished_thread_time(chat_id, finished_at=finished, _session_provider=session) is True
    assert _entries(session, chat_id) == [("auto_pause", START, finished, 5)]
    # Never ends before the segment started, never in the future.
    assert control.settle_finished_thread_time(chat_id, _session_provider=session) is False


def test_finished_run_keeps_the_timer_while_another_automation_run_is_live(development_db, monkeypatch):  # noqa: F811
    session, chat_id = development_db["session"], development_db["chat_id"]
    now = START + timedelta(minutes=10)
    monkeypatch.setattr(control, "utc_now_naive", lambda: now)
    _idle_agent(monkeypatch)
    _running_since(session, chat_id, START)
    with session() as db:
        db.add(AutomationRun(automation_id=1, status="running", started_at=START,
                             run_data=_json.dumps({"chat_id": chat_id})))
        db.commit()
    assert control.settle_finished_thread_time(chat_id, _session_provider=session) is False
    assert _item(session, chat_id).time_paused is False


def test_a_dead_automation_run_row_does_not_hold_the_timer_forever(development_db, monkeypatch):  # noqa: F811
    session, chat_id = development_db["session"], development_db["chat_id"]
    now = START + timedelta(hours=7)
    monkeypatch.setattr(control, "utc_now_naive", lambda: now)
    _idle_agent(monkeypatch)
    _running_since(session, chat_id, START)
    with session() as db:
        db.add(AutomationRun(automation_id=1, status="running", started_at=START,
                             run_data=_json.dumps({"chat_id": chat_id})))
        db.commit()
    assert control.sweep_idle_thread_timers(_session_provider=session) == [chat_id]


def test_finished_run_leaves_a_live_agent_timer_running(development_db, monkeypatch):  # noqa: F811
    session, chat_id = development_db["session"], development_db["chat_id"]
    monkeypatch.setattr(control, "utc_now_naive", lambda: START + timedelta(minutes=1))
    _idle_agent(monkeypatch, active=True)
    _running_since(session, chat_id, START)
    assert control.settle_finished_thread_time(chat_id, _session_provider=session) is False
    assert _item(session, chat_id).time_paused is False


def test_tool_automation_worker_settles_its_thread_timer_when_it_ends(monkeypatch):
    from distr.core import automation_subagent as subagent

    settled = []
    monkeypatch.setattr(subagent, "_execute_tool_automation",
                        lambda automation, manual: (False, "Skipped: nothing new", ""))
    monkeypatch.setattr(subagent, "update_automation_run", lambda *a, **k: None)
    monkeypatch.setattr(subagent, "_orchestrator_delivery_ack", lambda **k: None)
    monkeypatch.setattr(subagent, "release_workflow_run", lambda _wid: None)
    monkeypatch.setattr(control, "settle_finished_thread_time", lambda chat_id, **k: settled.append(chat_id))
    subagent._automation_worker(
        automation={"name": "WhatsApp Status", "action_config": {"tool": "whatsapp_status"}},
        run_id=1, manual=False, chat_id=564, speak=False, schedule_metadata=None, emit_event=None,
    )
    assert settled == [564]


def test_instruction_automation_worker_does_not_settle_on_handoff(monkeypatch):
    from distr.core import automation_subagent as subagent

    settled = []
    monkeypatch.setattr(subagent, "_execute_instruction_automation",
                        lambda automation, **k: (True, "Handed off.", ""))
    monkeypatch.setattr(subagent, "update_automation_run", lambda *a, **k: None)
    monkeypatch.setattr(subagent, "release_workflow_run", lambda _wid: None)
    monkeypatch.setattr(control, "settle_finished_thread_time", lambda chat_id, **k: settled.append(chat_id))
    subagent._automation_worker(
        automation={"name": "Daily plan", "action_config": {}},
        run_id=2, manual=False, chat_id=574, speak=False, schedule_metadata=None, emit_event=None,
    )
    assert settled == []
