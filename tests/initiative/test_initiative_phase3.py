"""Phase 3: clearer control labels, Own-outcomes update channel (announce +
progress), the recommendations observer, and decision-support framing.

Every outbound path is mocked: Telegram, WhatsApp relay, TTS, client adapters,
Google Calendar and MemPalace. Nothing leaves the machine; state goes to tmp.
"""

import json
import re
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import distr.core.initiative.outcome_flow as of

REPO = Path(__file__).resolve().parents[2]
TEMPLATE = REPO / "distr/gui/web/templates/settings/sections/initiative.html"
QUIET = {"initiative_level": "observe", "initiative_allow_telegram": False}
STD = {"initiative_level": "operate", "initiative_allow_telegram": True}
# Own with "Ask before messaging clients" switched off: the automatic path. Ask-first is covered in test_initiative_fixes_20261112.py.
OWN = {"initiative_level": "own", "initiative_allow_telegram": True, "initiative_scan_whatsapp": True,
       "initiative_scan_email": True, "initiative_ask_external_comms": False}


class Outbox:
    """Collects every would-be outbound message, per channel."""

    def __init__(self):
        self.telegram, self.whatsapp, self.voice, self.events = [], [], [], []

    def send_to_telegram(self, text, reply_markup=None):
        self.telegram.append(text)
        return True


@pytest.fixture
def out(monkeypatch, tmp_path):
    box = Outbox()
    monkeypatch.setenv("DECISIONS_DB_DIR", str(tmp_path))
    monkeypatch.setattr(of, "_telegram_manager", lambda: box)
    monkeypatch.setattr("distr.core.orchestrator.emit_event", lambda **kw: box.events.append(kw))
    monkeypatch.setattr("distr.core.integrations.whatsapp.relay_client.send_message_via_relay",
                        lambda jid, text, caption="": box.whatsapp.append((jid, text)) or {"success": True})
    monkeypatch.setattr("distr.core.signals.speak_text_directly_event_queue",
                        lambda text: box.voice.append(text) or True)
    monkeypatch.setattr(of, "_intake_context", lambda token: {
        "source": "whatsapp", "source_label": "WhatsApp", "client": "Anna", "project": "Shop",
        "summary": "The checkout button is broken on mobile"})

    class SyncThread:
        def __init__(self, target, args=(), **_):
            self.target, self.args = target, args

        def start(self):
            self.target(*self.args)

    monkeypatch.setattr(of.threading, "Thread", SyncThread)
    box.tmp = tmp_path
    box.mp = monkeypatch
    return box


def audit_lines(tmp):
    path = tmp / "logs" / "initiative_outcome_audit.jsonl"
    return [json.loads(l) for l in path.read_text().splitlines()] if path.exists() else []


def human(text):
    assert not of.looks_technical(text), text
    assert "{" not in text and "Traceback" not in text and "_" not in text, text


# ============================ 1. Control labels ============================

EXPECTED_LABELS = {
    "initiative_scan_email": "Notify on incoming email",
    "initiative_scan_whatsapp": "Notify on incoming WhatsApp messages",
    "initiative_scan_telegram": "Notify on incoming Telegram messages",
    "initiative_scan_boards": "Watch local project boards",
    "initiative_scan_external_boards": "Watch Jira and Trello boards",
    "initiative_suggest_backlog_promotion": "Suggest promoting backlog items",
    "initiative_allow_routine_tasks": "Run routines automatically",
    "initiative_allow_ticket_lane_moves": "Move tickets between lanes",
    "initiative_allow_workflow_start": "Start workflows on tickets",
    "initiative_allow_project_cli": "Run project coding agents",
    "initiative_allow_telegram": "Allow Telegram conversations",
    "initiative_telegram_notify_suggestions": "Send triage updates to Telegram",
    "initiative_recommendations": "Be my assistant",
    "initiative_ask_external_comms": "Ask before messaging clients or others",
    "initiative_ask_file_changes": "Ask before changing files",
    "initiative_ask_sensitive": "Ask before sensitive actions",
}


