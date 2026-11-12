"""Same-channel rule for "Be my assistant" (12 Nov 2026, replaces intelligent channel selection).

Paul: always reply on the channel the incoming message came from; never pick a "best" one.
Every outbound path is mocked (Telegram manager, TTS queue, WhatsApp relay, MemPalace,
orchestrator events). State goes to tmp. Nothing leaves the machine.
"""

import json
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock

import pytest

import distr.core.initiative.channel_select as cs
import distr.core.initiative.outcome_flow as of

REPO = Path(__file__).resolve().parents[2]
STD_ON = {"initiative_level": "operate", "initiative_allow_telegram": False, "initiative_recommendations": True}
PAUL = 4242


class FakeTelegram:
    def __init__(self, connected=True, accept=True):
        self.telegram_user_id = PAUL
        self.connected, self.accept, self.sent = connected, accept, []

    def is_connected(self):
        return self.connected

    def send_to_telegram(self, text, reply_markup=None):
        self.sent.append({"text": text, "reply_markup": reply_markup})
        return self.accept


class World:
    pass


@pytest.fixture
def world(monkeypatch, tmp_path):
    w = World()
    w.tg = FakeTelegram()
    w.voice, w.whatsapp, w.events, w.drawers = [], [], [], []
    monkeypatch.setenv("DECISIONS_DB_DIR", str(tmp_path))
    monkeypatch.setattr(of, "_telegram_manager", lambda: w.tg)
    monkeypatch.setattr("distr.core.orchestrator.emit_event", lambda **kw: w.events.append(kw))
    monkeypatch.setattr("distr.core.signals.speak_text_directly_event_queue", lambda text: w.voice.append(text) or True)
    monkeypatch.setattr("distr.core.integrations.whatsapp.relay_client.send_message_via_relay",
                        lambda jid, text, caption="", **kw: w.whatsapp.append((jid, text)) or {"success": True})
    adapter = MagicMock()
    adapter.add_drawer.side_effect = lambda content, **kw: w.drawers.append((content, kw)) or {"success": True}
    adapter.search.return_value = {"results": []}
    monkeypatch.setattr("distr.core.mempalace.adapter.get_adapter", lambda: adapter)
    monkeypatch.setattr(of, "_in_meeting_now", lambda now: False)
    recs = [of._rec("stuck", f"s:{i}", f"Item {i} in Shop hasn't moved for 6 days.", project="Shop", weight=3)
            for i in range(12)]
    monkeypatch.setattr(of, "build_recommendations", lambda settings=None, now=None: {
        "done": [], "recommendations": list(recs), "profile": [], "sources": {}})
    w.marker = 0

    def tick(now, *, observed=True, active=("Shop",)):
        if observed:
            w.marker += 1
        monkeypatch.setattr(of, "_activity_marker", lambda _now: (f"m{w.marker}", set(active)))
        return of.maybe_send_recommendations(STD_ON, now=now)

    w.tick = tick
    w.tmp = tmp_path
    return w


def audit(tmp):
    path = tmp / "logs" / "initiative_outcome_audit.jsonl"
    return [json.loads(l) for l in path.read_text().splitlines()] if path.exists() else []


def all_nudges():
    return (of._load_state().get("assistant") or {}).get("nudges") or []


def nudge(i=1):
    now = datetime.now()
    return {"id": f"n{i}-{int(now.timestamp())}", "fp": f"n{i}", "area": "stuck", "project": "Shop",
            "pinned": False, "text": f"Item {i} needs a look.", "why": "test",
            "created": now.isoformat(timespec="minutes"), "created_ts": now.timestamp()}


# ----------------------------------------------------------------- the rule (pure)

@pytest.mark.parametrize("incoming, expected", [
    ("telegram", "telegram"), ("tg", "telegram"),
    ("spoken", "spoken"), ("voice", "spoken"), ("ptt", "spoken"), ("push-to-talk", "spoken"), ("hotkey", "spoken"),
    ("in_app", "in_app"), ("app", "in_app"), ("web", "in_app"),
])
def test_reply_goes_back_on_the_incoming_channel(incoming, expected):
    choice = cs.decide(incoming=incoming)
    assert choice.channel == expected and not choice.ask


def test_no_incoming_message_stays_in_the_app():
    for value in (None, "", "   "):
        choice = cs.decide(incoming=value)
        assert choice.channel == "in_app" and not choice.ask


