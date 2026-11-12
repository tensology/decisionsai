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