def test_every_switch_has_a_purpose_label_and_helper():
    html = TEMPLATE.read_text()
    toggles = re.findall(r'<label class="settings-toggle[^"]*"><input type="checkbox" id="([^"]+)".*?'
                         r'<span class="settings-toggle__label">([^<]+)<span class="block text-xs[^"]*">([^<]+)</span>',
                         html)
    found = {i: (label, helper) for i, label, helper in toggles}
    assert set(found) == set(EXPECTED_LABELS)
    for field, label in EXPECTED_LABELS.items():
        assert found[field][0] == label
        assert len(found[field][1]) > 15
    for bare in (">Email<", ">WhatsApp<", ">Telegram<", ">Local boards<", ">Recommendations<"):
        assert bare not in html


def test_every_switch_is_saved_by_the_route():
    from distr.gui.web.routes.settings import initiative as route_mod

    assert set(EXPECTED_LABELS) <= set(route_mod.INITIATIVE_FIELDS)


# ============================ 2. Own outcomes channel ======================

def test_channel_default_is_telegram_if_on_else_log():
    assert of.own_channel_choice({"initiative_allow_telegram": True}) == "telegram"
    assert of.own_channel_choice({"initiative_allow_telegram": False}) == "log"
    assert of.own_channel_choice({"initiative_own_channel": "voice"}) == "voice"
    assert of.own_channel_choice({"initiative_own_channel": "carrier pigeon", "initiative_allow_telegram": True}) == "telegram"


def test_owner_whatsapp_number_normalised():
    assert of.owner_whatsapp_jid({"initiative_owner_whatsapp": "+27 82 123 4567"}) == "27821234567@s.whatsapp.net"
    assert of.owner_whatsapp_jid({"initiative_owner_whatsapp": "123"}) == ""
    assert of.owner_whatsapp_jid({}) == ""


@pytest.mark.parametrize("channel", ["telegram", "whatsapp", "voice", "log"])
def test_each_channel_routes_and_audits_full_text(out, channel):
    settings = {**OWN, "initiative_own_channel": channel, "initiative_owner_whatsapp": "+27821234567"}
    result = of.notify_owner('Started the work for Anna {"ticket_id": 9}', settings, kind="own_progress")
    assert result["channel"] == channel and result["delivered"] is True and result["fallback"] is None
    sent = {"telegram": out.telegram, "whatsapp": [t for _, t in out.whatsapp], "voice": out.voice, "log": []}
    for name, msgs in sent.items():
        assert len(msgs) == (1 if name == channel and name != "log" else 0), name
    if channel == "whatsapp":
        assert out.whatsapp[0][0] == "27821234567@s.whatsapp.net"
    rec = audit_lines(out.tmp)[-1]
    assert rec["event"] == "own_progress" and rec["channel"] == channel
    assert rec["text"] == "Started the work for Anna"
    human(rec["text"])


def test_whatsapp_without_owner_number_falls_back_clearly(out):
    res = of.notify_owner("Update", {**OWN, "initiative_own_channel": "whatsapp"})
    assert res["channel"] == "telegram" and "no WhatsApp number" in res["fallback"]
    assert out.whatsapp == [] and out.telegram == ["Update"]
    res = of.notify_owner("Update", {**OWN, "initiative_own_channel": "whatsapp", "initiative_allow_telegram": False})
    assert res["channel"] == "log" and res["delivered"] is True
    assert audit_lines(out.tmp)[-1]["fallback"] == "no WhatsApp number of your own is set"


def test_voice_failure_falls_back(out):
    out.mp.setattr("distr.core.signals.speak_text_directly_event_queue", lambda text: False)
    res = of.notify_owner("Update", {**OWN, "initiative_own_channel": "voice"})
    assert res["channel"] == "telegram" and res["fallback"] == "voice isn't available right now"


def test_telegram_choice_respects_toggle(out):
    res = of.notify_owner("Update", {**OWN, "initiative_own_channel": "telegram", "initiative_allow_telegram": False})
    assert res["channel"] == "log" and out.telegram == []


