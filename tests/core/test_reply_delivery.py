"""Closing replies must be deterministic, gated, and sent at most once."""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import distr.core.db.kanban  # noqa: F401
import distr.core.db.orchestrator  # noqa: F401
import distr.core.db.workflow  # noqa: F401
from distr.core.db import Base
from distr.core.db.kanban import KanbanBoard, KanbanLane, KanbanTicket
from distr.core.workflow import reply_delivery


@pytest.fixture()
def db_factory(monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    # expire_on_commit=True mirrors production: reading a detached ORM row raises,
    # which is how the reply path was silently skipping.
    factory = sessionmaker(bind=engine, expire_on_commit=True)
    monkeypatch.setattr("distr.core.db.get_session", lambda: factory())
    return factory


def _ticket(factory, *, title="Fix the checkout button", **overrides) -> int:
    with factory() as db:
        board = KanbanBoard(name="Reply board")
        db.add(board)
        db.flush()
        lane = KanbanLane(board_id=board.id, name="Ready", position=0)
        db.add(lane)
        db.flush()
        values = {
            "lane_id": lane.id,
            "title": title,
            "source_provider": "whatsapp",
            "source_thread_id": "27634103646",
        }
        values.update(overrides)
        ticket = KanbanTicket(**values)
        db.add(ticket)
        db.commit()
        return int(ticket.id)


@pytest.fixture()
def sent(monkeypatch):
    calls: list[dict] = []

    def _adapter(*, target, body, subject=""):
        calls.append({"target": target, "body": body, "subject": subject})
        return {"success": True, "target": target}

    monkeypatch.setitem(reply_delivery.ADAPTERS, "whatsapp", _adapter)
    monkeypatch.setitem(reply_delivery.ADAPTERS, "email", _adapter)
    return calls


@pytest.fixture()
def approved(monkeypatch):
    monkeypatch.setattr(
        "distr.core.workflow.dev_approvals.gate",
        lambda *args, **kwargs: None,
    )


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def test_extract_reply_text_reads_the_contract_field():
    packet = "status: done\n\nreply_text: All fixed, thanks for the nudge.\n\nnotes: internal"

    assert reply_delivery.extract_reply_text(packet) == "All fixed, thanks for the nudge."


def test_extract_reply_text_prefers_the_last_non_empty_match():
    packet = "reply_text: first draft\n\nreply_text: final wording"

    assert reply_delivery.extract_reply_text(packet) == "final wording"


def test_extract_reply_text_is_empty_when_missing():
    assert reply_delivery.extract_reply_text("status: done\n\nfiles: a.py") == ""
    assert reply_delivery.extract_reply_text("") == ""


def test_reply_key_is_stable_and_body_specific():
    first = reply_delivery.reply_key(1, "whatsapp", "abc", "hello")

    assert first == reply_delivery.reply_key(1, "whatsapp", "abc", "hello")
    assert first != reply_delivery.reply_key(1, "whatsapp", "abc", "hello there")
    assert first != reply_delivery.reply_key(2, "whatsapp", "abc", "hello")


# ---------------------------------------------------------------------------
# Delivery
# ---------------------------------------------------------------------------


def test_short_reply_on_a_normal_ticket_is_sent(db_factory, sent):
    ticket_id = _ticket(db_factory, title="Fix the checkout button")

    result = reply_delivery.deliver_ticket_reply(ticket_id, "Fixed and checked.")

    assert result["delivered"] is True
    assert result["channel"] == "whatsapp"
    assert sent and sent[0]["target"] == "27634103646"


def test_the_same_reply_is_never_sent_twice(db_factory, sent):
    ticket_id = _ticket(db_factory)

    first = reply_delivery.deliver_ticket_reply(ticket_id, "Fixed and checked.")
    second = reply_delivery.deliver_ticket_reply(ticket_id, "Fixed and checked.")

    assert first["delivered"] is True
    assert second["delivered"] is False and second["duplicate"] is True
    assert len(sent) == 1


def test_a_different_reply_is_allowed_after_the_first(db_factory, sent):
    ticket_id = _ticket(db_factory)

    reply_delivery.deliver_ticket_reply(ticket_id, "First update.")
    result = reply_delivery.deliver_ticket_reply(ticket_id, "Second update.")

    assert result["delivered"] is True
    assert len(sent) == 2


def test_incomplete_provenance_refuses_to_send(db_factory, sent):
    ticket_id = _ticket(db_factory, source_provider="", source_thread_id="")

    result = reply_delivery.deliver_ticket_reply(ticket_id, "Fixed and checked.")

    assert result["delivered"] is False
    assert "source_provider" in result["missing"]
    assert sent == []


def test_empty_reply_is_refused(db_factory, sent):
    ticket_id = _ticket(db_factory)

    assert reply_delivery.deliver_ticket_reply(ticket_id, "   ")["delivered"] is False
    assert sent == []


def test_unknown_ticket_is_refused(db_factory, sent):
    assert reply_delivery.deliver_ticket_reply(9999, "hello")["delivered"] is False
    assert sent == []


# ---------------------------------------------------------------------------
# Guards
# ---------------------------------------------------------------------------


def test_a_promise_claim_needs_a_human(db_factory, sent, monkeypatch):
    ticket_id = _ticket(db_factory)
    monkeypatch.setattr(
        "distr.core.workflow.dev_approvals.gate",
        lambda *args, **kwargs: {"ok": False, "needs_approval": True},
    )

    result = reply_delivery.deliver_ticket_reply(ticket_id, "It's now live in production.")

    assert result["delivered"] is False and result["pending_approval"] is True
    assert sent == []


def test_a_long_reply_needs_a_human(db_factory, sent, monkeypatch):
    ticket_id = _ticket(db_factory)
    monkeypatch.setattr(
        "distr.core.workflow.dev_approvals.gate",
        lambda *args, **kwargs: {"ok": False, "needs_approval": True},
    )

    result = reply_delivery.deliver_ticket_reply(ticket_id, "word " * 200)

    assert result["pending_approval"] is True
    assert sent == []


def test_a_critical_ticket_needs_a_human(db_factory, sent, monkeypatch):
    ticket_id = _ticket(db_factory, title="Add a schema migration for invoices")
    monkeypatch.setattr(
        "distr.core.workflow.dev_approvals.gate",
        lambda *args, **kwargs: {"ok": False, "needs_approval": True},
    )

    result = reply_delivery.deliver_ticket_reply(ticket_id, "Done.")

    assert result["pending_approval"] is True
    assert sent == []


def test_approval_lets_the_reply_through(db_factory, sent, approved):
    ticket_id = _ticket(db_factory, title="Add a schema migration for invoices")

    result = reply_delivery.deliver_ticket_reply(ticket_id, "Migration applied and verified.")

    assert result["delivered"] is True
    assert len(sent) == 1


def test_force_sends_without_the_gate(db_factory, sent):
    ticket_id = _ticket(db_factory, title="Add a schema migration for invoices")

    result = reply_delivery.deliver_ticket_reply(ticket_id, "Migration applied.", force=True)

    assert result["delivered"] is True
    assert len(sent) == 1


def test_approval_failure_fails_closed(db_factory, sent, monkeypatch):
    ticket_id = _ticket(db_factory, title="Add a schema migration for invoices")

    def _boom(*_args, **_kwargs):
        raise RuntimeError("approvals service down")

    monkeypatch.setattr("distr.core.workflow.dev_approvals.gate", _boom)

    result = reply_delivery.deliver_ticket_reply(ticket_id, "Migration applied.")

    assert result["delivered"] is False and result["pending_approval"] is True
    assert sent == []


# ---------------------------------------------------------------------------
# Unsupported and failure paths
# ---------------------------------------------------------------------------


def test_an_unsupported_channel_is_recorded_not_sent(db_factory, sent):
    ticket_id = _ticket(db_factory, source_provider="jira", source_thread_id="JIRA-1")

    result = reply_delivery.deliver_ticket_reply(ticket_id, "Fixed.")

    assert result["delivered"] is False and result["unsupported"] is True
    assert sent == []


def test_adapter_failure_is_reported_and_recorded(db_factory, monkeypatch):
    ticket_id = _ticket(db_factory)

    def _boom(*, target, body, subject=""):
        raise RuntimeError("relay down")

    monkeypatch.setitem(reply_delivery.ADAPTERS, "whatsapp", _boom)

    result = reply_delivery.deliver_ticket_reply(ticket_id, "Fixed and checked.")

    assert result["delivered"] is False
    assert "relay down" in result["error"]


def test_email_reaches_the_address_not_the_thread_id(db_factory, sent):
    ticket_id = _ticket(
        db_factory,
        source_provider="gmail",
        source_thread_id="thread-abc",
        source_contact="person@example.com",
    )

    result = reply_delivery.deliver_ticket_reply(ticket_id, "The fix is in.")

    assert result["delivered"] is True
    assert sent and sent[0]["target"] == "person@example.com"


def test_whatsapp_still_uses_the_thread_jid(db_factory, sent):
    ticket_id = _ticket(db_factory, source_provider="whatsapp", source_thread_id="27634103646")

    result = reply_delivery.deliver_ticket_reply(ticket_id, "The fix is in.")

    assert result["delivered"] is True
    assert sent and sent[0]["target"] == "27634103646"
