"""Spec 01 (ticket 336): readable approvals + presence routing.

Synthetic only. Boards, projects, tickets and Tensology / Telegram transports here are made up; nothing
reaches a live channel, board or running integration. Tests drive the real entry points (the Telegram
handler, the kanban tool's voice decision path, the openai_compat acknowledgment, the Incoming dispatch
guard) and monkeypatch the delivery seams rather than passing test-only parameters.
"""
from __future__ import annotations

import contextlib
import threading
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from distr.core.db import Base
from distr.core.db.kanban import KanbanBoard, KanbanLane, KanbanTicket
from distr.core.db.projects import Project


@pytest.fixture
def store(monkeypatch):
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)

    @contextlib.contextmanager
    def session():
        db = factory()
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    monkeypatch.setattr("distr.core.db.get_session", session)
    yield session
    engine.dispose()


@pytest.fixture(autouse=True)
def isolated_store(monkeypatch, tmp_path):
    """Throwaway proposal / ask ledgers, no live presence DB, and no real speech or channels."""
    monkeypatch.setenv("DECISIONS_DB_DIR", str(tmp_path))
    from distr.core import notification_routing as nr
    from distr.core.initiative import approval_notice as an

    monkeypatch.setattr(nr, "_read_activity", lambda: {})
    monkeypatch.setattr(nr, "_write_activity", lambda *a, **k: None)
    monkeypatch.setattr(an, "_deliver", lambda *a, **k: True)  # never inject speech in a test
    nr.clear_notification_activity_cache()
    yield
    nr.clear_notification_activity_cache()


@pytest.fixture
def transports(monkeypatch):
    """Record what would go to Telegram / Tensology / the CLI instead of doing it."""
    calls = SimpleNamespace(cards=[], edits=[], posted=[], dismissed=[], accepted=[], starts=[])
    monkeypatch.setattr("distr.core.initiative.pi_write_proposals._default_notify",
                        lambda text, markup: calls.cards.append((text, markup)) or {"ok": True, "message_id": 777, "chat_id": 555})
    monkeypatch.setattr("distr.core.initiative.pi_write_proposals._default_edit_card",
                        lambda text, mid, cid: calls.edits.append((text, mid, cid)) or True)
    monkeypatch.setattr("distr.core.initiative.pi_write_proposals._default_post_incoming",
                        lambda payload, key: calls.posted.append((payload, key)) or {"id": 9001})
    monkeypatch.setattr("distr.core.initiative.pi_write_proposals._default_dismiss_incoming",
                        lambda tid: calls.dismissed.append(tid))
    monkeypatch.setattr("distr.core.initiative.pi_write_proposals._default_accept_incoming",
                        lambda tid, ticket=None: calls.accepted.append((tid, ticket)))
    monkeypatch.setattr("distr.core.agent.tools.integrations.kanban_ticket.KanbanTicketTool._action_send_to_cli",
                        lambda self, tid, approved=False: calls.starts.append(tid) or "Ticket is running.")
    return calls


def _seed(store, *, title="Change the banking line"):
    with store() as db:
        project = Project(name="Synthetic Shop", folder_location="/tmp/synthetic-shop")
        db.add(project)
        db.flush()
        board = KanbanBoard(name="Synthetic Board", default_project_id=project.id)
        db.add(board)
        db.flush()
        lanes = {name: KanbanLane(board_id=board.id, name=name, position=i)
                 for i, name in enumerate(["Backlog", "In Progress", "QA", "Complete"])}
        db.add_all(lanes.values())
        db.flush()
        ticket = KanbanTicket(lane_id=lanes["Backlog"].id, title=title, position=0, linked_project_id=project.id)
        db.add(ticket)
        db.commit()
        return SimpleNamespace(project=project.id, board=board.id, ticket=ticket.id,
                               lanes={k: v.id for k, v in lanes.items()})


# 1. Routine moves and read-only runs never ask -------------------------------------------------------------

def test_routine_single_ticket_lane_move_is_inform_only():
    from distr.core.initiative.approval_notice import is_routine_lane_move

    assert is_routine_lane_move("ticket_lane_move", {"ticket_ids": [335], "target_lane": "In Progress"})
    assert is_routine_lane_move("ticket_lane_move", {"ticket_ids": [335], "target_lane": "QA"})
    assert not is_routine_lane_move("ticket_lane_move", {"ticket_ids": [335, 336], "target_lane": "In Progress"})
    assert not is_routine_lane_move("ticket_lane_move", {"ticket_ids": [335], "target_lane": "Complete"})
    assert not is_routine_lane_move("ticket_lane_move", {"ticket_ids": [335]})
    assert not is_routine_lane_move("ticket_lane_move", {"ticket_ids": [335], "target_lane": "In Progress"},
                                    {"initiative_routine_lane_moves_inform_only": False})