def test_progress_routing_only_in_own(out):
    assert of.route_own_update("Still running", {**STD, "initiative_own_channel": "voice"}) is False
    assert of.route_own_update("Still running", {**QUIET, "initiative_own_channel": "voice"}) is False
    assert out.voice == []
    assert of.route_own_update("Still running", {**OWN, "initiative_own_channel": "voice"}) is True
    assert out.voice == ["Still running"]
    # Telegram choice and questions that need an answer stay on the existing sender.
    assert of.route_own_update("x", {**OWN, "initiative_own_channel": "telegram"}) is False
    assert of.route_own_update("Approve?", {**OWN, "initiative_own_channel": "voice"}, requires_response=True) is False
    assert out.telegram == []


def _service():
    from distr.core.initiative.service import InitiativeService

    service = InitiativeService.__new__(InitiativeService)
    service.telegram_manager = MagicMock(telegram_user_id=42)
    service.event_queue = None
    return service


def test_execution_nudges_follow_the_chosen_channel(out, monkeypatch):
    import distr.core.initiative.service as svc

    monkeypatch.setattr("distr.core.engagement_gates.proactive_delivery_blocked", lambda **kw: (False, ""))
    engagement = MagicMock()
    monkeypatch.setattr(svc, "HumanEngagementService", engagement)
    settings = {**OWN, "initiative_own_channel": "whatsapp", "initiative_owner_whatsapp": "+27821234567"}
    _service()._send_telegram_if_allowed("The checkout fix is halfway done.", settings, kind="workflow_idle_nudge")
    assert [t for _, t in out.whatsapp] == ["The checkout fix is halfway done."]
    engagement.assert_not_called()
    # Standard keeps the existing Telegram engagement path.
    _service()._send_telegram_if_allowed("Halfway done.", {**STD, "initiative_own_channel": "whatsapp"}, kind="workflow_idle_nudge")
    engagement.assert_called_once()


def test_own_work_action_is_announced_before_it_runs(out):
    from distr.core.initiative.proposed_action import ProposedAction

    action = ProposedAction(action_type="workflow_start", description="Start the release workflow for Shop",
                            draft="Run the release checklist, then deploy to staging.")
    _service()._announce_own_start(action, {**OWN, "initiative_own_channel": "voice"})
    assert out.voice and out.voice[0].startswith("I'm going to start this work: Start the release workflow for Shop.")
    assert "Here's what I'm doing: Run the release checklist" in out.voice[0]
    src = (REPO / "distr/core/initiative/service.py").read_text()
    assert src.index("self._announce_own_start(action, settings)\n            self._execute_action(") > 0


def _client_reply(settings, delivered=True):
    adapter = SimpleNamespace(name="whatsapp")
    return of.before_client_reply(ticket_id=12, shaped="Hi Anna, it's fixed.", envelope={"recipient": "+27 82"},
                                  target=SimpleNamespace(channel="whatsapp", contact="Anna"), adapter=adapter,
                                  force=False, approval_context=None, settings=settings)


def test_own_end_to_end_announce_run_and_finish_on_whatsapp(out):
    client_sends = []
    out.mp.setattr("distr.core.kanban.whatsapp_intake_approval._execute_ready",
                   lambda token, chat_id=None: {"action": "ready", "ticket_id": 12, "development_chat_id": 9})
    out.mp.setattr("distr.core.workflow.reply_delivery._deliver_prepared",
                   lambda tid, body, envelope, adapter, workflow=None: client_sends.append(body) or {"delivered": True})
    settings = {**OWN, "initiative_own_channel": "whatsapp", "initiative_owner_whatsapp": "+27821234567"}
    assert of.on_intake_staged({"token": "t1"}, settings=settings) is True
    _client_reply(settings)
    owner_msgs = [t for _, t in out.whatsapp]
    assert owner_msgs[0].startswith("I'm going to start this work: Anna's request from WhatsApp for Shop.")
    assert "Development thread" in owner_msgs[0]
    assert owner_msgs[1].startswith("Finished: the work for Anna is done")
    assert client_sends == ["Hi Anna, it's fixed."]
    assert out.telegram == [] and out.voice == []
    events = [e["event"] for e in audit_lines(out.tmp)]
    assert events == ["own_announce", "own_intake_accepted", "own_client_reply_sent", "own_finished"]
    for msg in owner_msgs:
        human(msg)


