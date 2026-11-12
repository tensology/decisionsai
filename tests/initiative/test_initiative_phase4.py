"""Phase 4: client updates for Telegram- and Jira-originated work, and ignored
projects kept out of the proactive scan and Incoming triage matching.

Every outbound send is mocked: the Telegram manager is a fake, the Jira comment
call is a stub, and the database is in memory.
"""

from __future__ import annotations

import contextlib
import json
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import distr.core.db.kanban  # noqa: F401
import distr.core.db.orchestrator  # noqa: F401
import distr.core.db.projects  # noqa: F401
import distr.core.db.workflow  # noqa: F401
import distr.core.initiative.outcome_flow as of
from distr.core.db import Base
from distr.core.db.kanban import KanbanBoard, KanbanLane, KanbanTicket
from distr.core.db.projects import Project
from distr.core.workflow import reply_delivery

# Own with "Ask before messaging clients" switched off: the automatic path. Ask-first is covered in test_initiative_fixes_20261112.py.
OWN = {"initiative_level": "own", "initiative_allow_telegram": True, "initiative_scan_telegram": True,
       "initiative_scan_external_boards": True, "initiative_ask_external_comms": False}
STD = {"initiative_level": "operate", "initiative_allow_telegram": True, "initiative_scan_telegram": True,
       "initiative_scan_external_boards": True}
QUIET = {"initiative_level": "observe", "initiative_allow_telegram": False}
OWNER_CHAT = 4242


class FakeTelegram:
    telegram_user_id = OWNER_CHAT
    chat_id = None

    def __init__(self):
        self.sent = []

    def send_to_telegram(self, text, reply_markup=None, *, expected_chat_id=None):
        assert expected_chat_id in (None, str(OWNER_CHAT))
        self.sent.append({"text": text, "reply_markup": reply_markup})
        return True


@pytest.fixture
def env(monkeypatch, tmp_path):
    monkeypatch.setenv("DECISIONS_DB_DIR", str(tmp_path))
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=True)
    monkeypatch.setattr("distr.core.db.get_session", lambda: factory())
    tg = FakeTelegram()
    events, comments = [], []
    monkeypatch.setattr(of, "_telegram_manager", lambda: tg)
    monkeypatch.setattr("distr.core.kanban.ticket_workflow_engagement._telegram_manager_from_app", lambda: tg)
    monkeypatch.setattr("distr.core.orchestrator.emit_event", lambda **kw: events.append(kw))
    monkeypatch.setattr("distr.core.workflow.dev_approvals.gate", lambda *a, **k: None)
    account = {"provider": "jira", "email": "paul@example.com", "api_token": "x", "domain": "acme.atlassian.net"}
    state = SimpleNamespace(account=account, settings=dict(OWN))
    monkeypatch.setattr("distr.core.kanban.jira_intake.load_jira_account", lambda: state.account)
    monkeypatch.setattr("distr.core.kanban.jira_work_lifecycle.post_jira_comment",
                        lambda acct, key, body, **kw: comments.append({"key": key, "body": body}) or {"success": True})
    monkeypatch.setattr(of, "load_settings", lambda: dict(state.settings))
    return SimpleNamespace(tg=tg, events=events, comments=comments, factory=factory, tmp=tmp_path, state=state, mp=monkeypatch)


def _ticket(factory, **values) -> int:
    with factory() as db:
        board = KanbanBoard(name="Board")
        db.add(board)
        db.flush()
        lane = KanbanLane(board_id=board.id, name="Ready", position=0)
        db.add(lane)
        db.flush()
        ticket = KanbanTicket(lane_id=lane.id, title="Update the weekly report", **values)
        db.add(ticket)
        db.commit()
        return int(ticket.id)


def _telegram_ticket(env, chat=OWNER_CHAT):
    return _ticket(env.factory, source_provider="telegram", source_thread_id=str(chat), source_contact=str(chat),
                   source_external_id="tgmsg-1")


def _jira_ticket(env):
    return _ticket(env.factory, source_provider="jira", source_external_id="ABC-12", external_id="ABC-12",
                   source_label="Jira")


def _audit(tmp):
    path = tmp / "logs" / "initiative_outcome_audit.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def _reply_flow():
    flows = (of._load_state().get("flows") or {}).items()
    return next((key, flow) for key, flow in flows if flow.get("kind") == "reply")


REPLY = "All done. The weekly report is updated and ready for you."


# ============================ TELEGRAM =====================================

def test_own_telegram_work_replies_in_the_originating_chat_and_is_audited(env):
    ticket_id = _telegram_ticket(env)
    outcome = reply_delivery.deliver_ticket_reply(ticket_id, REPLY)
    assert outcome["delivered"] is True
    texts = [m["text"] for m in env.tg.sent]
    assert outcome["body"] in texts  # the client update itself, through the existing sender
    assert any(t.startswith("Finished:") for t in texts)
    sent = [a for a in _audit(env.tmp) if a["event"] == "own_client_reply_sent"]
    assert sent and sent[0]["destination"] == str(OWNER_CHAT) and sent[0]["delivered"] is True
    assert of.posture_handles_reply(ticket_id) is True
    for text in texts:
        assert not of.looks_technical(text), text


