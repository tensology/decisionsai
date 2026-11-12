"""Initiative whole-gate audit fixes (12 Nov 2026).

Every outbound call is mocked: no Telegram, WhatsApp, email, Jira or Tensology traffic.
The database is in memory and the attention store lives in tmp_path.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import distr.core.initiative.outcome_flow as of
from distr.core.db.projects import Project
from distr.core.initiative import attention
from distr.core.workflow import reply_delivery
from tests.initiative.test_initiative_phase4 import (  # noqa: F401  (pytest fixture)
    OWNER_CHAT,
    REPLY,
    _audit,
    _ticket,
    env,
)

ASK_ON = {"initiative_ask_external_comms": True}
QUIET = {"initiative_level": "observe", "initiative_allow_telegram": False, **ASK_ON}
STD_TG_OFF = {"initiative_level": "operate", "initiative_allow_telegram": False, **ASK_ON}
STD_TG_UNSET = {"initiative_level": "operate", **ASK_ON}
OWN_TG_OFF = {"initiative_level": "own", "initiative_allow_telegram": False, "initiative_scan_whatsapp": True,
              "initiative_scan_email": True, **ASK_ON}
OWN_ASK_OFF = {**OWN_TG_OFF, "initiative_allow_telegram": True, "initiative_ask_external_comms": False}


def _project(env, name="Spoortrack") -> int:
    with env.factory() as db:
        row = Project(name=name)
        db.add(row)
        db.commit()
        return int(row.id)


def _wa_ticket(env, **extra) -> int:
    values = dict(source_provider="whatsapp", source_thread_id="27820000000@s.whatsapp.net",
                  source_contact="Andy <27820000000@s.whatsapp.net>", source_external_id="wamid-1")
    values.update(extra)
    return _ticket(env.factory, **values)


def _mail_ticket(env, **extra) -> int:
    values = dict(source_provider="gmail", source_thread_id="thread-1", source_contact="andy@client.co",
                  source_external_id="msg-1")
    values.update(extra)
    return _ticket(env.factory, **values)


@pytest.fixture
def transport(env, monkeypatch):
    """Record would-be sends and approval asks; never touch a real channel."""
    sent, gates = [], []

    def deliver(tid, body, envelope, adapter, workflow=None):
        sent.append({"ticket": tid, "body": body, "adapter": adapter})
        return {"delivered": True, "body": body}

    def gate(tool, channel, args, approval_id, summary, **kw):
        gates.append({"channel": channel, "approval_id": approval_id, "summary": summary})
        return None if approval_id == "approved-1" else {"approval_status": "pending", "approval_id": "a-1"}

    monkeypatch.setattr(reply_delivery, "_deliver_prepared", deliver)
    monkeypatch.setattr("distr.core.workflow.dev_approvals.gate", gate)
    monkeypatch.setattr(reply_delivery, "_prepare_mail_target", lambda *a, **k: None)
    monkeypatch.setitem(reply_delivery.ADAPTERS, "whatsapp", SimpleNamespace(name="whatsapp"))
    monkeypatch.setitem(reply_delivery.ADAPTERS, "email", SimpleNamespace(name="email"))
    return SimpleNamespace(sent=sent, gates=gates)


# ---------------------------------------------------------------- item 1

@pytest.mark.parametrize("settings", [QUIET, STD_TG_OFF, STD_TG_UNSET, OWN_TG_OFF],
                         ids=["quiet", "standard-telegram-off", "standard-telegram-unset", "own-telegram-off"])
@pytest.mark.parametrize("make", [_wa_ticket, _mail_ticket], ids=["whatsapp", "email"])
def test_ask_before_messaging_clients_holds_every_reply(env, transport, monkeypatch, settings, make):
    env.state.settings = dict(settings)
    if settings is STD_TG_UNSET:
        monkeypatch.setattr(of, "_telegram_manager", lambda: None)
    monkeypatch.setattr(reply_delivery, "_approval_reason", lambda **kw: "")  # a reply the old gate would let through
    ticket_id = make(env)
    out = reply_delivery.deliver_ticket_reply(ticket_id, REPLY)
    assert out.get("delivered") is not True, out
    assert transport.sent == []
    assert transport.gates and reply_delivery.ASK_FIRST_REASON in transport.gates[0]["summary"]


@pytest.mark.parametrize("make", [_wa_ticket, _mail_ticket], ids=["whatsapp", "email"])
def test_approved_reply_is_sent_exactly_once(env, transport, make):
    env.state.settings = dict(OWN_TG_OFF)
    ticket_id = make(env)
    out = reply_delivery.deliver_ticket_reply(ticket_id, REPLY, approval_id="approved-1")
    assert out["delivered"] is True and len(transport.sent) == 1


def test_ask_setting_missing_defaults_to_ask():
    assert of.ask_before_client_messages({}) is True
    assert of.ask_before_client_messages({"initiative_ask_external_comms": False}) is False


def test_own_with_telegram_off_holds_and_tells_the_owner_off_telegram(env, transport, monkeypatch):
    told = []
    monkeypatch.setattr(of, "notify_owner", lambda text, settings=None, **kw: told.append((text, kw.get("kind"))))
    env.state.settings = dict(OWN_TG_OFF)
    out = reply_delivery.deliver_ticket_reply(_wa_ticket(env), REPLY)
    assert out.get("held_for_approval") is True and transport.sent == []
    assert told and told[0][1] == "own_held" and "waiting for your OK" in told[0][0]
    rec = [a for a in _audit(env.tmp) if a["event"] == "own_client_reply_held_for_approval"]
    assert rec and rec[0]["delivered"] is False and rec[0]["surface"] == "approval_queue"
    assert not [a for a in _audit(env.tmp) if a["event"] == "own_client_reply_sent"]


# ---------------------------------------------------------------- items 4, 12

def test_own_whatsapp_toggle_off_stops_the_send_and_records_it(env, transport):
    env.state.settings = {**OWN_ASK_OFF, "initiative_scan_whatsapp": False}
    out = reply_delivery.deliver_ticket_reply(_wa_ticket(env), REPLY)
    assert out["delivered"] is False and out["blocked"] is True and transport.sent == []
    events = [a["event"] for a in _audit(env.tmp)]
    assert "own_client_reply_blocked_source_off" in events and "own_client_reply_sent" not in events


def test_failed_own_send_is_not_logged_as_sent(env, monkeypatch):
    monkeypatch.setattr(reply_delivery, "_deliver_prepared",
                        lambda *a, **k: {"delivered": False, "reason": "relay refused"})
    out = of.before_client_reply(ticket_id=1, shaped="Hi Andy", envelope={"recipient": "+27820000000"},
                                 target=SimpleNamespace(channel="whatsapp", contact="Andy"), adapter=object(),
                                 force=False, approval_context=None, settings=OWN_ASK_OFF)
    assert out["delivered"] is False
    events = {a["event"]: a for a in _audit(env.tmp)}
    assert "own_client_reply_sent" not in events
    assert events["own_client_reply_not_sent"]["status"] == "not_sent"
    assert events["own_client_reply_not_sent"]["reason"] == "relay refused"


# ---------------------------------------------------------------- items 2, 3, 5

def test_ignored_client_reply_is_held_even_with_ask_off(env, transport):
    env.state.settings = dict(OWN_ASK_OFF)
    attention.ignore_client("Andy <27820000000@s.whatsapp.net>", label="Andy")
    out = reply_delivery.deliver_ticket_reply(_wa_ticket(env), REPLY)
    assert out.get("delivered") is not True and transport.sent == []
    rec = [a for a in _audit(env.tmp) if a["event"] == "own_client_reply_held_for_approval"]
    assert rec and "not to worry about Andy" in rec[0]["reason"]


def test_ignore_client_persists_and_unignore_restores(env):
    attention.ignore_client("andy@client.co", label="Andy")
    assert attention.client_ignored("Andy Smith <andy@client.co>", source="gmail")["label"] == "Andy"
    stored = json.loads(attention.store_path().read_text())
    assert stored["clients"]
    assert attention.unignore_client("andy@client.co") is True
    assert attention.client_ignored("andy@client.co") is None


def test_waiting_client_on_ignored_project_is_surfaced(env):
    pid = _project(env)
    attention.set_project_ignored(pid, note="parked")
    _wa_ticket(env, linked_project_id=pid)
    tensions = attention.waiting_tensions()
    assert tensions, "a waiting client must never be hidden by an ignore"
    text = attention.tension_text(tensions[0])
    assert "Spoortrack" in text or "set aside" in text


def test_own_intake_on_ignored_project_is_not_started(env, monkeypatch):
    pid = _project(env)
    attention.set_project_ignored(pid)
    started = MagicMock()
    monkeypatch.setattr(of, "_own_accept", started)
    told = []
    monkeypatch.setattr(of, "notify_owner", lambda text, settings=None, **kw: told.append(text))
    monkeypatch.setattr(of, "_intake_context", lambda token: {
        "source": "whatsapp", "source_label": "WhatsApp", "client": "Andy", "project": "Spoortrack",
        "project_id": pid, "client_ids": [], "summary": "Can you fix the login?"})
    pending = {"token": "tok-1"}
    assert of.on_intake_staged(pending, settings={**OWN_ASK_OFF, "initiative_allow_telegram": False}) is None
    started.assert_not_called()
    assert "Heads up" in pending["text"] and told and "Andy" in told[0]
    assert [a for a in _audit(env.tmp) if a["event"] == "own_intake_held_set_aside"]


# ---------------------------------------------------------------- item 8

def test_ignore_note_survives_more_than_five_memory_sections(tmp_path, env):
    from distr.core.memory.files import CTX_MEMORY_MAX_SECTIONS, load_context_snippets_for_llm, memory_paths

    root = tmp_path / "mem"
    root.mkdir()
    sections = ["Paul: don't worry about the client Andy for now."]
    sections += [f"Note {i}: shipped the weekly report." for i in range(CTX_MEMORY_MAX_SECTIONS + 3)]
    memory_paths(root)["memory"].write_text("\n---\n".join(sections), encoding="utf-8")
    attention.ignore_client("andy@client.co", label="Andy")
    memory = load_context_snippets_for_llm(root=root)["memory"]
    assert memory.startswith("## Standing instructions")
    assert "don't worry about the client Andy" in memory and "Andy" in memory.split("\n")[1]


def test_memory_palace_hints_change_attention(env):
    profile = attention.attention_profile(
        ["Paul said Spoortrack is on hold until January.", "The Merrypak launch deadline is Friday."],
        ["Spoortrack", "Merrypak", "Shop"])
    assert "Spoortrack" in profile["quiet"] and profile["boost"].get("Merrypak")
    assert "Shop" not in profile["quiet"] and "Shop" not in profile["boost"]


# ---------------------------------------------------------------- items 6, 15

def test_email_address_link_routes_mail_and_flags_edge_cases(env, monkeypatch):
    from distr.core.incoming import project_links

    a, b = _project(env, "Merrypak"), _project(env, "Shop")
    monkeypatch.setattr(project_links, "source_project_index", lambda refresh=False: {})
    attention.link_address("orders@merrypak.co.za", a)
    items = [
        {"source": "gmail", "source_thread_id": "t1", "sender": "Orders <orders@merrypak.co.za>",
         "subject": "Label reprint", "created_at": "2026-11-12T06:00:00"},
        {"source": "gmail", "source_thread_id": "t1", "sender": "orders@merrypak.co.za",
         "subject": "Re: New quote for boxes", "created_at": "2026-11-12T07:00:00"},
        {"source": "gmail", "source_thread_id": "t2", "sender": "Kim <kim@other.co>", "subject": "Fwd: Label reprint"},
    ]
    project_links.apply_project_links(items)
    assert items[0]["project_id"] == a and items[0]["project_link_via"] == "email:orders@merrypak.co.za"
    assert items[1].get("topic_drift") is True
    assert items[2].get("needs_link") is True and items[2]["suggested_project_ids"] == [a]
    attention.link_address("orders@merrypak.co.za", b)
    conflict = project_links.apply_project_links([{"source": "gmail", "source_thread_id": "t9",
                                                   "sender": "orders@merrypak.co.za", "subject": "Hi"}])[0]
    assert conflict["project_link_conflict"]["project_ids"] == sorted([a, b]) and "project_id" not in conflict
    assert {f["kind"] for f in attention.link_flags()} >= {"conflict", "topic_drift", "needs_link"}


# ---------------------------------------------------------------- item 9

def test_proposal_born_whatsapp_ticket_resolves_to_its_origin(env):
    from distr.core.db.kanban import KanbanTicket
    from distr.core.workflow.reply_channel import resolve_reply_target

    ticket_id = _ticket(env.factory, source_provider="incoming_proposal",
                        source_external_id="local:whatsapp:120363412181484366@g.us:58578abf0123",
                        source_label="proposal-backfill")
    with env.factory() as db:
        target = resolve_reply_target(db.get(KanbanTicket, ticket_id))
    assert target.channel == "whatsapp" and target.can_send, target


# ---------------------------------------------------------------- item 10

def test_telegram_group_reply_needs_an_explicit_link_and_a_group_sender(env, monkeypatch):
    from distr.core.incoming import project_links

    class Mgr:
        telegram_user_id = OWNER_CHAT
        chat_id = None

    monkeypatch.setattr(reply_delivery, "_telegram_manager", lambda: Mgr(), raising=False)
    monkeypatch.setattr("distr.core.kanban.ticket_workflow_engagement._telegram_manager_from_app", lambda: Mgr())
    monkeypatch.setattr(project_links, "source_project_index", lambda refresh=False: {("telegram", "-100123"): {1}})
    from distr.core.incoming.source_reply import ReplyPreflightError

    with pytest.raises(ReplyPreflightError, match="can't post into groups"):
        reply_delivery._send_telegram(target="-100123", body="Hi")
    with pytest.raises(ReplyPreflightError, match="can't reply to"):
        reply_delivery._send_telegram(target="-100999", body="Hi")


# ---------------------------------------------------------------- item 11

def test_internal_development_prompt_never_reaches_telegram():
    from distr.core.initiative.service import _user_facing_instruction
    from distr.core.integrations.telegram.sender import _audit_outbound_telegram_text

    leaked = ("the IDE session finished AW Hypnotherapy. It was working on: DECISIONS DEVELOPMENT THREAD. "
              "Work as the active IDE coding agent and use the project tools directly.")
    clean = _audit_outbound_telegram_text(leaked)
    assert "DECISIONS DEVELOPMENT THREAD" not in clean and "IDE coding agent" not in clean
    assert "finished AW Hypnotherapy" in clean
    prompt = ("[DECISIONS DEVELOPMENT THREAD]\nWork as the active IDE coding agent.\n\n"
              "Current user instruction:\nRenew the HTTPS certificate\n\nAttached files for this turn:\n- a.png")
    assert _user_facing_instruction(prompt) == "Renew the HTTPS certificate"
    assert _user_facing_instruction("[DECISIONS DEVELOPMENT THREAD]\nmode text") == ""


# ---------------------------------------------------------------- item 13

def test_repeat_approval_asks_are_remembered(env):
    from datetime import datetime, timedelta

    now = datetime(2026, 11, 12, 0, 48)
    assert attention.recent_ask("hash-290", now=now) is None
    attention.record_ask("hash-290", "Merrypak ticket", now=now)
    assert attention.recent_ask("hash-290", now=now + timedelta(hours=5))
    assert attention.recent_ask("hash-290", now=now + timedelta(days=4)) is None


# ---------------------------------------------------------------- item 14

def test_automations_and_queue_hold_on_ignored_project(env, monkeypatch):
    from distr.core.automation_orchestrator import initiative_hold_reason

    pid = _project(env)
    attention.set_project_ignored(pid)
    assert "set aside" in initiative_hold_reason({"linked_project_id": pid})
    assert initiative_hold_reason({"linked_project_id": pid + 99}) == ""
    attention.ignore_client("27820000000@s.whatsapp.net", label="Andy")
    held = initiative_hold_reason({"_invocation_context": {"origin_surface": "whatsapp",
                                                          "origin_thread_id": "27820000000@s.whatsapp.net"}})
    assert "Andy" in held
    ticket_id = _wa_ticket(env, linked_project_id=pid)
    assert "set aside" in attention.ticket_hold_reason(ticket_id)


def test_quiet_holds_incoming_message_automations_only(env, monkeypatch):
    from distr.core.automation_orchestrator import initiative_hold_reason

    monkeypatch.setattr(of, "current_level", lambda settings=None: "observe")
    ctx = {"origin_surface": "whatsapp", "origin_thread_id": "27829999999@s.whatsapp.net"}
    assert "Quiet" in initiative_hold_reason({"automation_type": "channel_intake", "_invocation_context": ctx})
    assert initiative_hold_reason({"automation_type": "scheduled"}) == ""


# ---------------------------------------------------------------- items 7, 16

def test_recommendations_name_a_waiting_client_on_a_set_aside_project(env, monkeypatch):
    monkeypatch.setattr(of, "attention_hints", lambda *a, **k: [], raising=False)
    pid = _project(env)
    attention.set_project_ignored(pid)
    _wa_ticket(env, linked_project_id=pid)
    data = of.build_recommendations({"initiative_level": "operate"})
    assert {"tensions", "pending_replies", "links", "automations"} <= set(data["sources"])
    assert any(r["area"] == "tension" for r in data["recommendations"])
    text = of.recommendations_digest({"initiative_level": "operate"})
    assert "Still waiting, even though you set them aside" in text


# ---------------------------------------------------------------- items 5, 6 surfaces

def test_attention_tool_ignores_a_client_and_links_an_email(env):
    pytest.importorskip("langchain")  # the tool base class; present in the app venv
    from distr.core.agent.tools.system.initiative_attention_tool import InitiativeAttentionTool

    pid = _project(env, "Merrypak")
    tool = InitiativeAttentionTool()
    out = tool._run(action="ignore_client", client="Andy <andy@client.co>")
    assert "Andy" in out and attention.client_ignored("andy@client.co")
    out = tool._run(action="link_email", email="orders@merrypak.co.za", project="Merrypak")
    assert "Merrypak" in out and attention.address_project_ids("orders@merrypak.co.za")[0] == {pid}


def test_attention_routes_are_registered_and_write_locally(env):
    import asyncio

    from fastapi import APIRouter

    from distr.gui.web.routes.settings import initiative_attention as routes

    _project(env, "Merrypak")
    router = APIRouter()
    routes.register_routes(router, None)
    paths = {(r.path, tuple(sorted(r.methods))) for r in router.routes}
    assert ("/initiative/attention", ("GET",)) in paths
    post = next(r.endpoint for r in router.routes if r.path == "/initiative/attention/email-links" and "POST" in r.methods)
    resp = asyncio.run(post(routes._EmailLinkPayload(address="@merrypak.co.za", project="Merrypak")))
    assert resp.status_code == 200 and "domain:merrypak.co.za" in attention.address_links()


def test_attention_tool_is_registered_and_intent_routed():
    from distr.core.agent.tool_intents import _RULES

    loader = (__import__("pathlib").Path(__file__).resolve().parents[2] / "distr/core/agent/tools/loader.py").read_text()
    assert loader.count("InitiativeAttentionTool") >= 2 and "initiative_attention" in loader
    assert any("initiative_attention" in json.dumps(rule, default=str) for rule in _RULES)
