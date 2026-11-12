"""Initiative leftovers (12 Nov 2026): proposal-email contact and the initiative_level SQL default.

Every outbound call is mocked: no email, WhatsApp, Telegram or Tensology traffic.
The database is in memory (or a temp file for the schema test).
"""

from __future__ import annotations

import json
import sqlite3
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine

from distr.core.db import Chat
from distr.core.db.kanban import KanbanTicket
from distr.core.workflow import reply_delivery
from distr.core.workflow.reply_channel import proposal_origin, resolve_reply_target
from tests.initiative.test_initiative_phase4 import REPLY, _ticket, env  # noqa: F401  (pytest fixture)

THREAD = "18c2f0a1b2c3d4e5"
REF = f"local:gmail:{THREAD}:58578abf0123456789abcdef"


def _proposal(sender="Andy Smith <andy@client.co>", *, direction="inbound", ids=("m1", "m2")):
    return {
        "id": f"local:gmail:{THREAD}", "source": "gmail", "source_thread_id": THREAD,
        "project_id": 1, "message_ids": list(ids),
        "evidence": [
            {"id": "m1", "body": "Can you update the site?", "sender_name": "Bob <bob@other.co>", "direction": "inbound"},
            {"id": "m2", "body": "Also the footer please", "sender_name": sender, "direction": direction},
        ],
    }


def _proposal_ticket(env, proposal, **extra) -> int:
    with env.factory() as db:
        chat = Chat(title="Proposal", project_id=1,
                    params=json.dumps({"incoming_proposal_handoff": {"key": REF, "proposal": proposal}}))
        db.add(chat)
        db.commit()
        chat_id = int(chat.id)
    values = dict(source_provider="incoming_proposal", source_external_id=REF, source_chat_id=chat_id)
    values.update(extra)
    return _ticket(env.factory, **values)


def _target(env, ticket_id):
    with env.factory() as db:
        return resolve_reply_target(db.get(KanbanTicket, ticket_id))


# ------------------------------------------------------------- proposal email contact

def test_proposal_email_ticket_uses_the_original_senders_exact_address(env):
    target = _target(env, _proposal_ticket(env, _proposal()))
    assert target.channel == "email" and target.can_send, target
    assert target.contact == "andy@client.co"      # the latest inbound message's own sender, not Bob
    assert target.message_id == "m2"
    assert target.thread_id == THREAD


def test_proposal_email_without_any_address_still_fails_closed(env, monkeypatch):
    ticket_id = _proposal_ticket(env, _proposal(sender="Andy Smith"))  # display name only: never guessed
    target = _target(env, ticket_id)
    assert not target.can_send and "source_contact" in target.missing and target.contact == ""
    monkeypatch.setattr(reply_delivery, "_deliver_prepared", lambda *a, **k: pytest.fail("must not send"))
    out = reply_delivery.deliver_ticket_reply(ticket_id, REPLY)
    assert out["delivered"] is False and out["reason"] == "incomplete provenance"
    assert "won't guess" in out["detail"]


def test_outbound_or_other_thread_messages_are_never_used_as_the_contact(env):
    # The latest message is ours (outbound): it isn't a primary message, so m1 (Bob) is the one we reply to.
    proposal = _proposal(sender="Paul <paul@tensology.com>", direction="outbound", ids=("m1",))
    target = _target(env, _proposal_ticket(env, proposal))
    assert target.contact == "bob@other.co" and target.message_id == "m1"
    # A proposal for a different thread gives nothing.
    other = dict(_proposal(), source_thread_id="another-thread", id="local:gmail:another-thread")
    target = _target(env, _proposal_ticket(env, other))
    assert not target.can_send and target.contact == ""


def test_a_valid_contact_already_on_the_ticket_wins(env):
    ticket_id = _proposal_ticket(env, _proposal(), source_contact="ops@client.co")
    assert _target(env, ticket_id).contact == "ops@client.co"


def test_whatsapp_proposal_origin_is_unchanged():
    origin = proposal_origin({"source_external_id": "local:whatsapp:120363412181484366@g.us:58578abf0123"})
    assert origin["source_provider"] == "whatsapp" and origin["source_message_id"] == ""


@pytest.fixture
def mail(env, monkeypatch):
    """Mock the provider read and the send; record would-be payloads."""
    calls = SimpleNamespace(resolved=[], sent=[], gates=[])

    def resolve(source):
        calls.resolved.append(source)
        return {"channel": "gmail", "to": ["andy@client.co"], "cc": [], "subject": "Re: Site update",
                "recipient_names": ["Andy Smith"], "message_id": source["message_ids"][0], "thread_id": THREAD,
                "in_reply_to": "<m2@mail>", "references": "", "from": "paul@tensology.com"}

    def gate(tool, channel, args, approval_id, summary, **kw):
        calls.gates.append({"channel": channel, "approval_id": approval_id, "summary": summary})
        return None if approval_id == "approved-1" else {"approval_status": "pending", "approval_id": "a-1"}

    def deliver(tid, body, envelope, adapter, workflow=None):
        calls.sent.append({"ticket": tid, "body": body, "to": envelope["recipient"],
                           "resolved": envelope.get("resolved_target")})
        return {"delivered": True, "body": body}

    monkeypatch.setattr("distr.core.incoming.source_reply.resolve_target", resolve)
    monkeypatch.setattr("distr.core.workflow.dev_approvals.gate", gate)
    monkeypatch.setattr(reply_delivery, "_deliver_prepared", deliver)
    return calls


