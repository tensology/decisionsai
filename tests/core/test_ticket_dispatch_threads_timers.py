"""Ticket dispatch: explicit ticket, background run, thread + timer, configured worker, Pi write proposals.

Synthetic data only: boards, projects and tickets here are made up.
"""
from __future__ import annotations

import contextlib
import logging
import threading
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from distr.core.agent.tools.integrations.kanban_ticket import KanbanTicketTool
from distr.core.db import Base, Chat
from distr.core.db.kanban import KanbanBoard, KanbanLane, KanbanTicket
from distr.core.db.projects import Project
from distr.core.db.workflow import AutoWorkflow, DevelopmentTimeEntry, DevelopmentWorkItem


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

    monkeypatch.setattr(KanbanTicketTool, "_get_session", lambda self: session())
    monkeypatch.setattr("distr.core.incoming.media.sync_thread_files_safely", lambda *a, **k: None)
    yield session
    engine.dispose()


@pytest.fixture(autouse=True)
def no_tensology(monkeypatch):
    """Never reach the real Tensology: record what would be mirrored or dismissed."""
    calls = SimpleNamespace(posted=[], dismissed=[])
    monkeypatch.setattr("distr.core.initiative.pi_write_proposals._default_post_incoming",
                        lambda payload, key: calls.posted.append((payload, key)) or {"id": 9001})
    monkeypatch.setattr("distr.core.initiative.pi_write_proposals._default_dismiss_incoming",
                        lambda tid: calls.dismissed.append(tid))
    monkeypatch.setattr("distr.core.initiative.pi_write_proposals._default_notify", lambda text, markup: True)
    return calls


def _seed(session, *, source_chat_id=None):
    with session() as db:
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
        older = KanbanTicket(lane_id=lanes["Backlog"].id, title="Older unrelated alert", position=0,
                             linked_project_id=project.id)
        wanted = KanbanTicket(lane_id=lanes["Backlog"].id, title="Change the banking line", position=1,
                              linked_project_id=project.id, source_chat_id=source_chat_id)
        db.add_all([older, wanted])
        db.commit()
        return SimpleNamespace(project=project.id, board=board.id, older=older.id, wanted=wanted.id,
                               lanes={k: v.id for k, v in lanes.items()})


# 2. explicit ticket only ------------------------------------------------------------------

def test_send_to_cli_never_falls_back_to_a_remembered_ticket(store, monkeypatch):
    ids = _seed(store)
    seen = []
    monkeypatch.setattr(KanbanTicketTool, "_action_send_to_cli", lambda self, tid: seen.append(tid) or "ok")
    tool = KanbanTicketTool()
    tool._last_ticket_id = ids.older
    tool._run(action="send_to_cli")
    assert len(seen) == 1 and not seen[0]  # no ticket named: nothing remembered is used


def test_send_to_cli_without_ticket_sends_nothing(store):
    _seed(store)
    message = KanbanTicketTool()._action_send_to_cli(None)
    assert "explicit ticket_id" in message
    with store() as db:
        assert db.query(AutoWorkflow).count() == 0
        assert db.query(DevelopmentWorkItem).count() == 0


# 3 + 5. background run on the ticket's thread with its timer ------------------------------

def test_send_to_cli_returns_at_once_and_runs_on_the_ticket_thread(store, monkeypatch):
    ids = _seed(store, source_chat_id=None)
    release = threading.Event()
    calls: dict = {}
    reports: list = []

    decision = SimpleNamespace(to_route_dict=lambda: {"backend": "pi", "model": "synthetic-worker:cloud",
                                                      "model_provider": "ollama", "complexity": "medium"})
    monkeypatch.setattr("distr.core.orchestrator_routing.resolve_execution_route", lambda **k: decision)
    monkeypatch.setattr("distr.core.kanban.ticket_cli_context.build_kanban_ticket_cli_instruction",
                        lambda *a, **k: "Change the banking line on the synthetic site.")
    monkeypatch.setattr("distr.core.kanban.lifecycle.move_ticket_to_delivery_lane", lambda *a, **k: True)
    monkeypatch.setattr("distr.core.kanban.ticket_work_thread.ensure_ticket_work_thread", lambda tid, **k: 4242)
    monkeypatch.setattr("distr.core.kanban.ticket_work_thread.start_ticket_run_time",
                        lambda cid, **k: calls.setdefault("started", cid))
    monkeypatch.setattr("distr.core.kanban.ticket_work_thread.finish_ticket_run_time",
                        lambda cid, **k: calls.setdefault("finished", cid))
    monkeypatch.setattr("distr.core.initiative.job_progress.report",
                        lambda job, kind, text="", **k: reports.append((job, kind)))

    async def fake_run(project, instruction, **kwargs):
        calls["run_kwargs"] = kwargs
        calls["instruction"] = instruction
        release.wait(5)
        return SimpleNamespace(success=True, output="done", error=None)

    monkeypatch.setattr("distr.core.project_cli_backends.run_project_task", fake_run)

    message = KanbanTicketTool()._action_send_to_cli(ids.wanted, approved=True)

    assert "is running" in message and "#4242" in message
    assert "finished" not in calls  # the turn came back while the run is still going
    release.set()
    worker = next(t for t in threading.enumerate() if t.name == f"ticket-cli-{ids.wanted}")
    worker.join(5)
    assert calls["started"] == 4242 and calls["finished"] == 4242
    assert calls["run_kwargs"]["chat_id"] == 4242
    assert calls["run_kwargs"]["ticket_id"] == ids.wanted
    assert calls["run_kwargs"]["model_override"] == "synthetic-worker:cloud"
    assert not calls["instruction"].startswith("READ ONLY.")  # approved work runs as written
    assert (f"ticket:{ids.wanted}", "picked_up") in reports
    assert (f"ticket:{ids.wanted}", "done") in reports