def test_own_blocked_is_reported(out):
    out.mp.setattr("distr.core.kanban.whatsapp_intake_approval._execute_ready",
                   lambda token, chat_id=None: {"action": "ready_failed"})
    of.on_intake_staged({"token": "t2"}, settings={**OWN, "initiative_own_channel": "log"})
    events = audit_lines(out.tmp)
    assert events[-1]["event"] == "own_blocked" and "blocked" in events[-1]["text"]
    assert out.telegram == [] and out.whatsapp == [] and out.voice == []


def test_quiet_and_standard_never_announce(out):
    assert of.on_intake_staged({"token": "q"}, settings={**QUIET, "initiative_own_channel": "voice"}) is False
    pending = {"token": "s"}
    assert of.on_intake_staged(pending, settings={**STD, "initiative_own_channel": "voice"}) is None
    assert out.voice == [] and out.whatsapp == [] and audit_lines(out.tmp) == []


def _post(monkeypatch, **payload):
    import asyncio
    import distr.core.settings as core_settings
    from fastapi import APIRouter
    from distr.gui.web.routes.settings import initiative as route_mod

    saved = []
    monkeypatch.setattr(core_settings, "save_settings_to_db", lambda data: saved.append(dict(data)), raising=False)  # conftest stub is a MagicMock; a prior undo deletes the attr
    router = APIRouter()
    route_mod.register_routes(router, None)
    endpoint = next(r.endpoint for r in router.routes if r.path == "/initiative" and "POST" in r.methods)
    resp = asyncio.run(endpoint(route_mod._InitiativePayload(**payload)))
    return resp.status_code, saved


def test_channel_setting_saved_and_validated(monkeypatch):
    code, saved = _post(monkeypatch, initiative_level="own", initiative_own_channel="WhatsApp",
                        initiative_owner_whatsapp="+27 82 123 4567")
    assert code == 200
    assert saved[-1]["initiative_own_channel"] == "whatsapp" and saved[-1]["initiative_owner_whatsapp"] == "+27821234567"
    assert _post(monkeypatch, initiative_own_channel="fax")[0] == 400
    assert _post(monkeypatch, initiative_owner_whatsapp="12")[0] == 400


def test_channel_columns_and_panel_wired():
    from distr.core.db import Settings
    from distr.gui.web.routes.settings import initiative as route_mod

    assert hasattr(Settings, "initiative_own_channel") and hasattr(Settings, "initiative_owner_whatsapp")
    assert route_mod.DEFAULTS["initiative_own_channel"] == ""
    mig = (REPO / "distr/core/db/migrations.py").read_text()
    assert '("initiative_own_channel", "VARCHAR DEFAULT \'\'")' in mig
    html = TEMPLATE.read_text()
    assert 'id="initiative-own-updates" class="mb-6 rounded-lg border border-white/10 bg-black/10 p-4 hidden"' in html
    assert sorted(re.findall(r'name="initiative_own_channel" value="(\w+)"', html)) == ["log", "telegram", "voice", "whatsapp"]


# ============================ 3. Recommendations observer ==================

def _sources(monkeypatch, *, profile=("Paul cares most about shipping Shop and protecting family evenings",)):
    monkeypatch.setattr(of, "_project_observations", lambda now: (
        ["Shop: Fix checkout"],
        [of._rec("stuck", "1:5", "\u201cInvoice export\u201d in Ledger hasn't moved for 9 days.", project="Ledger", weight=3),
         of._rec("stuck", "2:6", "\u201cMobile layout\u201d in Shop hasn't moved for 6 days.", project="Shop", weight=3),
         of._rec("stuck", "3:7", "\u201cLogo\u201d in Brand hasn't moved for 6 days.", project="Brand", pinned=True, weight=1)],
        {"Shop": 4, "Ledger": 2}))
    monkeypatch.setattr(of, "_inbox_observations", lambda settings: [
        of._rec("waiting", "email|quote", "Waiting on you (Gmail): Anna asked for a quote", weight=2)])
    monkeypatch.setattr(of, "_calendar_observations", lambda now, counts: [
        of._rec("calendar", "Shop sync|2026-11-12", "Before \u201cShop sync\u201d at 10:00, Shop has 4 open items.",
                project="Shop", weight=3)])
    monkeypatch.setattr(of, "profile_hints", lambda limit=3: list(profile))