def test_routine_move_reports_ticket_and_action_once(monkeypatch):
    from distr.core.initiative import approval_notice as an
    from distr.core.initiative.service import _lane_move_done_line

    done = _lane_move_done_line({"ticket_ids": [335], "ticket_title": "EFT banking details",
                                 "target_lane": "In Progress"})
    assert done == "Moved ticket 335, EFT banking details, to In Progress and started its timer."

    sent = []
    monkeypatch.setattr(an, "_deliver", lambda surface, spoken, written, data=None: sent.append((surface, spoken)) or True)
    first = an.inform_after(done, key="lane:move-335", surface="remote")
    second = an.inform_after(done, key="lane:move-335", surface="remote")
    assert first["sent"] is True and second["sent"] is False  # one nudge, not a drumbeat
    assert sent == [("remote", done)]


def test_read_only_runs_bypass_approval_and_writes_do_not():
    from distr.core.initiative.pi_write_proposals import is_write_instruction

    assert is_write_instruction("inspect the checkout and report what you find") is False
    assert is_write_instruction("run the tests and tell me what fails") is False
    assert is_write_instruction("fix the checkout and push it") is True


# 2. A write proposal reads plainly and mirrors to Tensology incoming ---------------------------------------

def test_proposal_ask_names_ticket_title_action_why_and_how_to_answer():
    from distr.core.initiative.pi_write_proposals import ask_for

    asked = ask_for(335, "EFT banking details", "Synthetic Shop", filed=False, from_lane="Backlog")
    assert "ticket 335, EFT banking details" in asked
    assert "In Progress" in asked and "start Pi on it" in asked
    assert "changes code" in asked and "your okay" in asked
    assert "approve or decline" in asked and "Telegram" in asked


def test_write_proposal_sends_a_distinct_card_and_mirrors_tensology(store, transports):
    from distr.core.initiative import pi_write_proposals as pw

    ids = _seed(store)
    out = pw.file_write_proposal(ids.project, "Synthetic Shop", "Replace the card form with hosted checkout",
                                 _sync=True)
    tid = out["ticket_id"]
    assert out["status"] == "proposed" and out["card_sent"] is True

    card_text, markup = transports.cards[0]
    assert f"ticket {tid}," in card_text and "Nothing has been changed" in card_text
    assert [b["callback_data"] for b in markup["inline_keyboard"][0]] == [f"pw:{tid}:approve", f"pw:{tid}:decline"]

    payload, key = transports.posted[0]
    assert key == f"pi-write-{tid}"
    assert payload["origin"] == "decisions" and payload["origin_ref"] == f"decisions-ticket-{tid}"
    assert payload["instruction"] == "Replace the card form with hosted checkout"
    assert payload["title"] and payload["rationale"]


# 3. One approval identity drives all three views, drops the buttons, and dispatches once -------------------

def test_remote_approval_edits_the_card_and_dispatches_once(store, transports):
    from distr.core.initiative import pi_write_proposals as pw

    ids = _seed(store)
    out = pw.file_write_proposal(ids.project, "Synthetic Shop", "Swap the payment provider", _sync=True)
    tid = out["ticket_id"]
    reply = pw.decide(tid, "approve", source="remote")

    assert transports.starts == [tid]
    assert reply["reply_markup"]["inline_keyboard"] == []  # buttons removed
    assert pw.get(tid)["status"] == "approved" and pw.get(tid)["dispatch_key"] == f"pi-write-dispatch-{tid}"
    assert transports.accepted == [(9001, tid)]

    again = pw.decide(tid, "approve", source="remote")
    assert transports.starts == [tid] and "already approved" in again["text"]


def test_voice_decision_entry_point_dispatches_the_named_ticket(store, transports):
    """The remote / desktop voice path: the kanban tool's proposal decision."""
    from distr.core.agent.tools.integrations.kanban_ticket import KanbanTicketTool
    from distr.core.initiative import pi_write_proposals as pw

    ids = _seed(store)
    out = pw.file_write_proposal(ids.project, "Synthetic Shop", "Swap the checkout script", _sync=True)
    tid = out["ticket_id"]
    KanbanTicketTool()._action_decide_proposal("approve", tid)
    assert transports.starts == [tid]