def test_ensure_ticket_work_thread_creates_one_thread_and_releases_a_chat_link(store):
    from distr.core.kanban.ticket_work_thread import ensure_ticket_work_thread

    with store() as db:
        conversation = Chat(title="Synthetic chat conversation")
        db.add(conversation)
        db.commit()
        conversation_id = conversation.id
    ids = _seed(store, source_chat_id=conversation_id)

    first = ensure_ticket_work_thread(ids.wanted, _session_provider=store)
    again = ensure_ticket_work_thread(ids.wanted, _session_provider=store)

    assert first and first == again and first != conversation_id
    with store() as db:
        item = db.query(DevelopmentWorkItem).filter_by(chat_id=first).one()
        assert item.local_ticket_id == ids.wanted
        assert item.project_id == ids.project
        assert db.get(KanbanTicket, ids.wanted).source_chat_id == first
        assert db.get(Chat, first).project_id == ids.project


def test_thread_timer_starts_with_the_run_and_records_a_time_entry_when_it_ends(store):
    from datetime import timedelta

    from distr.core.db.time import utc_now_naive
    from distr.core.kanban.ticket_work_thread import (
        ensure_ticket_work_thread,
        finish_ticket_run_time,
        start_ticket_run_time,
    )

    ids = _seed(store)
    chat_id = ensure_ticket_work_thread(ids.wanted, _session_provider=store)
    assert start_ticket_run_time(chat_id, _session_provider=store)
    with store() as db:
        item = db.query(DevelopmentWorkItem).filter_by(chat_id=chat_id).one()
        assert item.time_paused is False and item.time_started_at is not None
        item.time_started_at = utc_now_naive() - timedelta(seconds=90)
        db.commit()

    assert finish_ticket_run_time(chat_id, _session_provider=store)
    with store() as db:
        item = db.query(DevelopmentWorkItem).filter_by(chat_id=chat_id).one()
        assert item.time_paused is True and item.time_started_at is None
        entry = db.query(DevelopmentTimeEntry).filter_by(chat_id=chat_id).one()
        assert entry.source == "auto_pause" and 85 <= entry.seconds <= 100
        assert item.time_accumulated_seconds == entry.seconds
    assert finish_ticket_run_time(chat_id, _session_provider=store) is False  # already paused


def test_moving_a_ticket_updates_its_thread_timer_by_lane(store, monkeypatch):
    ids = _seed(store)
    moves = []
    monkeypatch.setattr("distr.core.kanban.ticket_work_thread.apply_ticket_lane_time",
                        lambda tid, lane, **k: moves.append((int(tid), lane)) or "started")
    KanbanTicketTool()._action_move_ticket(ids.wanted, "In Progress")
    KanbanTicketTool()._action_move_ticket(ids.wanted, "Backlog")
    assert moves == [(ids.wanted, "In Progress"), (ids.wanted, "Backlog")]


def _timer(store, chat_id):
    with store() as db:
        item = db.query(DevelopmentWorkItem).filter_by(chat_id=chat_id).one()
        entries = db.query(DevelopmentTimeEntry).filter_by(chat_id=chat_id).all()
        return item, entries


def _age_running_segment(store, chat_id, seconds):
    from datetime import timedelta

    from distr.core.db.time import utc_now_naive

    with store() as db:
        item = db.query(DevelopmentWorkItem).filter_by(chat_id=chat_id).one()
        item.time_started_at = utc_now_naive() - timedelta(seconds=seconds)
        item.time_last_activity_at = utc_now_naive()
        db.commit()