def test_recommendations_rank_pinned_then_what_matters_to_paul(out, monkeypatch):
    _sources(monkeypatch)
    data = of.build_recommendations(STD, now=datetime(2026, 11, 12, 9))
    order = [r["project"] or r["area"] for r in data["recommendations"]]
    assert order[0] == "Brand"  # pinned first
    assert order.index("Shop") < order.index("Ledger")  # matches Paul's stated priorities
    text = of.recommendations_digest(STD, now=datetime(2026, 11, 12, 9))
    assert "with your priorities in mind" in text
    for section in ("Worth a decision", "Waiting on you", "Done lately"):
        assert section in text
    human(text.replace("\u201c", "").replace("\u201d", ""))


def test_recommendations_degrade_without_memory_or_sources(out, monkeypatch):
    monkeypatch.setattr(of, "_project_observations", MagicMock(side_effect=RuntimeError("db down")))
    monkeypatch.setattr(of, "_inbox_observations", MagicMock(side_effect=RuntimeError("scan down")))
    monkeypatch.setattr(of, "_calendar_observations", MagicMock(side_effect=RuntimeError("no google")))
    monkeypatch.setattr("distr.core.mempalace.adapter.get_adapter", MagicMock(side_effect=RuntimeError("no palace")))
    for name in ("_tension_observations", "_pending_reply_observations", "_link_observations", "_automation_observations"):
        monkeypatch.setattr(of, name, MagicMock(side_effect=RuntimeError("source down")))  # never read a real link cache
    data = of.build_recommendations(STD)
    assert data["recommendations"] == [] and data["profile"] == []
    assert {k: data["sources"][k] for k in ("projects", "inbox", "calendar", "memory")} == {
        "projects": "unavailable", "inbox": "unavailable", "calendar": "unavailable", "memory": "empty"}
    # 12 Nov fixes: pending replies, project links, automations and ignore-vs-waiting tensions are sources too.
    assert {"tensions", "pending_replies", "links", "automations"} <= set(data["sources"])
    assert "Quiet on all fronts" in of.recommendations_digest(STD)


def test_mempalace_profile_is_read(monkeypatch):
    adapter = MagicMock()
    adapter.search.return_value = {"results": [{"content": "Paul is a founder; prefers short updates."}]}
    monkeypatch.setattr("distr.core.mempalace.adapter.get_adapter", lambda: adapter)
    assert of.profile_hints() == ["Paul is a founder; prefers short updates."]


def _world(monkeypatch, *, marker="m1", active=("Shop",), meeting=False):
    monkeypatch.setattr(of, "_activity_marker", lambda now: (marker, set(active)))
    monkeypatch.setattr(of, "_in_meeting_now", lambda now: meeting)


def test_assistant_nudges_in_app_no_fixed_clock(out, monkeypatch):
    _sources(monkeypatch)
    _world(monkeypatch)
    on = {**STD, "initiative_recommendations": True}
    early = datetime(2026, 11, 12, 6, 30)  # before the old 08:00 round-up
    assert of.maybe_send_recommendations(on, now=early) is True
    nudges = of.assistant_nudges(now=early)
    assert len(nudges) == of.ASSISTANT_ITEMS_PER_NUDGE
    assert {n["project"] for n in nudges} == {"Shop"}  # what you're working on comes first
    assert all(n["why"] == "You've been working in Shop" for n in nudges)
    for n in nudges:
        human(n["text"])
    assert out.telegram == [] and out.whatsapp == [] and out.voice == []
    assert audit_lines(out.tmp)[-1]["event"] == "assistant_nudge_offered"
    assert audit_lines(out.tmp)[-1]["channel"] == "in_app"
    # On demand (/recommendations in Telegram) still shows the full picture.
    assert "Invoice export" in of.handle_telegram_text("/recommendations")["text"]


