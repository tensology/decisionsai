"""2026-10-29: cron/time-window schedule, read-only mode, notification_policy, Gmail read-only, support monitor."""

from __future__ import annotations

import json
import sqlite3
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

SAST = ZoneInfo("Africa/Johannesburg")
SCHED = {"kind": "cron", "cron": "1-51/10 7-19 * * *", "timezone": "Africa/Johannesburg"}


# ---------------------------------------------------------------- gap 1
def _fires_for_day(day: datetime) -> list[datetime]:
    from distr.core.workflow.scheduler import _next_run_from_cron

    start = day.replace(hour=0, minute=0, tzinfo=SAST).astimezone(timezone.utc).replace(tzinfo=None)
    end = start + timedelta(days=1)
    out, cur = [], start
    while True:
        nxt = _next_run_from_cron(SCHED["cron"], cur, SCHED["timezone"])
        if nxt is None or nxt >= end:
            return out
        out.append(nxt.replace(tzinfo=timezone.utc).astimezone(SAST))
        cur = nxt + timedelta(seconds=1)


def test_cron_window_schedule_fires_0701_to_1951_sast_only():
    fires = _fires_for_day(datetime(2026, 10, 30))
    assert fires[0].strftime("%H:%M") == "07:01"
    assert fires[-1].strftime("%H:%M") == "19:51"
    assert len(fires) == 13 * 6
    assert all(7 <= f.hour < 20 for f in fires)
    assert {f.minute for f in fires} == {1, 11, 21, 31, 41, 51}


def test_cron_kind_normalizes_and_rejects_garbage():
    from distr.core.automation.store import AutomationStoreError, compute_next_run, normalize_schedule

    s = normalize_schedule(SCHED, strict=True)
    assert s["kind"] == "cron" and s["cron"] == "1-51/10 7-19 * * *"
    nxt = compute_next_run(s).replace(tzinfo=timezone.utc).astimezone(SAST)
    assert 7 <= nxt.hour < 20 and nxt.minute % 10 == 1
    with pytest.raises(AutomationStoreError):
        normalize_schedule({"kind": "cron", "cron": "every ten minutes"}, strict=True)
    with pytest.raises(AutomationStoreError):
        normalize_schedule({"kind": "cron", "cron": "*/10 7-19 * * *", "timezone": "Mars/Base"}, strict=True)


def test_cron_automation_persists_and_advances(tmp_path):
    from distr.core.automation.scheduler import ensure_automation_schema
    from distr.core.automation.scheduler_advance import advance_next_run_for_automation
    from distr.core.automation.store import create_automation, get_automation_row

    ensure_automation_schema()
    a = create_automation(name="cron test", automation_type="scheduled_instruction", status="active", instruction="x", preset_id="", schedule=SCHED, action_config={})
    assert a["schedule"]["kind"] == "cron" and a["schedule"]["cron"] == SCHED["cron"]
    row = get_automation_row(a["id"])
    advance_next_run_for_automation(row)
    local = row.next_run_at.replace(tzinfo=timezone.utc).astimezone(SAST)
    assert 7 <= local.hour < 20 and local.minute % 10 == 1


# ---------------------------------------------------------------- gap 2
def test_read_only_blocks_writes_but_allows_state_file(tmp_path):
    from distr.core.automation.read_only import ReadOnlyPolicy, ReadOnlyViolation, assert_action_allowed, read_only_scope

    state = tmp_path / "state.json"
    state.write_text("{}")
    other = tmp_path / "other.txt"
    policy = ReadOnlyPolicy(state)
    with read_only_scope(policy):
        with pytest.raises(ReadOnlyViolation):
            other.write_text("nope")
        with pytest.raises(ReadOnlyViolation):
            state.unlink()
        with pytest.raises(ReadOnlyViolation):
            sqlite3.connect(str(tmp_path / "x.db"))
        with pytest.raises(ReadOnlyViolation):
            import subprocess

            subprocess.run(["true"])
        with pytest.raises(ReadOnlyViolation):
            urllib.request.urlopen(urllib.request.Request("http://127.0.0.1:9/x", data=b"a", method="POST"))
        with pytest.raises(ReadOnlyViolation):
            assert_action_allowed("send_email")
        assert state.read_text() == "{}"  # reads are fine
        policy.write_state({"ok": 1})
        policy.write_state({"ok": 2})
    assert json.loads(state.read_text()) == {"ok": 2}
    assert list(tmp_path.glob("state.json.bak-*"))
    assert not other.exists()
    other.write_text("fine outside scope")  # hook is inert outside the scope
    assert len(policy.blocked) >= 6


