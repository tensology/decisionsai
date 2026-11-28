"""Replay of the 28 Nov Rework conversation, plus the rules behind it.

Fake Telegram transport, fake model and a fake draft store. Nothing is sent to
Telegram, WhatsApp or a client.
"""

import json
import time
from types import SimpleNamespace

import pytest

import distr.core.initiative.outcome_flow as of
import distr.core.incoming.telegram_reply_intent as intent

YOCO = (
    "Caleb, Yoco support (Iesa) is tracing the stuck C6828W payment (R1,300.46, 26 Nov) and needs the "
    "Yoco account email and the account holder's ID number before he'll help. "
    "Can you reply to his email on Monday with those, please?"
)
NEWER = ["Caleb: seems like something went wrong with the reply to yoco email, can you have a look when you have a chance please"]
MERGED = "Caleb, I'll have a look at the Yoco email reply when I have a chance. " + YOCO[len("Caleb, "):]


class FakeTelegram:
    def __init__(self):
        self.sent, self.edited, self.typing = [], [], 0
        self.last_telegram_message_id, self.last_telegram_chat_id = 0, 1

    def send_to_telegram(self, text, reply_markup=None, interaction_token=None, **_kw):
        self.last_telegram_message_id = len(self.sent) + 100
        self.sent.append({"text": text, "reply_markup": reply_markup, "id": self.last_telegram_message_id})
        return True

    def edit_telegram_message(self, text, *, message_id, chat_id=None, reply_markup=None):
        self.edited.append({"text": text, "id": message_id})
        return True

    def _start_typing_loop(self, action="typing"):
        self.typing += 1

    def _stop_typing_loop(self):
        pass


class Env(SimpleNamespace):
    def card_ids(self):
        return [m["id"] for m in self.tg.sent]


def _labels(markup):
    return [b["text"] for row in (markup or {}).get("inline_keyboard", []) for b in row]


@pytest.fixture
def env(monkeypatch, tmp_path):
    monkeypatch.setenv("DECISIONS_DB_DIR", str(tmp_path))
    tg = FakeTelegram()
    monkeypatch.setattr(of, "_telegram_manager", lambda: tg)
    monkeypatch.setattr(of, "telegram_allowed", lambda settings=None: True)
    monkeypatch.setattr(of, "load_settings", lambda: {"initiative_allow_telegram": True})
    monkeypatch.setattr(of, "audit", lambda *a, **k: audits.append((a, k)))
    monkeypatch.setattr(of, "_run_in_background", lambda fn: fn())
    monkeypatch.setattr(of, "newer_client_messages", lambda flow, limit=3: list(NEWER))
    audits.clear()
    handed_on, actions, models = [], [], []

    def model(messages, settings, **_kw):
        models.append(messages)
        user = json.loads(messages[-1]["content"]) if messages[0]["content"].startswith("You read one") else None
        if user is not None:  # intent call
            return verdicts.pop(0), "test/strong"
        return composed.pop(0), "test/strong"

    verdicts, composed = [], []
    monkeypatch.setattr(intent, "complete_strong", model)
    import distr.core.incoming.client_reply_rework as rework
    monkeypatch.setattr(rework, "load_settings_from_db", lambda: {}, raising=False)
    monkeypatch.setattr("distr.core.settings.load_settings_from_db", lambda: {})

    def create_draft(chat_id, response_id, source_key, **kw):
        new = {"id": f"d{len(tg.sent)}", "text": kw["preset_text"], "response_id": response_id, "source_key": source_key,
               "target": {"channel": "whatsapp", "recipient_names": ["Caleb"], "jid": "g@g.us"}}
        of.hold_thread_reply(chat_id, new, answering="Yoco payment", label="MP_WebDev")
        return new

    def act_on_draft(chat_id, draft_id, action, text="", sender=None):
        actions.append(action)
        return {"status": "cancelled" if action == "cancel" else "sent"}

    monkeypatch.setattr("distr.core.incoming.source_reply.create_draft", create_draft)
    monkeypatch.setattr("distr.core.incoming.source_reply.act_on_draft", act_on_draft)

    class Bus:
        def deliver_telegram_user_input(self, **kw):
            handed_on.append(kw)
            return True

    monkeypatch.setattr("distr.core.integrations.bus.get_integration_message_bus", lambda: Bus())
    monkeypatch.setattr("distr.core.tensology_client.handle_proposal_decision_reply", lambda *a, **k: None)
    return Env(tg=tg, mp=monkeypatch, handed_on=handed_on, actions=actions, models=models,
               verdicts=verdicts, composed=composed)


audits: list = []


def _verdict(intent_name, **kw):
    return json.dumps({"intent": intent_name, "instruction": "", "message": "", "question": "", **kw})


