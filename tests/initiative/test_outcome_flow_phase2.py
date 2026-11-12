"""Phase 2 posture flows (Own autopilot, Standard Telegram loop, proposals on
demand, Assist removal, recommendations, ignored/pinned projects).

Every external send is mocked: no Telegram, WhatsApp, Gmail or Mailshot traffic,
no Development threads, no real database writes.
"""

from datetime import datetime
from pathlib import Path
from threading import Thread
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import distr.core.initiative.outcome_flow as of

REPO = Path(__file__).resolve().parents[2]
# Own with "Ask before messaging clients" switched off: the automatic path. Ask-first is covered in test_initiative_fixes_20261112.py.
OWN = {"initiative_level": "own", "initiative_allow_telegram": True, "initiative_scan_whatsapp": True,
       "initiative_scan_email": True, "initiative_ask_external_comms": False}
STD = {"initiative_level": "operate", "initiative_allow_telegram": True}
QUIET = {"initiative_level": "observe", "initiative_allow_telegram": False}


class FakeTelegram:
    def __init__(self):
        self.sent = []

    def send_to_telegram(self, text, reply_markup=None):
        self.sent.append({"text": text, "reply_markup": reply_markup})
        return True


@pytest.fixture
def env(monkeypatch, tmp_path):
    monkeypatch.setenv("DECISIONS_DB_DIR", str(tmp_path))
    tg = FakeTelegram()
    events = []
    monkeypatch.setattr(of, "_telegram_manager", lambda: tg)
    monkeypatch.setattr("distr.core.orchestrator.emit_event", lambda **kw: events.append(kw))
    monkeypatch.setattr(of, "_intake_context", lambda token: {
        "source": "whatsapp", "source_label": "WhatsApp", "client": "Anna", "project": "Shop",
        "summary": "The checkout button is broken on mobile"})

    class SyncThread:
        def __init__(self, target, args=(), **_):
            self.target, self.args = target, args

        def start(self):
            self.target(*self.args)

    monkeypatch.setattr(of.threading, "Thread", SyncThread)
    return SimpleNamespace(tg=tg, events=events, tmp=tmp_path, mp=monkeypatch)


def _audit_lines(tmp):
    import json

    path = tmp / "logs" / "initiative_outcome_audit.jsonl"
    return [json.loads(l) for l in path.read_text().splitlines()] if path.exists() else []


def _all_human(texts):
    for text in texts:
        assert not of.looks_technical(text), text
        assert "{" not in text and "Traceback" not in text, text


# ============================ OWN OUTCOMES =================================

def test_own_intake_auto_accepts_and_starts_thread(env):
    calls = []
    env.mp.setattr("distr.core.kanban.whatsapp_intake_approval._execute_ready",
                   lambda token, chat_id=None: calls.append(token) or {
                       "action": "ready", "ticket_id": 7, "development_chat_id": 99, "workflow_run_id": 5})
    assert of.on_intake_staged({"token": "tok1"}, settings=OWN) is True
    assert calls == ["tok1"]
    flow = of.get_flow(of._flow_key("intake", "tok1"))
    assert flow["status"] == "running" and flow["chat_id"] == 99
    assert any(e["event"] == "own_intake_accepted" for e in _audit_lines(env.tmp))
    assert len(env.tg.sent) == 1 and "Anna" in env.tg.sent[0]["text"]
    _all_human(m["text"] for m in env.tg.sent)


def test_own_intake_respects_source_toggle(env):
    run = MagicMock()
    env.mp.setattr("distr.core.kanban.whatsapp_intake_approval._execute_ready", run)
    assert of.on_intake_staged({"token": "tok2"}, settings={**OWN, "initiative_scan_whatsapp": False}) is False
    run.assert_not_called()
    assert env.tg.sent == []


def test_own_intake_heads_up_respects_telegram_toggle(env):
    env.mp.setattr("distr.core.kanban.whatsapp_intake_approval._execute_ready",
                   lambda token, chat_id=None: {"action": "ready", "ticket_id": 1})
    assert of.on_intake_staged({"token": "tok3"}, settings={**OWN, "initiative_allow_telegram": False}) is True
    assert env.tg.sent == []