def test_read_only_runner_refuses_non_capable_tool():
    from distr.core.automation_tool_runner import run_automation_tool_for

    res = run_automation_tool_for({"name": "x", "action_config": {"read_only": {"state_file": "/tmp/s.json"}}}, "whatsapp_status", {})
    assert res["success"] is False and "not read-only capable" in res["output"]


def test_read_only_instruction_automation_is_refused():
    from distr.core.automation_orchestrator import dispatch_automation_to_current_chat

    res = dispatch_automation_to_current_chat({"name": "x", "instruction": "do things", "action_config": {"read_only": True}})
    assert res["status"] == "failed" and "Read-only" in res["summary"]


# ---------------------------------------------------------------- gap 3
def _deliver(raw, policy):
    from distr.core.automation.notify import deliver

    calls = {"event": [], "thread": [], "desktop": []}
    out = deliver(
        {"name": "Support monitor", "thread_chat_id": 7, "action_config": {"notification_policy": policy}},
        raw,
        emit_event=lambda **kw: calls["event"].append(kw),
        post_to_thread=lambda cid, body: calls["thread"].append((cid, body)) or True,
        desktop=lambda title, msg: calls["desktop"].append((title, msg)),
    )
    return out, calls


def test_notification_fires_on_new_item():
    out, calls = _deliver({"notify": True, "headline": "1 new items, 1 need action.", "markdown": "- NEEDS ACTION x"}, {"mode": "on_new", "channels": ["in_app", "macos"]})
    assert out["notified"] and calls["event"] and calls["thread"] and calls["desktop"]
    assert calls["desktop"][0] == ("Support monitor", "1 new items, 1 need action.")


def test_notification_silent_when_nothing_new():
    out, calls = _deliver({"notify": False}, {"mode": "on_new", "channels": ["in_app", "macos"]})
    assert out["quiet"] and not out["notified"] and not any(calls.values())


def test_notification_rejects_unsafe_channels():
    from distr.core.automation.notify import normalize_policy

    assert normalize_policy({"mode": "on_new", "channels": ["whatsapp", "email", "telegram", "macos"]})["channels"] == ["macos"]


def test_no_policy_keeps_legacy_behaviour():
    from distr.core.automation.notify import deliver

    assert deliver({"action_config": {}}, {"notify": True}) == {"notified": False, "quiet": False, "channels": [], "policy": None}


# ---------------------------------------------------------------- gap 4
class FakeGmail:
    def __init__(self):
        self.calls = []

    def is_connected(self):
        return True

    def _ensure_valid_token(self):
        return True

    def _make_request(self, method, url, **kw):
        self.calls.append((method, url))
        if "/messages?" in url:
            return {"messages": [{"id": "m1"}]}
        return {"id": "m1", "threadId": "t1", "internalDate": "1793260000000", "labelIds": ["INBOX"], "snippet": "hi",
                "payload": {"headers": [{"name": "From", "value": "Ann <ann@client.co>"}, {"name": "Subject", "value": "Invoice question?"}]}}


def test_gmail_readonly_search_uses_only_get_metadata():
    from distr.core.automation import gmail_readonly

    svc = FakeGmail()
    res = gmail_readonly.search(gmail_readonly.connect(svc), "in:inbox after:1")
    assert res[0]["subject"] == "Invoice question?"
    assert all(m == "GET" for m, _ in svc.calls)
    assert "format=metadata" in svc.calls[1][1]


# ---------------------------------------------------------------- monitor
def _fixture_db(path: Path) -> Path:
    con = sqlite3.connect(path)
    con.executescript(
        """
        create table whatsapp_messages(id integer primary key, jid text, chat_type text, sender_push_name text, sender_phone text, text text, caption text, media_type text, whatsapp_timestamp integer, from_me integer);
        create table telegram_group_messages(id integer primary key, chat_title text, text text, caption text, telegram_date integer);
        create table kanban_boards(id integer primary key, name text, archived integer);
        create table kanban_lanes(id integer primary key, board_id integer, name text);
        create table kanban_tickets(id integer primary key, lane_id integer, title text, created_date text, modified_date text);
        create table auto_workflow_runs(id integer primary key, workflow_id integer, started_at text, status text, ticket_id integer);
        insert into kanban_boards values (1,'AuctionNow',0);
        insert into kanban_lanes values (1,1,'In Progress');
        insert into kanban_tickets values (5,1,'Old ticket','2026-09-01 10:00:00','2026-09-03 13:10:00');
        insert into auto_workflow_runs values (166,430,'2026-10-24 19:09:08','waiting',287);
        """
    )
    con.commit()
    con.close()
    return path