def test_telegram_reply_to_a_chat_the_sender_cannot_reach_is_refused(env):
    ticket_id = _telegram_ticket(env, chat=999)
    outcome = reply_delivery.deliver_ticket_reply(ticket_id, REPLY)
    assert outcome["delivered"] is False and outcome.get("preflight_failed")
    assert all(REPLY not in m["text"] or "blocked" in m["text"] for m in env.tg.sent)
    assert any("blocked" in m["text"] for m in env.tg.sent)


def test_standard_telegram_holds_then_sends_exact_confirmed_text(env):
    env.state.settings = dict(STD)
    ticket_id = _telegram_ticket(env)
    held = reply_delivery.deliver_ticket_reply(ticket_id, REPLY)
    assert held.get("pending_approval") and not held["delivered"]
    assert len(env.tg.sent) == 1 and "Send message to client" in json.dumps(env.tg.sent[0]["reply_markup"])
    key, flow = _reply_flow()
    overview = of._handle_flow_action(key, "send")["text"]
    exact = of.client_body(flow["body"])
    assert exact in overview
    done = of._handle_flow_action(key, "confirm")
    assert done["text"].startswith("Sent.")
    assert env.tg.sent[-1]["text"] == exact
    assert of.get_flow(key)["status"] == "sent"
    confirmed = [a for a in _audit(env.tmp) if a["event"] == "operate_client_reply_confirmed"]
    assert confirmed and confirmed[0]["delivered"] is True


# ============================ JIRA =========================================

def test_own_jira_work_posts_a_comment_on_the_issue(env):
    ticket_id = _jira_ticket(env)
    outcome = reply_delivery.deliver_ticket_reply(ticket_id, REPLY)
    assert outcome["delivered"] is True
    assert env.comments == [{"key": "ABC-12", "body": outcome["body"]}]
    sent = [a for a in _audit(env.tmp) if a["event"] == "own_client_reply_sent"]
    assert sent[0]["destination"] == "ABC-12"
    assert any("Finished" in m["text"] and "Jira" in m["text"] for m in env.tg.sent)


def test_standard_jira_comment_waits_for_confirm_then_posts_exact_text(env):
    env.state.settings = dict(STD)
    ticket_id = _jira_ticket(env)
    reply_delivery.deliver_ticket_reply(ticket_id, REPLY)
    assert env.comments == []
    key, flow = _reply_flow()
    of._handle_flow_action(key, "send")
    assert env.comments == []
    of._handle_flow_action(key, "confirm")
    assert env.comments == [{"key": "ABC-12", "body": of.client_body(flow["body"])}]


def test_jira_without_a_connected_account_sends_nothing_and_says_so(env):
    env.state.account = None
    ticket_id = _jira_ticket(env)
    outcome = reply_delivery.deliver_ticket_reply(ticket_id, REPLY)
    assert outcome["delivered"] is False and outcome.get("preflight_failed")
    assert "No Jira account" in outcome["reason"]
    assert env.comments == []
    assert any("blocked" in m["text"] for m in env.tg.sent)


def test_quiet_keeps_telegram_and_jira_unsupported(env):
    env.state.settings = dict(QUIET)
    for ticket_id in (_jira_ticket(env), _telegram_ticket(env)):
        outcome = reply_delivery.deliver_ticket_reply(ticket_id, "Done.")
        assert outcome["delivered"] is False
    assert env.comments == [] and env.tg.sent == []


def test_jira_review_card_is_skipped_when_initiative_owns_the_update(monkeypatch, tmp_path):
    from sqlalchemy import text

    from distr.core.kanban import jira_work_lifecycle as lifecycle

    monkeypatch.setenv("DECISIONS_DB_DIR", str(tmp_path))
    monkeypatch.setattr(lifecycle, "engine", create_engine(f"sqlite:///{tmp_path / 'jl.db'}"))
    lifecycle.ensure_tables()
    lifecycle.record_ticket_created(ticket_id=42, board_id=7, project_id=3, issue_key="ACME-2")
    audits = []
    monkeypatch.setattr(lifecycle, "_audit", lambda *a, **k: audits.append(a[2]))
    of.save_flow(of._flow_key("reply", 42, "x"), kind="reply", ticket_id=42, status="awaiting_decision")
    assert lifecycle.prepare_completed_jira_review(ticket_id=42, run_id=8, status="completed") is None
    assert audits == ["client_update_via_initiative"]
    with lifecycle.engine.connect() as conn:
        assert conn.execute(text("SELECT COUNT(*) FROM jira_reply_reviews")).scalar_one() == 0


def test_posture_handles_reply_ignores_failed_and_other_tickets(monkeypatch, tmp_path):
    monkeypatch.setenv("DECISIONS_DB_DIR", str(tmp_path))
    of.save_flow("a", kind="reply", ticket_id=5, status="send_failed")
    of.save_flow("b", kind="intake", ticket_id=6, status="running")
    assert of.posture_handles_reply(5) is False and of.posture_handles_reply(6) is False
    of.save_flow("c", kind="reply", ticket_id=5, status="sent")
    assert of.posture_handles_reply(5) is True