@pytest.mark.parametrize("channel,adapter_name,destination", [
    ("whatsapp", "whatsapp", "+27 82 000 0000"),
    ("email", "email", "anna@example.com"),
    ("mailshot", "mailshot", "anna@client.co"),
])
def test_own_client_update_sent_via_originating_source_and_audited(env, channel, adapter_name, destination):
    delivered = []
    env.mp.setattr("distr.core.workflow.reply_delivery._deliver_prepared",
                   lambda tid, body, envelope, adapter, workflow=None: delivered.append((tid, body, adapter))
                   or {"delivered": True, "body": body})
    adapter = SimpleNamespace(name=adapter_name)
    target = SimpleNamespace(channel=channel, contact="Anna")
    shaped = "Hi Anna, **all fixed**: the checkout button works on mobile again."
    out = of.before_client_reply(ticket_id=12, shaped=shaped, envelope={"recipient": destination},
                                 target=target, adapter=adapter, force=False, approval_context=None, settings=OWN)
    assert out["delivered"] is True
    assert delivered[0][2] is adapter  # same channel the work came in on
    assert "**" not in delivered[0][1] and "checkout button works" in delivered[0][1]
    rec = [e for e in _audit_lines(env.tmp) if e["event"] == "own_client_reply_sent"][0]
    assert rec["body"] == delivered[0][1] and rec["destination"] == destination
    assert len(env.tg.sent) == 1
    _all_human(m["text"] for m in env.tg.sent)


def test_own_client_update_skipped_when_source_toggle_off(env):
    sent = MagicMock()
    env.mp.setattr("distr.core.workflow.reply_delivery._deliver_prepared", sent)
    out = of.before_client_reply(ticket_id=1, shaped="Hi", envelope={}, target=SimpleNamespace(channel="whatsapp", contact="A"),
                                 adapter=object(), force=False, approval_context=None,
                                 settings={**OWN, "initiative_scan_whatsapp": False})
    # Audit 12 Nov: returning None fell through to the legacy send. Now nothing goes out and the record says so.
    assert out["delivered"] is False and out["blocked"] is True and "switched off" in out["reason"]
    sent.assert_not_called()
    rec = [e for e in _audit_lines(env.tmp) if e["event"] == "own_client_reply_blocked_source_off"]
    assert rec and rec[0]["delivered"] is False and rec[0]["status"] == "not_sent"


# ============================ STANDARD =====================================

def test_standard_intake_card_is_accept_decline_and_humanized(env):
    pending = {"token": "tokS", "text": "WA batch wi:tokS token 4f9a3c2b1d0e9f8a"}
    assert of.on_intake_staged(pending, settings=STD) is None  # legacy sender delivers it
    labels = [b["text"] for row in pending["reply_markup"]["inline_keyboard"] for b in row]
    assert labels == ["Accept", "Decline", "Add a note first"]
    assert "Anna" in pending["text"] and "WhatsApp" in pending["text"]
    _all_human([pending["text"]])


def test_notify_hook_quiet_sends_nothing_and_standard_sends_card(env, monkeypatch):
    import distr.core.kanban.whatsapp_intake_approval as wi
    import distr.core.utils as utils

    tg = FakeTelegram()
    monkeypatch.setattr("distr.core.kanban.ticket_workflow_engagement._telegram_manager_from_app", lambda: tg)
    monkeypatch.setattr(utils, "load_settings_from_db", lambda: dict(QUIET))
    assert wi.notify_telegram_approval({"token": "q1", "text": "x"}) is False
    assert tg.sent == []
    monkeypatch.setattr(utils, "load_settings_from_db", lambda: dict(STD))
    assert wi.notify_telegram_approval({"token": "s1", "text": "x"}) is True
    assert [b["text"] for b in tg.sent[0]["reply_markup"]["inline_keyboard"][0]] == ["Accept", "Decline"]


def test_standard_accept_and_decline_are_humanized(env, monkeypatch):
    import distr.core.kanban.whatsapp_intake_approval as wi

    monkeypatch.setattr(of, "load_settings", lambda: dict(STD))
    monkeypatch.setattr(wi, "handle_telegram_reply", lambda text, chat_id=None: {
        "action": "ready" if text.endswith(":yes") else "no", "token": "tokS",
        "text": "Created ticket #4242 and started workflow run #555.", "ticket_id": 4242,
        "development_chat_id": 909})
    yes = of.handle_telegram_text("wi:tokS:yes", chat_id=1)
    no = of.handle_telegram_text("wi:tokS:no", chat_id=1)
    assert "on it" in yes["text"].lower() and "set that one aside" in no["text"]
    _all_human([yes["text"], no["text"]])
    assert of.get_flow(of._flow_key("intake", "tokS"))["chat_id"] == 909