def test_assistant_paced_and_driven_by_observed_activity(out, monkeypatch):
    _sources(monkeypatch)
    _world(monkeypatch)
    on = {**STD, "initiative_recommendations": True}
    t0 = datetime(2026, 11, 12, 10, 0)
    assert of.maybe_send_recommendations(on, now=t0) is True
    _world(monkeypatch, marker="m2")
    assert of.maybe_send_recommendations(on, now=t0 + timedelta(minutes=20)) is False  # too soon
    _world(monkeypatch, marker="m1")  # no new activity after the gap
    assert of.maybe_send_recommendations(on, now=t0 + timedelta(minutes=50)) is False
    _world(monkeypatch, marker="m3", active=())  # board activity observed
    assert of.maybe_send_recommendations(on, now=t0 + timedelta(minutes=55)) is True
    assert len(of.assistant_nudges(now=t0 + timedelta(minutes=55))) == 4
    # Remaining item comes on the slow refresh even without board activity.
    assert of.maybe_send_recommendations(on, now=t0 + timedelta(minutes=55 + 91)) is True
    # Nothing new: no nudge, no repeat.
    _world(monkeypatch, marker="m4")
    assert of.maybe_send_recommendations(on, now=t0 + timedelta(hours=5)) is False
    assert out.telegram == []


def test_assistant_holds_back_in_meetings_and_respects_posture(out, monkeypatch):
    _sources(monkeypatch)
    day = datetime(2026, 11, 12, 11, 0)
    _world(monkeypatch, meeting=True)
    on = {**STD, "initiative_recommendations": True}
    assert of.maybe_send_recommendations(on, now=day) is False
    assert of.assistant_nudges(now=day) == []
    _world(monkeypatch, marker="m2")
    assert of.maybe_send_recommendations({**QUIET, "initiative_recommendations": True,
                                          "initiative_allow_telegram": True}, now=day + timedelta(minutes=10)) is False
    assert of.maybe_send_recommendations(STD, now=day + timedelta(minutes=10)) is False  # setting off
    # In-app only, so it no longer depends on Telegram being on; Own doesn't send either.
    assert of.maybe_send_recommendations({**on, "initiative_allow_telegram": False}, now=day + timedelta(minutes=10)) is True
    own = {**OWN, "initiative_recommendations": True, "initiative_own_channel": "whatsapp",
           "initiative_owner_whatsapp": "+27821234567"}
    _world(monkeypatch, marker="m3")
    assert of.maybe_send_recommendations(own, now=day + timedelta(minutes=60)) is True
    assert out.telegram == [] and out.whatsapp == [] and out.voice == []


def test_assistant_nudges_can_be_ignored_or_dismissed_and_fade(out, monkeypatch):
    _sources(monkeypatch)
    _world(monkeypatch)
    on = {**STD, "initiative_recommendations": True}
    t0 = datetime(2026, 11, 12, 9, 0)
    assert of.maybe_send_recommendations(on, now=t0) is True
    first = of.assistant_nudges(now=t0)[0]
    assert of.dismiss_assistant_nudge(first["id"]) is True
    assert of.dismiss_assistant_nudge(first["id"]) is False
    assert first["id"] not in [n["id"] for n in of.assistant_nudges(now=t0)]
    assert of.assistant_nudges(now=t0 + timedelta(hours=13)) == []


def test_assistant_daily_cap(out, monkeypatch):
    many = [of._rec("stuck", f"x:{i}", f"Item {i} hasn't moved.", project="Shop", weight=1) for i in range(40)]
    monkeypatch.setattr(of, "build_recommendations", lambda settings=None, now=None: {
        "done": [], "recommendations": many, "profile": [], "sources": {}})
    on = {**STD, "initiative_recommendations": True}
    t = datetime(2026, 11, 12, 6, 0)
    offered = 0
    for i in range(30):
        _world(monkeypatch, marker=f"m{i}")
        offered += of.maybe_send_recommendations(on, now=t + timedelta(minutes=46 * i)) and (t + timedelta(minutes=46 * i)).date() == t.date()
    assert offered == of.ASSISTANT_MAX_PER_DAY