# ============================ IGNORED PROJECTS =============================

@contextlib.contextmanager
def _ctx(factory):
    session = factory()
    try:
        yield session
        session.commit()
    finally:
        session.close()


def _projects_db(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'p.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with _ctx(factory) as db:
        kept = Project(name="Shop", folder_location=str(tmp_path / "shop"), coding_backend="codex")
        hidden = Project(name="Secret", folder_location=str(tmp_path / "secret"), coding_backend="codex",
                         agent_ignored=True)
        db.add_all([kept, hidden])
        db.flush()
        board = KanbanBoard(name="Work")
        db.add(board)
        db.flush()
        lane = KanbanLane(board_id=board.id, name="Inbox", position=0)
        db.add(lane)
        db.flush()
        db.add_all([
            KanbanTicket(lane_id=lane.id, title="Fix the shop checkout", linked_project_id=kept.id, priority="high"),
            KanbanTicket(lane_id=lane.id, title="Fix the secret thing", linked_project_id=hidden.id, priority="critical"),
        ])
        ids = SimpleNamespace(kept=kept.id, hidden=hidden.id, board=board.id)
    return factory, ids


def test_proactive_scan_skips_ignored_projects(monkeypatch, tmp_path):
    import distr.core.orchestrator_proactive as proactive

    factory, ids = _projects_db(tmp_path)
    monkeypatch.setattr(proactive, "get_session", lambda: _ctx(factory))
    signals = proactive._collect_board_signals(limit=20)
    project_ids = {s["project_id"] for s in signals}
    assert ids.kept in project_ids and ids.hidden not in project_ids
    assert all("secret" not in s["title"].lower() for s in signals)


def test_incoming_triage_never_matches_ignored_projects(monkeypatch, tmp_path):
    from distr.core.incoming import proposals

    factory, ids = _projects_db(tmp_path)
    monkeypatch.setattr("distr.core.db.get_session", lambda: _ctx(factory))
    candidates = proposals._candidate_projects()
    assert [c["id"] for c in candidates] == [ids.kept]
    assert proposals._project_map() == {"shop": ids.kept}


# ===================== OWN + SWITCHED-OFF CHANNELS ==========================

def test_own_telegram_update_blocked_when_telegram_is_off_and_owner_told_on_whatsapp(env):
    owner_msgs = []
    env.mp.setattr(of, "_whatsapp_to_owner", lambda jid, text: owner_msgs.append((jid, text)) or True)
    env.state.settings = dict(OWN, initiative_allow_telegram=False, initiative_own_channel="whatsapp",
                              initiative_owner_whatsapp="+27 82 000 0000")
    ticket_id = _telegram_ticket(env)
    outcome = reply_delivery.deliver_ticket_reply(ticket_id, REPLY)
    assert outcome["delivered"] is False and outcome["blocked"] is True
    assert "Telegram is switched off" in outcome["reason"]
    assert env.tg.sent == []  # nothing at all went to Telegram
    events = _audit(env.tmp)
    blocked = [a for a in events if a["event"] == "own_client_reply_blocked_setting_off"]
    assert blocked and blocked[0]["status"] == "not_sent" and blocked[0]["destination"] == str(OWNER_CHAT)
    assert len(owner_msgs) == 1 and "didn't send" in owner_msgs[0][1]
    assert not of.looks_technical(owner_msgs[0][1])
    assert any(a["event"] == "own_blocked" and a["channel"] == "whatsapp" for a in events)
    assert of.posture_handles_reply(ticket_id) is False


def test_own_telegram_update_blocked_when_telegram_is_off_logs_by_default(env):
    env.state.settings = dict(OWN, initiative_allow_telegram=False)
    ticket_id = _telegram_ticket(env)
    outcome = reply_delivery.deliver_ticket_reply(ticket_id, REPLY)
    assert outcome["blocked"] is True and env.tg.sent == []
    note = [a for a in _audit(env.tmp) if a["event"] == "own_blocked"]
    assert note and note[0]["channel"] == "log" and "Telegram is switched off" in note[0]["text"]


def test_own_jira_update_blocked_when_external_boards_switch_is_off(env):
    env.state.settings = dict(OWN, initiative_scan_external_boards=False)
    ticket_id = _jira_ticket(env)
    outcome = reply_delivery.deliver_ticket_reply(ticket_id, REPLY)
    assert outcome["delivered"] is False and outcome["blocked"] is True
    assert env.comments == []
    assert any("Jira is switched off" in m["text"] for m in env.tg.sent)
    assert any(a["event"] == "own_client_reply_blocked_setting_off" for a in _audit(env.tmp))


def test_own_jira_update_still_sends_when_only_telegram_is_off(env):
    env.state.settings = dict(OWN, initiative_allow_telegram=False)
    ticket_id = _jira_ticket(env)
    outcome = reply_delivery.deliver_ticket_reply(ticket_id, REPLY)
    assert outcome["delivered"] is True and len(env.comments) == 1
    assert env.tg.sent == []  # the "Finished" note goes to the log, not Telegram