def test_plain_yes_is_left_to_existing_handlers(env, monkeypatch):
    monkeypatch.setattr(of, "load_settings", lambda: dict(STD))
    assert of.handle_telegram_text("yes", chat_id=1) is None


def _standard_reply(env):
    target = SimpleNamespace(channel="whatsapp", contact="Anna")
    return of.before_client_reply(ticket_id=12, shaped="Hi Anna, it's fixed now.", envelope={"recipient": "+27 82"},
                                  target=target, adapter=object(), force=False,
                                  approval_context={"chat_id": 909}, settings=STD)


def test_standard_work_done_card_holds_the_send(env):
    sent = MagicMock()
    env.mp.setattr("distr.core.workflow.reply_delivery._deliver_prepared", sent)
    out = _standard_reply(env)
    assert out["pending_approval"] is True and out["delivered"] is False
    sent.assert_not_called()
    labels = [b["text"] for row in env.tg.sent[0]["reply_markup"]["inline_keyboard"] for b in row]
    assert labels == ["Continue", "Check it", "Send message to client"]
    _all_human(m["text"] for m in env.tg.sent)


def test_standard_send_shows_overview_then_sends_only_after_confirm(env, monkeypatch):
    _standard_reply(env)
    key = env.tg.sent[0]["reply_markup"]["inline_keyboard"][0][0]["callback_data"].split(":")[1]
    deliver = MagicMock(return_value={"delivered": True})
    monkeypatch.setattr("distr.core.workflow.reply_delivery.deliver_ticket_reply", deliver)

    early = of.handle_telegram_text(f"oc:{key}:confirm")
    assert "look at the message first" in early["text"].lower()
    deliver.assert_not_called()

    overview = of.handle_telegram_text(f"oc:{key}:send")
    assert "To: Anna" in overview["text"] and "Via: WhatsApp" in overview["text"]
    assert "Hi Anna, it's fixed now." in overview["text"]
    deliver.assert_not_called()

    done = of.handle_telegram_text(f"oc:{key}:confirm")
    deliver.assert_called_once()
    args, kwargs = deliver.call_args
    assert args == (12, "Hi Anna, it's fixed now.") and kwargs["force"] is True
    assert kwargs["approval_context"] == {"chat_id": 909}
    assert done["text"].startswith("Sent.")
    again = of.handle_telegram_text(f"oc:{key}:confirm")
    assert "already went out" in again["text"]
    deliver.assert_called_once()
    rec = [e for e in _audit_lines(env.tmp) if e["event"] == "operate_client_reply_confirmed"][0]
    assert rec["body"] == "Hi Anna, it's fixed now." and rec["destination"] == "+27 82"


@pytest.mark.parametrize('provider_outcome', [
    {'delivered': False, 'unconfirmed': True},
    {'delivered': False, 'duplicate': True, 'delivery_status': 'scope_conflict'},
])
def test_standard_unconfirmed_send_survives_stale_telegram_buttons(env, monkeypatch, provider_outcome):
    _standard_reply(env)
    key = env.tg.sent[0]["reply_markup"]["inline_keyboard"][0][0]["callback_data"].split(":")[1]
    deliver = MagicMock(return_value=provider_outcome)
    monkeypatch.setattr("distr.core.workflow.reply_delivery.deliver_ticket_reply", deliver)

    of.handle_telegram_text(f"oc:{key}:send")
    result = of.handle_telegram_text(f"oc:{key}:confirm")
    assert "not confirmed" in result["text"]
    assert "nothing went out" not in result["text"]
    assert "reply_markup" not in result
    assert of.get_flow(key)["status"] == "unconfirmed"
    assert of.posture_handles_reply(12)

    for action in ("check", "cancel", "send", "confirm"):
        assert of.handle_telegram_text(f"oc:{key}:{action}") == result
        assert of.get_flow(key)["status"] == "unconfirmed"
    repeated = _standard_reply(env)
    assert repeated["unconfirmed"] is True and len(env.tg.sent) == 1
    deliver.assert_called_once()