@pytest.mark.parametrize("out_lane", ["Complete", "Done", "Backlog"])
def test_in_progress_starts_the_timer_and_leaving_it_pauses_it(store, out_lane):
    from distr.core.kanban.ticket_work_thread import apply_ticket_lane_time

    ids = _seed(store)
    assert apply_ticket_lane_time(ids.wanted, "In Progress", _session_provider=store) == "started"
    with store() as db:
        chat_id = db.get(KanbanTicket, ids.wanted).source_chat_id
    item, entries = _timer(store, chat_id)
    assert item.time_paused is False and item.time_started_at is not None and entries == []
    _age_running_segment(store, chat_id, 120)

    assert apply_ticket_lane_time(ids.wanted, out_lane, _session_provider=store) == "paused"
    item, entries = _timer(store, chat_id)
    assert item.time_paused is True and len(entries) == 1 and 115 <= entries[0].seconds <= 130
    assert apply_ticket_lane_time(ids.wanted, out_lane, _session_provider=store) == "unchanged"
    # A ticket that never had a thread is left alone when it moves out.
    assert apply_ticket_lane_time(ids.older, "Complete", _session_provider=store) == "unchanged"


def test_lane_time_and_worker_run_time_share_one_timer(store):
    from distr.core.kanban.ticket_work_thread import (
        apply_ticket_lane_time,
        finish_ticket_run_time,
        start_ticket_run_time,
    )

    ids = _seed(store)
    apply_ticket_lane_time(ids.wanted, "In Progress", _session_provider=store)
    with store() as db:
        chat_id = db.get(KanbanTicket, ids.wanted).source_chat_id
    _age_running_segment(store, chat_id, 60)
    started_at = _timer(store, chat_id)[0].time_started_at
    assert start_ticket_run_time(chat_id, _session_provider=store)  # the worker run starts on the same timer
    item, entries = _timer(store, chat_id)
    assert item.time_started_at == started_at and entries == []  # no second segment opened
    assert finish_ticket_run_time(chat_id, _session_provider=store)
    assert apply_ticket_lane_time(ids.wanted, "Complete", _session_provider=store) == "unchanged"
    item, entries = _timer(store, chat_id)
    assert len(entries) == 1 and 55 <= entries[0].seconds <= 70
    assert item.time_accumulated_seconds == entries[0].seconds


# 6. Pi write work waits for approval -------------------------------------------------------

@pytest.mark.parametrize("text,write", [
    ("Implement the new banking details in the checkout email", True),
    ("Fix the failing test and push it", True),
    ("Find where the banking details are stored and report back", False),
    ("Run the test suite and tell me what fails", False),
])
def test_write_instructions_are_recognised(text, write):
    from distr.core.initiative.pi_write_proposals import is_write_instruction

    assert is_write_instruction(text) is write


def test_pi_write_proposal_files_a_backlog_ticket_and_waits_for_approval(store, monkeypatch, tmp_path):
    from distr.core.initiative import pi_write_proposals as pw

    monkeypatch.setenv("DECISIONS_DB_DIR", str(tmp_path))
    ids = _seed(store)
    cards = []
    out = pw.file_write_proposal(ids.project, "Synthetic Shop", "Update the banking line on the synthetic site",
                                 notify=lambda text, markup: cards.append((text, markup)) or True,
                                 _session_provider=store)
    assert out["status"] == "proposed" and out["card_sent"] is True
    tid = out["ticket_id"]
    with store() as db:
        ticket = db.get(KanbanTicket, tid)
        assert db.get(KanbanLane, ticket.lane_id).name == "Backlog"
        assert ticket.send_to_cli is False
        assert db.query(AutoWorkflow).count() == 0  # nothing dispatched
    buttons = [b["callback_data"] for b in cards[0][1]["inline_keyboard"][0]]
    assert buttons == [f"pw:{tid}:approve", f"pw:{tid}:decline"]
    assert pw.get(tid)["status"] == "pending"

    started = []
    reply = pw.decide(tid, "approve", start=lambda t: started.append(t) or "Ticket is running.\n\nREFERENCE:\nx")
    assert started == [tid] and reply["text"].startswith("Approved. Ticket is running.")
    assert pw.decide(tid, "approve", start=lambda t: started.append(t))["text"].endswith("Nothing changed.")
    assert started == [tid]