def _hold(env):
    old = {"id": "d0", "text": YOCO, "response_id": 9, "source_key": "whatsapp:g@g.us",
           "target": {"channel": "whatsapp", "recipient_names": ["Caleb"], "jid": "g@g.us"}}
    key = of.hold_thread_reply(42, old, answering="Yoco payment", label="MP_WebDev")
    first = env.tg.sent[0]["id"]
    prompt = of.handle_telegram_text(f"oc:{key}:rework", chat_id=1, telegram_message_id=first)
    assert prompt["text"] == "What should I change?"
    return key


def _live_cards(env):
    """Cards that still show buttons: sent with a keyboard and never edited to a plain notice."""
    edited = {e["id"] for e in env.tg.edited}
    return [m for m in env.tg.sent if _labels(m["reply_markup"]) and m["id"] not in edited]


def test_replay_spoken_note_for_the_newer_client_message_lands_in_the_reply_once(env):
    key = _hold(env)
    env.verdicts.append(_verdict("edit_draft", instruction="Say I'll have a look at the Yoco email reply when I get a chance."))
    env.composed.append(MERGED)
    # The relay can deliver the same note twice; it must be applied once.
    claimed = of.handle_pending_rework("so tell him I'll have a look at the yoko email reply when I get a chance", chat_id=1,
                                       telegram_message_id="19385", source="voice")
    assert claimed == {"suppress": True}
    assert of.handle_pending_rework("so tell him I'll have a look at the yoko email reply when I get a chance", chat_id=1,
                                    telegram_message_id="19385", source="voice") == {"suppress": True}
    new_cards = [m for m in env.tg.sent if "Ready to send" in m["text"]]
    assert len(new_cards) == 2  # the original card and exactly one reworked card
    assert MERGED in new_cards[-1]["text"]
    assert _labels(new_cards[-1]["reply_markup"]) == ["Send message to client", "Rework", "Not yet"]
    assert of.get_flow(key)["status"] == "superseded"
    # The model saw the draft and the client's newer message, so "have a look" is not orphaned.
    intent_call = json.loads(env.models[0][-1]["content"])
    assert intent_call["client_messages_newer_than_the_draft_was_written"] == NEWER
    assert "Newer client messages" in env.models[1][-1]["content"]
    # Never sent to the client.
    assert env.actions == []
    assert len(_live_cards(env)) == 1  # only the newest card keeps its buttons


def test_rework_tap_keeps_the_draft_readable_and_repeat_edits_are_skipped(env):
    key = _hold(env)
    assert env.tg.edited[0]["text"].startswith("Reworking the message to Caleb")
    assert YOCO in env.tg.edited[0]["text"]
    before = len(env.tg.edited)
    of._edit_flow_cards(key, text=of.CANCELLED_REPLACED_TEXT)
    of._edit_flow_cards(key, text=of.CANCELLED_REPLACED_TEXT)  # second identical edit would 400 on Telegram
    same = [e for e in env.tg.edited[before:] if e["text"] == of.CANCELLED_REPLACED_TEXT]
    assert len(same) == len(of.get_flow(key)["telegram_message_ids"])


def test_unrelated_spoken_note_is_left_for_normal_routing_and_the_draft_waits(env):
    key = _hold(env)
    env.verdicts.append(_verdict("other_topic"))
    note = "what is the status of the Player1Sport tickets"
    assert of.handle_pending_rework(note, chat_id=1, telegram_message_id="19390", source="voice") == {"suppress": True}
    assert env.handed_on and env.handed_on[0]["text"] == note and env.handed_on[0]["input_type"] == "voice"
    assert of.get_flow(key)["status"] == "awaiting_rework"
    assert not any("Ready to send" in m["text"] for m in env.tg.sent[1:])
    # A repeat delivery is swallowed, so the agent never answers the same note twice.
    assert of.handle_pending_rework(note, chat_id=1, telegram_message_id="19390", source="voice") == {"suppress": True}
    assert len(env.handed_on) == 1


def test_unclear_note_gets_one_short_question_and_changes_nothing(env):
    key = _hold(env)
    env.verdicts.append(_verdict("unclear", question="Is that for the Yoco payment draft or Caleb's newer message?"))
    of.handle_pending_rework("uh the thing about the, yeah", chat_id=1, telegram_message_id="1", source="voice")
    assert env.tg.sent[-1]["text"] == "Is that for the Yoco payment draft or Caleb's newer message?"
    assert of.get_flow(key)["status"] == "awaiting_rework"
    assert env.actions == []