def test_two_telegram_confirmations_claim_one_client_send(env, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    monkeypatch.setattr(of.threading, "Thread", Thread)
    _standard_reply(env)
    key = env.tg.sent[0]["reply_markup"]["inline_keyboard"][0][0]["callback_data"].split(":")[1]
    entered, release = Event(), Event()

    def send(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return {"delivered": True}

    deliver = MagicMock(side_effect=send)
    monkeypatch.setattr("distr.core.workflow.reply_delivery.deliver_ticket_reply", deliver)
    of.handle_telegram_text(f"oc:{key}:send")
    with ThreadPoolExecutor(max_workers=1) as worker:
        first = worker.submit(of.handle_telegram_text, f"oc:{key}:confirm")
        try:
            assert entered.wait(5)
            assert of.get_flow(key)["status"] == "sending"
            assert "not confirmed" in of.handle_telegram_text(f"oc:{key}:confirm")["text"]
            assert "reply_markup" not in of.handle_telegram_text(f"oc:{key}:send")
        finally:
            release.set()
        assert first.result(5)["text"].startswith("Sent.")
    assert "already went out" in of.handle_telegram_text(f"oc:{key}:send")["text"]
    assert of.get_flow(key)["status"] == "sent"
    deliver.assert_called_once()


def test_standard_not_yet_sends_nothing(env, monkeypatch):
    _standard_reply(env)
    key = env.tg.sent[0]["reply_markup"]["inline_keyboard"][0][0]["callback_data"].split(":")[1]
    deliver = MagicMock()
    monkeypatch.setattr("distr.core.workflow.reply_delivery.deliver_ticket_reply", deliver)
    of.handle_telegram_text(f"oc:{key}:send")
    out = of.handle_telegram_text(f"oc:{key}:cancel")
    assert "nothing has been sent" in out["text"].lower()
    deliver.assert_not_called()


def test_standard_continue_check_and_not_done_loop(env, monkeypatch):
    _standard_reply(env)
    key = env.tg.sent[0]["reply_markup"]["inline_keyboard"][0][0]["callback_data"].split(":")[1]
    steer = MagicMock()
    monkeypatch.setattr("distr.core.initiative.action_handlers.control_development_thread", steer)
    monkeypatch.setattr(of, "_work_status", lambda tid: "Here's where things stand: it is still being worked on.")
    assert "keep going" in of.handle_telegram_text(f"oc:{key}:continue")["text"]
    assert steer.call_args[0][0]["chat_id"] == 909 and steer.call_args[0][0]["operation"] == "steer"
    check = of.handle_telegram_text(f"oc:{key}:check")
    assert "where things stand" in check["text"] and check["reply_markup"]
    reply = of.handle_telegram_text("It's not done, the button still doesn't work on iPhone")
    assert "passed it back" in reply["text"]
    assert "iPhone" in steer.call_args[0][0]["instruction"]
    _all_human([reply["text"], check["text"]])


def test_standard_with_telegram_off_keeps_legacy_gate(env):
    out = of.before_client_reply(ticket_id=1, shaped="Hi", envelope={}, target=SimpleNamespace(channel="whatsapp", contact="A"),
                                 adapter=object(), force=False, approval_context=None,
                                 settings={**STD, "initiative_allow_telegram": False})
    assert out is None and env.tg.sent == []


def test_quiet_client_reply_untouched(env):
    out = of.before_client_reply(ticket_id=1, shaped="Hi", envelope={}, target=SimpleNamespace(channel="whatsapp", contact="A"),
                                 adapter=object(), force=False, approval_context=None, settings=QUIET)
    assert out is None and env.tg.sent == []


def test_stale_flow_button_is_humanized(env):
    assert "already been wrapped up" in of.handle_telegram_text("oc:deadbeef:confirm")["text"]


def test_humanize_strips_ids_json_and_tracebacks():
    noisy = ('Created ticket #4242 run 555 {"ticket_id": 4242} wi:abc123:yes 3f9a2c4b5d6e7f80 '
             '/Users/paul/x/y.py\nTraceback (most recent call last):\n  File "x"\nValueError: boom')
    clean = of.humanize(noisy)
    assert not of.looks_technical(clean)
    assert "4242" not in clean and "Traceback" not in clean and "/Users" not in clean


# ============================ PROPOSALS ====================================

@pytest.mark.parametrize("text", ["/proposals", "any proposals pending?", "show me what's incoming",
                                  "give me an update on the proposals", "what has come in"])
def test_proposals_phrases_match(text):
    assert of.is_proposals_request(text)


@pytest.mark.parametrize("text", ["run 2", "dismiss 1", "create ticket all", "read my drafts",
                                  "what approvals are waiting", "hello there"])
def test_proposals_phrases_do_not_collide(text):
    assert not of.is_proposals_request(text)


@pytest.fixture
def proposals(monkeypatch):
    import distr.core.incoming.proposals as ip

    rows = [{"key": "a", "title": "Fix checkout", "source": "whatsapp", "project_name": "Shop"},
            {"key": "b", "title": "Hidden work", "source": "email", "project_name": "Old Site"}]
    created, dismissed = [], []
    monkeypatch.setattr(ip, "list_incoming_proposals", lambda limit=50: {"proposals": list(rows)})
    monkeypatch.setattr(ip, "_proposal_handoff_key", lambda p: f"handoff:{p['key']}")
    monkeypatch.setattr(ip, "create_proposal_thread",
                        lambda p, start_work=False, require_review=False, telegram_chat_id=None:
                        created.append((p["key"], start_work, require_review, telegram_chat_id)))
    monkeypatch.setattr(ip, "dismiss_proposal", lambda p: dismissed.append(p["key"]) or True)
    monkeypatch.setattr(of, "ignored_project_names", lambda: {"old site"})
    return SimpleNamespace(rows=rows, created=created, dismissed=dismissed)


def test_proposals_list_in_quiet_without_telegram_toggle(env, proposals, monkeypatch):
    monkeypatch.setattr(of, "load_settings", lambda: dict(QUIET))
    out = of.handle_telegram_text("/proposals")
    assert "Fix checkout" in out["text"] and "Hidden work" not in out["text"]
    buttons = out["reply_markup"]["inline_keyboard"][0]
    assert [b["text"] for b in buttons] == ["Accept 1", "Decline 1"]
    assert buttons[0]["callback_data"].startswith("ip:") and buttons[0]["callback_data"].endswith(":accept")
    _all_human([out["text"]])


def test_proposal_accept_decline_and_stale(env, proposals):
    h = of._proposal_key_hash(proposals.rows[0])
    assert "started work" in of.handle_telegram_text(f"ip:{h}:accept", chat_id="12345")["text"]
    assert proposals.created == [("a", True, True, 12345)]
    assert "set" in of.handle_telegram_text(f"ip:{h}:decline", chat_id=12345)["text"]
    assert proposals.dismissed == ["a"]
    proposals.rows.pop(0)
    assert "already been handled" in of.handle_telegram_text(f"ip:{h}:accept", chat_id=12345)["text"]
    assert proposals.created == [("a", True, True, 12345)]


@pytest.mark.parametrize("action,chat_id", [("accept", None), ("decline", -12345), ("unknown", 12345)])
def test_telegram_proposal_rejects_unscoped_or_unknown_actions(env, proposals, action, chat_id):
    key = of._proposal_key_hash(proposals.rows[0])
    assert of.handle_telegram_text(f"ip:{key}:{action}", chat_id=chat_id)
    assert proposals.created == [] and proposals.dismissed == []


@pytest.mark.parametrize("change", [
    {"tasks": ["Deploy to production"]},
    {"attachments": [{"id": "different-file"}]},
    {"project_id": 99},
])
def test_telegram_proposal_buttons_require_the_reviewed_revision(env, proposals, change):
    card = of.handle_telegram_text("/proposals")["reply_markup"]["inline_keyboard"][0]
    proposals.rows[0].update(change)
    for button in card:
        result = of.handle_telegram_text(button["callback_data"], chat_id=12345)
        assert "proposal changed" in result["text"]
    assert proposals.created == [] and proposals.dismissed == []
    fresh = of.handle_telegram_text("/proposals")["reply_markup"]["inline_keyboard"][0][0]
    assert "started work" in of.handle_telegram_text(fresh["callback_data"], chat_id=12345)["text"]
    assert proposals.created == [("a", True, True, 12345)]


def test_proposals_empty_inbox(env, monkeypatch):
    import distr.core.incoming.proposals as ip

    monkeypatch.setattr(ip, "list_incoming_proposals", lambda limit=50: {"proposals": []})
    monkeypatch.setattr(of, "ignored_project_names", lambda: set())
    assert "All clear" in of.handle_telegram_text("/proposals")["text"]


def test_telegram_ip_callback_routes_before_no_longer_available_fallback():
    src = (REPO / "distr/core/integrations/telegram/messages.py").read_text()
    assert src.index("_initiative_flow_reply") < src.index("This control is no longer available")
    assert src.index("_initiative_flow_reply") < src.index("handle_proposal_decision_reply(text")


# ============================ ASSIST REMOVED ===============================

def test_assist_removed_everywhere():
    from distr.core.initiative.policy import migrate_initiative_level
    from distr.gui.web.routes.settings import initiative as route_mod

    assert migrate_initiative_level("assist") == "operate"
    assert migrate_initiative_level("assistive") == "operate"
    assert "assist" not in route_mod.VALID_LEVELS
    assert route_mod.DEFAULTS["initiative_level"] == "operate"
    html = (REPO / "distr/gui/web/templates/settings/sections/initiative.html").read_text()
    assert 'data-level="assist"' not in html and html.count('class="initiative-btn') == 3
    assert ">Quiet<" in html and ">Standard<" in html and "Own outcomes" in html
    js = (REPO / "distr/gui/web/static/settings/js/initiative.js").read_text()
    assert "assist: {" not in js and "assist: 'operate'" in js


def test_saving_assist_stores_standard(monkeypatch):
    import asyncio
    import distr.core.settings as core_settings
    from fastapi import APIRouter
    from distr.gui.web.routes.settings import initiative as route_mod

    saved = []
    monkeypatch.setattr(core_settings, "save_settings_to_db", lambda data: saved.append(dict(data)), raising=False)  # conftest stub is a MagicMock; a prior undo deletes the attr
    router = APIRouter()
    route_mod.register_routes(router, None)
    endpoint = next(r.endpoint for r in router.routes if r.path == "/initiative" and "POST" in r.methods)
    ok = asyncio.run(endpoint(route_mod._InitiativePayload(initiative_level="assist")))
    assert ok.status_code == 200 and saved[-1]["initiative_level"] == "operate"


def test_service_start_persists_assist_as_standard(monkeypatch):
    import distr.core.utils as utils
    from distr.core.initiative.service import InitiativeService

    saved = []
    monkeypatch.setattr(utils, "load_settings_from_db", lambda: {"initiative_level": "assist"})
    monkeypatch.setattr(utils, "save_settings_to_db", lambda data: saved.append(dict(data)))
    import distr.core.initiative.service as svc

    for name in ("QTimer", "DraftQueue", "ContextAssembler"):
        monkeypatch.setattr(svc, name, MagicMock())
    InitiativeService(telegram_manager=MagicMock(telegram_user_id=None), chat_manager=MagicMock())
    assert saved == [{"initiative_level": "operate"}]
    assert InitiativeService._get_level({"initiative_level": "assist"}) == "operate"
    assert 'level == "assist"' not in (REPO / "distr/core/initiative/service.py").read_text()


# ============================ RECOMMENDATIONS ==============================

def test_recommendations_cadence_and_toggles(env, monkeypatch):
    # "Be my assistant": in-day, in-app nudges driven by activity; no 08:00 run, no Telegram.
    from datetime import timedelta

    recs = [of._rec("stuck", "1:1", "Shop checkout hasn't moved for 6 days.", project="Shop", weight=3)]
    monkeypatch.setattr(of, "build_recommendations", lambda settings=None, now=None: {
        "done": [], "recommendations": list(recs), "profile": [], "sources": {}})
    monkeypatch.setattr(of, "_activity_marker", lambda now: ("m1", {"Shop"}))
    monkeypatch.setattr(of, "_in_meeting_now", lambda now: False)
    on = {**STD, "initiative_recommendations": True}
    early = datetime(2026, 11, 12, 7, 0)
    assert of.maybe_send_recommendations({**QUIET, "initiative_recommendations": True,
                                          "initiative_allow_telegram": True}, now=early) is False
    assert of.maybe_send_recommendations(STD, now=early) is False  # toggle off by default
    assert of.maybe_send_recommendations(on, now=early) is True  # no fixed hour
    assert of.maybe_send_recommendations(on, now=early + timedelta(hours=10)) is False  # nothing new
    assert [n["text"] for n in of.assistant_nudges(now=early)] == ["Shop checkout hasn't moved for 6 days."]
    assert env.tg.sent == []


@pytest.mark.parametrize("text", ["/recommendations", "what's outstanding?", "project update please"])
def test_recommendations_on_demand(env, monkeypatch, text):
    monkeypatch.setattr(of, "project_report", lambda settings=None: "Here's your project round-up.")
    assert of.handle_telegram_text(text)["text"] == "Here's your project round-up."


def test_recommendations_setting_is_wired():
    from distr.core.db import Settings
    from distr.gui.web.routes.settings import initiative as route_mod

    assert hasattr(Settings, "initiative_recommendations")
    assert "initiative_recommendations" in route_mod.INITIATIVE_FIELDS
    assert route_mod.DEFAULTS["initiative_recommendations"] is False
    mig = (REPO / "distr/core/db/migrations.py").read_text()
    assert '("initiative_recommendations", "BOOLEAN DEFAULT 0")' in mig
    html = (REPO / "distr/gui/web/templates/settings/sections/initiative.html").read_text()
    assert 'id="initiative_recommendations"' in html


# ============================ IGNORE / PINNED ==============================

def test_ignored_projects_dropped_and_pinned_first(monkeypatch):
    import distr.core.initiative.work_scanner as ws

    monkeypatch.setattr(of, "ignored_project_ids", lambda: {3})
    monkeypatch.setattr(of, "pinned_board_ids", lambda: [20])
    monkeypatch.setattr(ws, "_ignored_board_ids", lambda ignored: {30})
    scan = {"proposals": [
        {"action_type": "workflow_start", "payload": {"board_id": 10, "project_id": 1}},
        {"action_type": "workflow_start", "payload": {"board_id": 11, "project_id": 3}},
        {"action_type": "email_triage", "payload": {"linked_project_id": 3}},
        {"action_type": "ticket_lane_move", "payload": {"board_id": 30}},
        {"action_type": "ticket_lane_move", "payload": {"board_id": 20}},
    ]}
    ws._apply_project_scope(scan)
    boards = [p["payload"].get("board_id") for p in scan["proposals"]]
    assert boards == [20, 10]
    assert scan["proposals"][0]["pinned"] is True


def test_orchestrator_selection_prefers_pinned():
    from distr.core.initiative.service import InitiativeService

    bundle = SimpleNamespace(work_scan={"proposals": [
        {"action_type": "jira_intake", "description": "jira", "payload": {}},
        {"action_type": "board_triage", "description": "pinned", "payload": {}, "pinned": True},
    ]})
    action = InitiativeService._proposal_from_work_scan(bundle, {}, "operate")
    assert action.description == "pinned"


def test_ignored_project_hidden_from_proposals_and_report(monkeypatch):
    monkeypatch.setattr(of, "ignored_project_names", lambda: {"shop"})
    import distr.core.incoming.proposals as ip

    monkeypatch.setattr(ip, "list_incoming_proposals",
                        lambda limit=50: {"proposals": [{"title": "x", "project_name": "Shop"}]})
    assert of._pending_proposals() == []


def test_ignore_flag_is_wired_through_project_ui():
    from distr.core.db.projects import Project
    from distr.gui.web.routes.settings._shared import ProjectUpdate

    assert hasattr(Project, "agent_ignored")
    assert "agent_ignored" in ProjectUpdate.model_fields
    assert "ALTER TABLE projects ADD COLUMN agent_ignored BOOLEAN DEFAULT 0" in (REPO / "distr/core/db/migrations.py").read_text()
    assert 'id="detail-agent-ignored"' in (REPO / "distr/gui/web/templates/projects/tabs/details.html").read_text()
    js = (REPO / "distr/gui/web/static/projects/js/projects.js").read_text()
    assert "project.agent_ignored === true" in js and "agent_ignored: !!(" in js
    route = (REPO / "distr/gui/web/routes/settings/projects.py").read_text()
    assert '"agent_ignored": bool(getattr(project, "agent_ignored", False))' in route
    assert "project.agent_ignored = bool(payload.agent_ignored)" in route