def test_tensology_decision_reaches_the_same_identity_and_statuses_are_distinct(store, transports):
    from distr.core.initiative import pi_write_proposals as pw

    ids = _seed(store)
    out = pw.file_write_proposal(ids.project, "Synthetic Shop", "Remove the old banner", _sync=True)
    tid = out["ticket_id"]
    reply = pw.handle_remote_decision({"request_id": 9001, "status": "dismissed", "event": "proposal_decided"})
    assert transports.starts == [] and pw.get(tid)["status"] == "declined"
    assert "Declined." in reply["text"] and reply.get("suppress") is True

    out2 = pw.file_write_proposal(ids.project, "Synthetic Shop", "Rename the banner", _sync=True)
    assert pw.get(out2["ticket_id"])["status"] == "pending"


# 4. Presence routing: distinct remote signals, desktop default, one nudge ----------------------------------

class DummyTelegram:
    telegram_user_id = 12345

    def is_connected(self):
        return True


def test_approval_remote_route_requires_open_and_recent_interaction():
    from distr.core import notification_routing as nr

    nr.clear_notification_activity_cache()
    nr.record_surface_activity("remote_request", at=108)
    assert nr.choose_notification_route(telegram_manager=DummyTelegram(), allow_telegram=True, now=130,
                                        strict_presence=True).surface == "desktop"
    nr.record_surface_activity("telegram", at=129)
    assert nr.choose_notification_route(telegram_manager=DummyTelegram(), allow_telegram=True, now=130,
                                        strict_presence=True).surface == "telegram"
    nr.record_surface_activity("remote", at=105)
    assert nr.choose_notification_route(telegram_manager=DummyTelegram(), allow_telegram=True, now=130,
                                        strict_presence=True).surface == "remote"

    nr.clear_notification_activity_cache()
    nr.record_surface_activity("remote", at=105)
    assert nr.choose_notification_route(telegram_manager=DummyTelegram(), allow_telegram=True, now=130,
                                        strict_presence=True).surface == "desktop"


def test_desktop_is_the_approval_default_but_other_callers_keep_none():
    from distr.core import notification_routing as nr
    from distr.core.initiative import approval_notice as an

    nr.clear_notification_activity_cache()
    # Other callers keep the None they rely on (so they can fall back to silent / telegram themselves).
    assert nr.choose_notification_route(telegram_manager=DummyTelegram(), allow_telegram=False, now=130) is None
    assert nr.choose_notification_route(telegram_manager=DummyTelegram(), allow_telegram=False, now=130,
                                        strict_presence=True).surface == "desktop"
    # The approval surface asks the router for that desktop default.
    assert an.primary_surface(manager=DummyTelegram(), allow_telegram=False) == "desktop"


def test_ask_once_nudges_only_once_per_key(monkeypatch):
    from distr.core.initiative import approval_notice as an

    monkeypatch.setattr(an, "primary_surface", lambda manager=None, **k: "remote")
    delivered = []
    monkeypatch.setattr(an, "_deliver", lambda surface, spoken, written, data=None: delivered.append((surface, spoken)) or True)
    first = an.ask_once("pi-write-dispatch-335", "Approve ticket 335?", surface="remote")
    second = an.ask_once("pi-write-dispatch-335", "Approve ticket 335?", surface="remote")
    assert first == {"surface": "remote", "sent": True, "first": True}
    assert second["sent"] is False and second["first"] is False and len(delivered) == 1


# 5. "Approve all" lists and asks for one named ticket; it never dispatches ---------------------------------

def _proposals(*ticket_ids):
    return [{"kind": "proposal", "ref": str(t), "ticket_id": t, "title": f"ticket {t} change", "at": 0.0}
            for t in ticket_ids]


@pytest.mark.parametrize("phrase", ["approve all", "approve everything",
                                    "confirm approve all", "confirm approve everything"])
def test_batch_phrase_never_dispatches_even_when_confirmed(monkeypatch, phrase):
    from distr.core.initiative import pi_write_proposals as pw
    from distr.core.initiative.approval_notice import decide_pending

    dispatched = []
    monkeypatch.setattr(pw, "decide", lambda tid, decision, source="remote": dispatched.append((tid, decision)) or {"text": "x"})
    out = decide_pending(phrase, items=_proposals(335, 336))
    assert "approve 335" in out["text"] and "approve 336" in out["text"]
    assert "Nothing has been approved" in out["text"]
    assert dispatched == []


def test_approve_all_with_one_pending_lists_that_one():
    from distr.core.initiative.approval_notice import decide_pending

    out = decide_pending("approve all", items=_proposals(335))
    assert "approve 335" in out["text"] and "Nothing has been approved" in out["text"]