def test_declined_pi_write_proposal_starts_nothing(store, monkeypatch, tmp_path):
    from distr.core.initiative import pi_write_proposals as pw

    monkeypatch.setenv("DECISIONS_DB_DIR", str(tmp_path))
    ids = _seed(store)
    out = pw.file_write_proposal(ids.project, "Synthetic Shop", "Remove the old banner",
                                 notify=lambda *a: True, _session_provider=store)
    started = []
    assert "Declined" in pw.decide(out["ticket_id"], "decline", start=lambda t: started.append(t))["text"]
    assert started == [] and pw.get(out["ticket_id"])["status"] == "declined"


def test_telegram_card_buttons_reach_the_pi_write_decision(monkeypatch):
    from distr.core.initiative import outcome_flow

    seen = []
    monkeypatch.setattr("distr.core.initiative.pi_write_proposals.decide",
                        lambda key, action: seen.append((key, action)) or {"text": "ok"})
    assert outcome_flow.handle_telegram_text("pw:123:approve", chat_id=555) == {"text": "ok"}
    assert seen == [("123", "approve")]


def test_pi_agent_files_a_proposal_for_writes_and_runs_reads_read_only(monkeypatch):
    from distr.core.agent.tools.integrations import pi_agent as mod

    tool = mod.PiAgentTool()
    monkeypatch.setattr("distr.core.pi_rpc.PiRpcSession.find_pi", staticmethod(lambda: "/usr/bin/pi"))
    monkeypatch.setattr(mod.PiAgentTool, "_resolve_project_folder", lambda self, n: "/tmp/synthetic-shop")
    monkeypatch.setattr(mod.PiAgentTool, "_get_project_id", lambda self, n: 7)
    monkeypatch.setattr(mod.PiAgentTool, "_build_project_context", lambda self, *a: "")
    monkeypatch.setattr(mod.PiAgentTool, "_create_audit", lambda self, *a, **k: None)
    monkeypatch.setattr("distr.core.signals.speak_text_directly_event_queue", lambda *a, **k: None)
    filed, sent = [], []
    monkeypatch.setattr("distr.core.initiative.pi_write_proposals.file_write_proposal",
                        lambda pid, name, instr, **k: filed.append(instr) or {"message": "Proposal filed."})
    monkeypatch.setattr("distr.core.agent.tools.integrations.unified_cli.dispatch_to_cli",
                        lambda **k: sent.append(k["instruction"]) or {"success": True, "message": "sent"})

    out = tool._run(instruction="Implement the new banking line", project_name="Synthetic Shop")
    assert "Proposal filed." in out and filed and not sent

    tool._run(instruction="Find where the banking line is stored", project_name="Synthetic Shop")
    assert sent and sent[0].startswith("READ ONLY.")


# 7. job status delivery is logged ----------------------------------------------------------

def test_job_status_delivery_is_logged_at_info(monkeypatch, caplog):
    from distr.core.initiative import job_progress as jp

    monkeypatch.setattr(jp, "_remote_active", lambda: True)
    monkeypatch.setattr(jp, "_remote_push", lambda payload: True)
    monkeypatch.setattr(jp, "enabled", lambda: True)
    caplog.set_level(logging.INFO, logger="distr.core.initiative.job_progress")
    jp.report("test:synthetic-job", "picked_up", "synthetic work", title="Synthetic")
    jp.flush("test:synthetic-job")
    assert any("route=remote" in r.getMessage() and "test:synthetic-job" in r.getMessage() for r in caplog.records)

    monkeypatch.setattr(jp, "_remote_active", lambda: False)
    monkeypatch.setattr(jp, "_telegram_send", lambda text, token: False)
    jp.report("test:synthetic-job-2", "failed", "synthetic failure", title="Synthetic")
    jp.flush("test:synthetic-job-2")
    assert any("delivery failed" in r.getMessage() and "test:synthetic-job-2" in r.getMessage() for r in caplog.records)


# Direct send_to_cli with an explicit ticket goes through the same approval card ------------

def _route_and_run(monkeypatch, calls):
    decision = SimpleNamespace(to_route_dict=lambda: {"backend": "pi", "model": "synthetic-worker:cloud",
                                                      "model_provider": "ollama", "complexity": "medium"})
    monkeypatch.setattr("distr.core.orchestrator_routing.resolve_execution_route", lambda **k: decision)
    monkeypatch.setattr("distr.core.kanban.ticket_cli_context.build_kanban_ticket_cli_instruction",
                        lambda *a, **k: "Ticket instruction for the synthetic site.")
    monkeypatch.setattr("distr.core.kanban.lifecycle.move_ticket_to_delivery_lane", lambda *a, **k: True)
    monkeypatch.setattr("distr.core.kanban.ticket_work_thread.ensure_ticket_work_thread", lambda tid, **k: None)
    monkeypatch.setattr("distr.core.kanban.ticket_work_thread.finish_ticket_run_time", lambda cid, **k: False)

    async def fake_run(project, instruction, **kwargs):
        calls.append(instruction)
        return SimpleNamespace(success=True, output="done", error=None)

    monkeypatch.setattr("distr.core.project_cli_backends.run_project_task", fake_run)