def test_support_monitor_reports_new_and_stale_then_goes_quiet(tmp_path):
    from distr.core.automation.read_only import ReadOnlyPolicy, read_only_scope
    from distr.core.automation.support_monitor import SupportMonitor

    db = _fixture_db(tmp_path / "s.db")
    now = datetime(2026, 10, 29, 10, 30, tzinfo=timezone.utc)
    ts = int(now.timestamp()) - 600
    con = sqlite3.connect(db)
    con.execute("insert into whatsapp_messages values (1,'roland@s','private','Roland','1','Can you call me about the invoice?',null,null,?,0)", (ts,))
    con.commit(); con.close()
    state = tmp_path / "state-decisions.json"
    policy = ReadOnlyPolicy(state)

    def mon():
        return SupportMonitor(db_path=db, state_file=state, seed_state_file=None, now=now, gmail_service=FakeGmail(),
                              mailshot_fetch=lambda: [{"_id": "e1", "from": "Bob <bob@client.co>", "subject": "Site is down", "date": "2026-10-29T10:00:00.000Z"}],
                              port_check=lambda: True)

    with read_only_scope(policy):
        first = mon().run(write_state=policy.write_state)
    assert first["notify"] and first["new_items"] == 4 and first["needs_action"]
    assert "NEEDS ACTION" in first["markdown"] and "Stale proposals" in first["markdown"]
    assert "telegram: dark" in first["markdown"]
    with read_only_scope(policy):
        second = mon().run(write_state=policy.write_state)
    assert second["notify"] is False and second["new_items"] == 0 and second["newly_stale"] == 0


def test_support_monitor_tunnel_down_and_night_silence(tmp_path):
    from distr.core.automation.support_monitor import SupportMonitor

    db = _fixture_db(tmp_path / "s.db")
    night = datetime(2026, 10, 29, 18, 5, tzinfo=timezone.utc)  # 20:05 SAST
    m = SupportMonitor(db_path=db, state_file=tmp_path / "s.json", seed_state_file=None, now=night, port_check=lambda: False)
    assert m.run()["notify"] is False
    day = datetime(2026, 10, 29, 8, 0, tzinfo=timezone.utc)
    m = SupportMonitor(db_path=db, state_file=tmp_path / "s.json", seed_state_file=None, now=day, port_check=lambda: False)
    saved = {}
    res = m.run(write_state=saved.update)
    assert res["channel_status"]["mailshot"].startswith("DOWN") and saved["outages"]


@pytest.mark.parametrize("notify", [True, False])
def test_worker_honours_notification_policy(tmp_path, monkeypatch, notify):
    from distr.core.db import Chat, get_session, init_db
    from distr.core.db.automation import Automation, AutomationRun

    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'notify.sqlite3'}")
    init_db()
    cfg = {"tool": "proactive_orchestrator", "args": {}, "notification_policy": {"mode": "on_new", "channels": ["in_app", "macos"]}}
    with get_session() as session:
        chat = Chat(title="n", created_date=datetime.utcnow(), modified_date=datetime.utcnow())
        session.add(chat)
        session.flush()
        auto = Automation(name="Mon", automation_type="tool_action", status="active", action_config=json.dumps(cfg))
        session.add(auto)
        session.flush()
        run = AutomationRun(automation_id=auto.id, status="running", run_data="{}")
        session.add(run)
        session.commit()
        chat_id, run_id = chat.id, run.id
    raw = {"notify": notify, "headline": "2 new items, 1 need action.", "markdown": "REPORT BODY"}
    monkeypatch.setattr("distr.core.automation_tool_runner.run_automation_tool", lambda tool, args: {"success": True, "output": "x", "spoken_summary": "x", "raw": raw})
    banners = []
    monkeypatch.setattr("distr.core.tensology_notify.show_desktop_notification", lambda t, m: banners.append((t, m)))
    from distr.core.automation_subagent import _automation_worker

    _automation_worker(automation={"id": "auto_1", "name": "Mon", "action_config": cfg}, run_id=run_id, manual=False, chat_id=chat_id, speak=False, schedule_metadata=None, emit_event=None)
    with get_session() as session:
        posts = [c.response for c in session.query(Chat).filter(Chat.parent_id == chat_id).all()]
        data = json.loads(session.get(AutomationRun, run_id).run_data)
    if notify:
        assert posts == ["REPORT BODY"] and banners == [("Mon", "2 new items, 1 need action.")]
        assert data["delivery_channel"] == "notification"
    else:
        assert posts == [] and banners == [] and data["delivery_channel"] == "none"