@pytest.mark.parametrize("incoming", ["carrier pigeon", "sms", "whatsapp", "gmail", "slack"])
def test_unknown_incoming_channel_is_refused_not_mapped(incoming):
    # WhatsApp/Gmail are client channels handled by reply_delivery/source_reply, never by this path.
    choice = cs.decide(incoming=incoming)
    assert choice.channel is None and not choice.ask


def test_activity_signals_no_longer_pick_a_channel():
    # The old scorer is gone: no choose(), no Signals, no learned preference.
    for name in ("choose", "Signals", "observe", "_history_penalty", "_most_recent_place", "_remember_in_mempalace"):
        assert not hasattr(cs, name), name
    assert cs.decide(incoming=None, board_observed=True, board_recent=True, in_meeting=False).channel == "in_app"
    src = (REPO / "distr/core/initiative/channel_select.py").read_text()
    assert "add_drawer" not in src and "confidence threshold" not in src.lower()


# ----------------------------------------------------------------- delivery

def test_proactive_suggestions_stay_in_the_app_however_active_paul_is(world):
    cs.record_telegram_user_activity()  # Paul just wrote on Telegram: still not a reason to push there
    assert world.tick(datetime.now()) is True
    assert world.tg.sent == [] and world.voice == [] and world.whatsapp == []
    shown = of.assistant_nudges()
    assert shown and all(n["channel"] == "in_app" for n in shown)
    assert not any(cs.ASK_TEXT in json.dumps(n) for n in all_nudges())
    assert cs.waiting_for_answer() is False and world.drawers == []


def test_telegram_in_telegram_out_exactly_once(world):
    used = cs.place([nudge()], cs.decide(incoming="telegram"), STD_ON)
    assert used == "telegram" and len(world.tg.sent) == 1
    assert world.voice == [] and world.whatsapp == []
    assert of.assistant_nudges() == []  # not shown a second time in the app


def test_spoken_in_spoken_out(world):
    used = cs.place([nudge()], cs.decide(incoming="ptt"), STD_ON)
    assert used == "spoken" and len(world.voice) == 1
    assert world.tg.sent == [] and world.whatsapp == []


def test_failed_same_channel_never_switches_channel(world):
    world.tg.accept = False
    used = cs.place([nudge()], cs.decide(incoming="telegram"), STD_ON)
    assert used == "telegram"
    assert world.voice == [] and world.whatsapp == []  # no second channel
    stored = all_nudges()[-1]
    assert stored["channel"] == "telegram" and stored["undelivered"] is True and "nothing was sent" in stored["delivery_note"]
    assert of.assistant_nudges()[0]["undelivered"] is True  # visible, marked as not delivered
    events = [e["event"] for e in audit(world.tmp)]
    assert "assistant_channel_not_delivered" in events and "assistant_channel_fallback" not in events


def test_unknown_channel_sends_nothing_and_is_visible(world):
    used = cs.place([nudge()], cs.decide(incoming="sms"), STD_ON)
    assert used == "unknown"
    assert world.tg.sent == [] and world.voice == [] and world.whatsapp == []
    assert of.assistant_nudges()[0]["undelivered"] is True


def test_old_open_question_can_be_closed_and_releases_into_the_app(world):
    state = of._load_state()
    cur = cs.channel_state(state)
    cur["pending_ask"] = {"id": "abcdefabcdef", "asked_on": "telegram", "created_ts": datetime.now().timestamp()}
    state[cs.STATE_KEY] = cur
    held = {**nudge(), "channel": "telegram", "held_for": "abcdefabcdef"}
    state["assistant"] = {"nudges": [held]}
    of._save_state(state)
    res = cs.answer("abcdefabcdef", "telegram", STD_ON)
    assert res["ok"] and res["channel"] == "in_app" and res["released"] == 1
    assert world.tg.sent == [] and world.drawers == []
    assert of.assistant_nudges()[0]["channel"] == "in_app"
    assert cs.answer("abcdefabcdef", "telegram", STD_ON)["ok"] is False


def test_setting_off_or_quiet_sends_nothing(world):
    for settings in ({**STD_ON, "initiative_recommendations": False}, {**STD_ON, "initiative_level": "observe"}):
        assert of.maybe_send_recommendations(settings, now=datetime.now() + timedelta(hours=5)) is False
    assert world.tg.sent == [] and world.voice == [] and world.whatsapp == []


def test_own_client_update_channel_is_untouched():
    assert of.own_channel_choice({"initiative_own_channel": "whatsapp"}) == "whatsapp"
    assert of.own_channel_choice({}) == "log"