def test_chat_send_to_cli_of_a_code_change_waits_for_the_card(store, monkeypatch, tmp_path, no_tensology):
    from distr.core.agent.tools.integrations.kanban_ticket import AWAITING_APPROVAL_MARK
    from distr.core.initiative import pi_write_proposals as pw

    monkeypatch.setenv("DECISIONS_DB_DIR", str(tmp_path))
    ids = _seed(store)  # "Change the banking line" would change code
    runs: list = []
    _route_and_run(monkeypatch, runs)
    cards = []
    monkeypatch.setattr(pw, "_default_notify", lambda text, markup: cards.append(markup) or True)
    monkeypatch.setattr(pw, "_mirror_to_incoming", lambda *a: None)  # mirror covered below

    out = KanbanTicketTool()._run(action="send_to_cli", ticket_id=ids.wanted)
    assert AWAITING_APPROVAL_MARK in out and "waits for your approval" in out
    again = KanbanTicketTool()._run(action="send_to_cli", ticket_id=ids.wanted)
    assert "already waiting" in again
    assert len(cards) == 1
    assert [b["callback_data"] for b in cards[0]["inline_keyboard"][0]] == [f"pw:{ids.wanted}:approve", f"pw:{ids.wanted}:decline"]
    with store() as db:
        assert db.query(AutoWorkflow).count() == 0  # nothing dispatched
    assert runs == []

    seen = []
    monkeypatch.setattr(KanbanTicketTool, "_action_send_to_cli",
                        lambda self, tid, approved=False: seen.append((tid, approved)) or "Ticket is running.")
    assert pw.decide(ids.wanted, "approve")["text"].startswith("Approved.")
    assert seen == [(ids.wanted, True)]


def test_chat_send_to_cli_of_a_read_only_ticket_runs_read_only(store, monkeypatch, tmp_path):
    monkeypatch.setenv("DECISIONS_DB_DIR", str(tmp_path))
    ids = _seed(store)
    with store() as db:
        db.get(KanbanTicket, ids.older).title = "Report which pages load slowly"
        db.commit()
    runs: list = []
    _route_and_run(monkeypatch, runs)
    out = KanbanTicketTool()._run(action="send_to_cli", ticket_id=ids.older)
    assert "is running" in out
    worker = next(t for t in threading.enumerate() if t.name == f"ticket-cli-{ids.older}")
    worker.join(5)
    assert runs and runs[0].startswith("READ ONLY.")


# Proposals reach Tensology's Incoming as well as Telegram ----------------------------------

def test_proposal_is_mirrored_to_tensology_incoming_and_decline_dismisses_it(store, monkeypatch, tmp_path, no_tensology):
    from distr.core.initiative import pi_write_proposals as pw

    monkeypatch.setenv("DECISIONS_DB_DIR", str(tmp_path))
    ids = _seed(store)
    out = pw.file_write_proposal(ids.project, "Synthetic Shop", "Replace the card form with hosted checkout",
                                 _sync=True, _session_provider=store)
    payload, key = no_tensology.posted[0]
    assert key == f"pi-write-{out['ticket_id']}"
    assert payload["origin"] == "decisions" and payload["origin_ref"] == f"decisions-ticket-{out['ticket_id']}"
    assert payload["decisions_project_id"] == ids.project
    assert payload["instruction"] == "Replace the card form with hosted checkout"
    assert pw.get(out["ticket_id"])["tensology_id"] == 9001
    pw.decide(out["ticket_id"], "decline")
    assert no_tensology.dismissed == [9001]


def test_tensology_outage_does_not_stop_the_telegram_card(store, monkeypatch, tmp_path):
    from distr.core.initiative import pi_write_proposals as pw

    monkeypatch.setenv("DECISIONS_DB_DIR", str(tmp_path))
    ids = _seed(store)

    def down(payload, key):
        raise RuntimeError("synthetic outage")

    out = pw.file_write_proposal(ids.project, "Synthetic Shop", "Rename the banner", post_incoming=down,
                                 _sync=True, _session_provider=store)
    assert out["status"] == "proposed" and out["card_sent"] is True
    assert "tensology_id" not in pw.get(out["ticket_id"])