def test_project_observations_respect_ignored_and_pinned(monkeypatch, tmp_path):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from distr.core.db.kanban import KanbanBoard, KanbanLane, KanbanTicket
    from distr.core.db.projects import Project

    engine = create_engine(f"sqlite:///{tmp_path / 'p.db'}")
    for model in (Project, KanbanBoard, KanbanLane, KanbanTicket):
        model.__table__.create(engine)
    Session = sessionmaker(bind=engine)
    now = datetime(2026, 11, 12, 9)
    old = now - timedelta(days=9)
    with Session() as s:
        for pid, name, ignored in ((1, "Shop", False), (2, "Secret", True), (3, "Brand", False)):
            s.add(Project(id=pid, name=name, agent_ignored=ignored, kanban_board_id=pid))
            s.add(KanbanBoard(id=pid, name=name, default_project_id=pid))
            s.add(KanbanLane(id=pid * 10, board_id=pid, name="In progress"))
            s.add(KanbanLane(id=pid * 10 + 1, board_id=pid, name="Done"))
            s.add(KanbanTicket(id=pid * 100, lane_id=pid * 10, title=f"{name} stuck task", modified_date=old))
            s.add(KanbanTicket(id=pid * 100 + 1, lane_id=pid * 10 + 1, title=f"{name} shipped", modified_date=now))
        s.commit()

    @contextmanager
    def session_scope():
        with Session() as s:
            yield s

    monkeypatch.setattr("distr.core.db.get_session", session_scope)
    monkeypatch.setattr(of, "ignored_project_ids", lambda: {2})
    monkeypatch.setattr(of, "pinned_board_ids", lambda: [3])
    done, recs, counts = of._project_observations(now)
    assert [r["project"] for r in recs] == ["Brand", "Shop"]
    assert recs[0]["pinned"] is True and "9 days" in recs[0]["text"]
    assert "Secret" not in counts and all("Secret" not in line for line in done)


def test_calendar_observations_link_meetings_to_projects(monkeypatch):
    import distr.core.agent.services.integrations.google_workspace as gw

    class FakeGoogle:
        def is_connected(self):
            return True

        def get_calendar_events(self, time_min=None, time_max=None, max_results=10):
            return [{"summary": "Shop sync with Anna", "start": {"dateTime": "2026-11-12T10:00:00+02:00"}}]

    monkeypatch.setattr(gw, "GoogleWorkspaceConnector", FakeGoogle)
    recs = of._calendar_observations(datetime(2026, 11, 12, 8), {"Shop": 4})
    assert len(recs) == 1 and "at 10:00, Shop has 4 open items" in recs[0]["text"]


def test_inbox_observations_use_the_toggle_aware_scan(monkeypatch):
    import distr.core.initiative.work_scanner as ws

    seen = {}
    monkeypatch.setattr(ws, "build_work_scan", lambda settings: seen.setdefault("s", settings) and {"proposals": [
        {"action_type": "email_triage", "description": "Anna asked for a quote", "payload": {"source": "email"}}]})
    recs = of._inbox_observations({"initiative_scan_email": True})
    assert recs[0]["text"] == "Waiting on you (Gmail): Anna asked for a quote"
    assert seen["s"] == {"initiative_scan_email": True}


# ============================ 4. Decision-support framing ==================

def test_prompts_and_copy_frame_the_agent_as_decision_support():
    from distr.core.initiative import service as svc

    assert "help the user make good decisions" in svc.DECISION_SUPPORT_PURPOSE
    assert "{DECISION_SUPPORT_PURPOSE}" in (REPO / "distr/core/initiative/service.py").read_text()
    prompt = (REPO / "distr/core/agent/services/llm/system_prompt.txt").read_text()
    assert "Your core purpose is to help the user decide well" in prompt
    assert "help you decide" in TEMPLATE.read_text()
    card = of.intake_card("tok", {"client": "Anna", "source_label": "WhatsApp", "project": "", "summary": "Fix it"})
    assert "Your call:" in card["text"] and "nothing is sent to Anna" in card["text"]
    assert "Your options:" in of.work_done_text("Anna", "whatsapp", "Done")