def test_proposal_email_reply_is_held_for_approval_when_ask_first_is_on(env, mail):
    env.state.settings = {"initiative_level": "operate", "initiative_allow_telegram": False,
                          "initiative_ask_external_comms": True}
    out = reply_delivery.deliver_ticket_reply(_proposal_ticket(env, _proposal()), REPLY)
    assert out.get("delivered") is not True, out
    assert mail.sent == [] and mail.gates and reply_delivery.ASK_FIRST_REASON in mail.gates[0]["summary"]
    # The original message was looked up by its real provider id, not the proposal handoff key.
    assert mail.resolved and mail.resolved[0]["message_ids"] == ["m2"] and mail.resolved[0]["thread_id"] == THREAD


def test_approved_proposal_email_reply_goes_to_the_verified_original_sender(env, mail):
    env.state.settings = {"initiative_level": "operate", "initiative_allow_telegram": False,
                          "initiative_ask_external_comms": True}
    out = reply_delivery.deliver_ticket_reply(_proposal_ticket(env, _proposal()), REPLY, approval_id="approved-1")
    assert out.get("delivered") is True, out
    assert len(mail.sent) == 1 and mail.sent[0]["to"] == "andy@client.co"
    assert mail.sent[0]["resolved"]["in_reply_to"] == "<m2@mail>"


# ------------------------------------------------------------- initiative_level SQL default

LEGACY_SQL = """CREATE TABLE settings (
\tid INTEGER NOT NULL,
\tlanguage VARCHAR,
\tinitiative_allow_telegram BOOLEAN DEFAULT 0,
\tinitiative_level VARCHAR DEFAULT 'assist',
\tinitiative_own_channel VARCHAR DEFAULT '',
\tPRIMARY KEY (id)
)"""


def _defaults(path):
    with sqlite3.connect(path) as conn:
        return {r[1]: r[4] for r in conn.execute("PRAGMA table_info(settings)")}, \
            conn.execute("SELECT * FROM settings ORDER BY id").fetchall()


def test_initiative_level_default_becomes_operate_without_touching_rows(tmp_path):
    from distr.core.db.migrations import align_initiative_level_default

    path = tmp_path / "settings.db"
    with sqlite3.connect(path) as conn:
        conn.execute(LEGACY_SQL)
        conn.execute("INSERT INTO settings VALUES (1, 'en', 1, 'operate', 'telegram')")
    before_defaults, before_rows = _defaults(path)
    assert before_defaults["initiative_level"] == "'assist'"
    engine = create_engine(f"sqlite:///{path}")
    assert align_initiative_level_default(engine)["changed"] is True
    after_defaults, after_rows = _defaults(path)
    assert after_defaults["initiative_level"] == "'operate'"
    assert after_rows == before_rows == [(1, "en", 1, "operate", "telegram")]
    assert {k: v for k, v in after_defaults.items() if k != "initiative_level"} == \
        {k: v for k, v in before_defaults.items() if k != "initiative_level"}
    with sqlite3.connect(path) as conn:
        conn.execute("INSERT INTO settings (id) VALUES (2)")
        assert conn.execute("SELECT initiative_level FROM settings WHERE id=2").fetchone()[0] == "operate"
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert align_initiative_level_default(engine)["changed"] is False  # idempotent


def test_unexpected_settings_schema_is_left_alone(tmp_path):
    from distr.core.db.migrations import align_initiative_level_default

    path = tmp_path / "settings.db"
    with sqlite3.connect(path) as conn:
        conn.execute(LEGACY_SQL)
        conn.execute("INSERT INTO settings VALUES (1, 'en', 1, 'operate', '')")
        conn.execute("CREATE INDEX ix_settings_language ON settings(language)")
    before = _defaults(path)
    out = align_initiative_level_default(create_engine(f"sqlite:///{path}"))
    assert out["changed"] is False
    assert _defaults(path) == before
    with sqlite3.connect(path) as conn:
        names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master")}
    assert "settings__initiative_default" not in names and "ix_settings_language" in names


def test_code_default_is_operate_and_migration_is_wired():
    import inspect

    from distr.core.db import Settings
    from distr.core.db import migrations

    assert Settings.__table__.c.initiative_level.default.arg == "operate"
    assert "align_initiative_level_default(engine)" in inspect.getsource(migrations.run_migrations)