def test_approve_all_with_no_pending_says_nothing_waiting():
    from distr.core.initiative.approval_notice import approve_all_preview, decide_pending

    assert "Nothing is waiting" in decide_pending("approve all", items=[])["text"]
    empty = approve_all_preview(items=[])
    assert empty["count"] == 0 and "Nothing is waiting" in empty["text"]


def test_named_ticket_phrase_goes_through_the_single_ticket_path(store, transports):
    from distr.core.initiative import pi_write_proposals as pw
    from distr.core.initiative.approval_notice import decide_pending

    ids = _seed(store)
    out = pw.file_write_proposal(ids.project, "Synthetic Shop", "Swap the checkout script", _sync=True)
    tid = out["ticket_id"]
    item = {"kind": "proposal", "ref": str(tid), "ticket_id": tid, "title": "Swap the checkout script", "at": 0.0}
    result = decide_pending(f"approve {tid}", items=[item])
    assert result["ok"] is True and transports.starts == [tid]


# 6. Staleness: atomic single-callback refusal, cleanup retires card + mirror, concurrency -------------------

def test_old_telegram_callback_on_a_stale_proposal_does_not_dispatch(store, transports):
    from distr.core.initiative.outcome_flow import handle_telegram_text
    from distr.core.initiative import pi_write_proposals as pw

    ids = _seed(store)
    out = pw.file_write_proposal(ids.project, "Synthetic Shop", "Old change", _sync=True)
    tid = out["ticket_id"]
    state = pw._load()
    state[str(tid)]["created"] = time.time() - 5 * 24 * 3600
    pw._save(state)

    reply = handle_telegram_text(f"pw:{tid}:approve", chat_id=555, telegram_message_id=778)
    assert transports.starts == []
    assert pw.get(tid)["status"] == "expired"
    assert reply["reply_markup"]["inline_keyboard"] == [] and "Expired" in reply["text"]
    assert reply["edit_message_id"] == 778
    assert transports.dismissed == [9001]


def test_cleanup_expires_and_retires_the_card_and_mirror(store, transports):
    from distr.core.initiative import approval_notice as an
    from distr.core.initiative import pi_write_proposals as pw

    ids = _seed(store)
    out = pw.file_write_proposal(ids.project, "Synthetic Shop", "Old change", _sync=True)
    tid = out["ticket_id"]
    state = pw._load()
    state[str(tid)]["created"] = time.time() - 5 * 24 * 3600
    pw._save(state)

    expired = an.expire_stale_approvals()
    assert {e["ticket_id"] for e in expired} == {tid}
    assert pw.get(tid)["status"] == "expired"
    assert transports.edits and "Expired" in transports.edits[-1][0]  # live buttons retired
    assert transports.dismissed == [9001]                             # mirror retired, no new ask


def test_stale_proposal_for_a_closed_ticket_expires(store, transports, monkeypatch):
    from distr.core.initiative import approval_notice as an
    from distr.core.initiative import pi_write_proposals as pw

    ids = _seed(store)
    out = pw.file_write_proposal(ids.project, "Synthetic Shop", "Live-looking change", _sync=True)
    monkeypatch.setattr(an, "_closed_ticket_ids", lambda: {out["ticket_id"]})
    assert {e["ticket_id"] for e in an.expire_stale_approvals()} == {out["ticket_id"]}


@pytest.mark.parametrize("ticket_ids, old", [([335, 336], False), ([335], False), ([337], True)])
def test_canonical_draft_approval_cannot_run_batched_closed_or_old_lane_moves(tmp_path, monkeypatch, ticket_ids, old):
    from distr.core.initiative import approval_notice as an, draft_execute
    from distr.core.initiative.draft_queue import DraftEntry, DraftQueue

    monkeypatch.setattr(an, "_closed_ticket_ids", lambda: {335} if ticket_ids == [335] else set())
    monkeypatch.setattr(draft_execute, "run_execute_payload", lambda _: pytest.fail("stale draft dispatched"))
    now = datetime.now(tz=timezone.utc)
    queue = DraftQueue(str(tmp_path / "lane-drafts.json"))
    assert queue.add(DraftEntry(
        id="old-lane-move", action_type="ticket_lane_move", description="Move synthetic ticket", draft="",
        reason="approval", created_at=(now - timedelta(hours=49 if old else 1)).isoformat(),
        expires_at=(now + timedelta(hours=2)).isoformat(),
        execute_payload={"kind": "initiative_action", "action": {
            "action_type": "ticket_lane_move", "payload": {"ticket_ids": ticket_ids, "target_lane": "QA"},
        }},
    ))
    assert draft_execute.approve_draft_in_queue(queue, "old-lane-move") is False
    assert queue.get_by_id("old-lane-move") is None