def test_spoken_cancel_cancels_the_draft_and_send_is_never_done_from_a_message(env):
    key = _hold(env)
    env.verdicts.append(_verdict("send"))
    of.handle_pending_rework("yes send it", chat_id=1, telegram_message_id="5", source="voice")
    assert "Tap Send message to client" in env.tg.sent[-1]["text"]
    assert "approve" not in env.actions and of.get_flow(key)["status"] == "awaiting_rework"
    env.verdicts.append(_verdict("cancel"))
    of.handle_pending_rework("actually never mind leave it", chat_id=1, telegram_message_id="6", source="voice")
    assert of.get_flow(key)["status"] == "cancelled"
    assert "approve" not in env.actions


def test_dictated_replacement_is_used_verbatim(env):
    _hold(env)
    env.verdicts.append(_verdict("dictated_message", message="Caleb, I'll look at the Yoco email reply today."))
    of.handle_pending_rework("say Caleb I'll look at the Yoco email reply today", chat_id=1, telegram_message_id="7")
    assert "Caleb, I'll look at the Yoco email reply today." in env.tg.sent[-1]["text"]
    assert len(env.models) == 1  # no second model call for a verbatim message


def test_work_runs_off_the_ui_thread(env):
    key = _hold(env)
    queued = []
    env.mp.setattr(of, "_run_in_background", queued.append)
    t0 = time.monotonic()
    assert of.handle_pending_rework("add please", chat_id=1, telegram_message_id="8") == {"suppress": True}
    assert time.monotonic() - t0 < 0.5 and env.models == []  # nothing ran on the caller's thread
    assert len(queued) == 1 and of.get_flow(key)["status"] == "awaiting_rework"
    assert env.tg.typing >= 1


def test_expired_rework_prompt_does_not_swallow_later_messages(env):
    key = _hold(env)
    assert of.latest_awaiting_rework()
    state = of._load_state()
    state["flows"][key]["updated"] = time.time() - of.REWORK_TTL_S - 5
    of._state_path().write_text(json.dumps(state))
    assert of.handle_pending_rework("check the Player1Sport board", chat_id=1, telegram_message_id="9") is None


def test_pending_rework_survives_a_restart_and_buttons_are_not_claimed(env):
    key = _hold(env)
    # State lives in the JSON file, so a fresh process reads the same pending rework.
    assert of.latest_awaiting_rework()[0] == key
    assert of.handle_pending_rework(f"oc:{key}:send", chat_id=1) is None
    assert of.handle_pending_rework("wf:abc123:approve", chat_id=1) is None


def test_model_down_falls_back_to_rules_and_still_reworks(env):
    key = _hold(env)

    def down(messages, settings, **_kw):
        raise RuntimeError("provider down")

    env.mp.setattr(intent, "complete_strong", down)
    of.handle_pending_rework("add please at the end", chat_id=1, telegram_message_id="10")
    assert of.get_flow(key)["status"] == "superseded"
    assert env.tg.sent[-1]["text"].count("please") >= 1


def test_strong_model_order_prefers_grok_then_falls_back_to_the_chat_model():
    settings = {"openrouter_enabled": 1, "openrouter_key": "x", "conversational_llm_provider": "openrouter",
                "conversational_llm_model": "deepseek/deepseek-v4.1-flash"}
    models = [m for _p, m in intent.strong_model_candidates(settings)]
    assert models[0] == "x-ai/grok-4.7" and models[-1] == "deepseek/deepseek-v4.1-flash"
    # The Settings orchestrator model wins when set.
    models = [m for _p, m in intent.strong_model_candidates({**settings, "orchestrator_provider": "openrouter",
                                                             "orchestrator_model": "anthropic/claude-opus-5.5"})]
    assert models[0] == "anthropic/claude-opus-5.5"


def test_complete_strong_skips_a_slow_or_failing_model():
    calls = []

    def run(provider, model, messages, settings):
        calls.append(model)
        if model == "a":
            time.sleep(0.3)
            return "late"
        if model == "b":
            raise RuntimeError("boom")
        return "ok"

    text, used = intent.complete_strong([], {}, complete=run, timeout=0.05,
                                        candidates=[("p", "a"), ("p", "b"), ("p", "c")])
    assert (text, used, calls) == ("ok", "p/c", ["a", "b", "c"])
    with pytest.raises(RuntimeError):
        intent.complete_strong([], {}, complete=run, timeout=0.05, candidates=[("p", "b")])


def test_rules_never_guess_about_other_work_or_long_unclear_talk():
    assert intent.heuristic_intent("cancel that")["intent"] == "cancel"
    assert intent.heuristic_intent("send it")["intent"] == "send"
    assert intent.heuristic_intent("Fix the login on Snuza", other_work=True)["intent"] == "other_topic"
    assert intent.heuristic_intent("add Monday")["intent"] == "edit_draft"
    assert intent.heuristic_intent("word " * 60)["intent"] == "unclear"