def test_two_proposals_decided_concurrently_dispatch_once_each(store, transports):
    from distr.core.initiative import pi_write_proposals as pw

    ids = _seed(store)
    one = pw.file_write_proposal(ids.project, "Synthetic Shop", "Change one", _sync=True)["ticket_id"]
    two = pw.file_write_proposal(ids.project, "Synthetic Shop", "Change two", _sync=True)["ticket_id"]

    def decide(tid):
        pw.decide(tid, "approve", source="remote")

    threads = [threading.Thread(target=decide, args=(t,)) for t in (one, two, one, two)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(10)
    assert sorted(transports.starts) == sorted([one, two])
    assert pw.get(one)["status"] == "approved" and pw.get(two)["status"] == "approved"


def test_telegram_acceptance_refuses_a_draft_tied_to_a_closed_ticket(monkeypatch, tmp_path):
    from unittest.mock import MagicMock

    from distr.core.initiative import approval_notice as an
    from distr.core.initiative.draft_queue import DraftEntry, DraftQueue
    from distr.core.integrations.telegram.messages import TelegramMessagesMixin

    queue = DraftQueue(path=str(tmp_path / "drafts.json"))
    queue.add(DraftEntry(
        id="lane-one", action_type="ticket_lane_move", description="Move ticket 335 to In Progress",
        draft="Board change", reason="Work scan", created_at=datetime.now(tz=timezone.utc).isoformat(),
        expires_at=(datetime.now(tz=timezone.utc) + timedelta(hours=1)).isoformat(),
        execute_payload={"ticket_ids": [335], "target_lane": "In Progress"},
    ))
    monkeypatch.setattr(an, "_closed_ticket_ids", lambda: {335})
    approved = []
    monkeypatch.setattr("distr.core.initiative.draft_execute.approve_draft_in_queue",
                        lambda q, draft_id: approved.append(draft_id) or True)
    handler = TelegramMessagesMixin()
    handler.send_to_telegram = MagicMock()

    assert handler._apply_initiative_draft_decision(queue, "lane-one", "approve") is True
    assert approved == []
    assert "stale" in handler.send_to_telegram.call_args.args[0].lower()


# 7. "Working on it" only after execution; the openai_compat entry point says a factual queued status --------

def test_working_line_waits_for_real_execution():
    from distr.core.initiative.approval_notice import preparing_status, working_or_queued_line

    queued = working_or_queued_line(started=False, what="start Pi on ticket 335", waiting_on="your approval")
    assert "Working on it" not in queued and "Queued" in queued and "Waiting on your approval" in queued
    assert working_or_queued_line(started=True, what="start Pi on ticket 335") == "Working on it."
    assert preparing_status("hand that to Pi") == "Queued: hand that to Pi."
    assert preparing_status() == "Queued: getting ready to start."


def test_openai_compat_pre_execution_acknowledgment_is_not_working_on_it(monkeypatch):
    from distr.core.agent.services.llm.openai_compat import OpenAICompatibleLLMService

    spoken = []
    monkeypatch.setattr("distr.core.signals.speak_text_directly_event_queue", lambda text: spoken.append(text))
    svc = OpenAICompatibleLLMService.__new__(OpenAICompatibleLLMService)
    svc._is_telegram_request = False
    svc._speaker_enabled = True
    svc._should_speak_acknowledgment = lambda last, calls: True

    svc._speak_acknowledgment("please investigate the checkout in the synthetic shop",
                              [{"function": {"name": "pi_agent"}}])
    assert spoken and spoken[0].startswith("Queued")
    assert "Working on it." not in spoken[0]


# 8. Incoming: stale / approval-managed rows cannot be started or merged ------------------------------------

def test_incoming_start_guard_blocks_expired_and_approval_managed(store, transports):
    from distr.core.incoming.proposals import require_startable
    from distr.core.initiative import pi_write_proposals as pw

    ids = _seed(store)
    pw.file_write_proposal(ids.project, "Synthetic Shop", "Change the banking line", _sync=True)

    with pytest.raises(ValueError, match="Approve it in Decisions"):
        require_startable({"id": "tensology:9001"}, verb="start")

    old = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
    with pytest.raises(ValueError, match="expired"):
        require_startable({"id": "tensology:4242", "message_ids": ["m1"],
                           "evidence": [{"id": "m1", "sent_at": old}]}, verb="merge")

    # A fresh, unrelated proposal is still startable.
    require_startable({"id": "tensology:4243", "message_ids": ["m2"],
                       "evidence": [{"id": "m2", "sent_at": datetime.now(timezone.utc).isoformat()}]}, verb="start")
