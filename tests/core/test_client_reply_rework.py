"""Rework, cancel/supersede, and contact-name behaviour for client-reply Telegram cards.

Uses a fake Telegram transport only. Nothing is sent to a real client or bot.
"""

from types import SimpleNamespace

import pytest

import distr.core.initiative.outcome_flow as of
from distr.core.incoming.client_reply_rework import compose_rework_text, literal_rework_text


class FakeTelegram:
    def __init__(self):
        self.sent = []
        self.edited = []
        self.last_telegram_message_id = 0
        self.last_telegram_chat_id = 1

    def send_to_telegram(self, text, reply_markup=None, interaction_token=None, **_kwargs):
        self.last_telegram_message_id = len(self.sent) + 1
        self.sent.append({
            "text": text,
            "reply_markup": reply_markup,
            "interaction_token": interaction_token,
            "message_id": self.last_telegram_message_id,
        })
        return True

    def edit_telegram_message(self, text, *, message_id, chat_id=None, reply_markup=None):
        self.edited.append({
            "text": text,
            "message_id": message_id,
            "chat_id": chat_id,
            "reply_markup": reply_markup,
        })
        return True


@pytest.fixture
def env(monkeypatch, tmp_path):
    monkeypatch.setenv("DECISIONS_DB_DIR", str(tmp_path))
    tg = FakeTelegram()
    monkeypatch.setattr(of, "_telegram_manager", lambda: tg)
    monkeypatch.setattr(of, "telegram_allowed", lambda settings=None: True)
    monkeypatch.setattr(of, "load_settings", lambda: {"initiative_allow_telegram": True})
    monkeypatch.setattr(of, "audit", lambda *a, **k: None)
    return SimpleNamespace(tg=tg, tmp=tmp_path, mp=monkeypatch)


def _labels(markup):
    return [b["text"] for row in (markup or {}).get("inline_keyboard", []) for b in row]


def _draft(text="I've pushed the v3 field-parity change and it's live on devapi.", **target):
    base = {"channel": "whatsapp", "recipient_names": ["Sibo"], "jid": "111@lid"}
    base.update(target)
    return {"id": "d1", "text": text, "target": base, "response_id": 9, "source_key": "whatsapp:111@lid"}


def test_literal_rework_text_extracts_exact_wording():
    assert literal_rework_text(
        'Rather update the message to say Sibo, I\'ve pushed the change and it\'s live on devapi.'
    ) == "Sibo, I've pushed the change and it's live on devapi."
    assert literal_rework_text('say: Sibo, the deploy is live on devapi.') == "Sibo, the deploy is live on devapi."
    assert literal_rework_text('Change the name to "Sibo" and mention the deploy') is None
    assert literal_rework_text("Don't call him Breakfast") is None
    verbatim = "Sibo, I've pushed the v3 field-parity change to api-v3 main and it's live on devapi."
    assert literal_rework_text(verbatim) == verbatim
    assert compose_rework_text(
        "Good afternoon Breakfast, I think I can't access the code.",
        'Update the message to say: Sibo, I\'ve pushed the change and it\'s live on devapi.',
    ) == "Sibo, I've pushed the change and it's live on devapi."


def test_compose_rework_uses_model_when_instruction_is_a_note():
    def complete(_provider, _model, prompt, _settings):
        assert "Previous draft:" in prompt[1]["content"]
        assert "Mention the deploy" in prompt[1]["content"]
        return "Good afternoon Sibo, we've pushed the change and it's live."

    out = compose_rework_text(
        "Sibo, I'm still looking at this.",
        "Mention the deploy and don't use we.",
        complete=complete,
    )
    assert out == "Sibo, I've pushed the change and it's live."
    assert "Good afternoon" not in out
    assert "we" not in out.lower()


def test_card_uses_contact_display_name_not_whatsapp_label(env):
    body = "Sibo, I've pushed the change and it's live on devapi."
    key = of.hold_thread_reply(42, _draft(body), answering="field parity on v3", label="Breakfast")
    assert key
    card = env.tg.sent[0]
    assert "Ready to send to Sibo (WhatsApp chat)" in card["text"]
    assert "Breakfast (WhatsApp chat)" not in card["text"]
    assert body in card["text"]
    assert _labels(card["reply_markup"]) == ["Send message to client", "Rework", "Not yet"]
    # Groups still use the group title.
    group = _draft(body, recipient_names=[], jid="1203630@g.us")
    of.hold_thread_reply(43, {**group, "id": "d-group"}, answering="v3", label="Snuza Dev")
    assert "Snuza Dev (WhatsApp group)" in env.tg.sent[1]["text"]


def test_rework_prompt_supersedes_old_card_and_sends_new_card(env):
    cancelled = []

    def act_on_draft(chat_id, draft_id, action, text="", sender=None):
        cancelled.append((chat_id, draft_id, action, text))
        return {"status": "cancelled" if action == "cancel" else "sent"}

    def create_draft(chat_id, response_id, source_key, **kwargs):
        assert kwargs["replaces"] == "d1"
        assert kwargs["preset_text"] == "Sibo, I've pushed the change and it's live on devapi."
        new = {
            "id": "d2",
            "text": kwargs["preset_text"],
            "target": {"channel": "whatsapp", "recipient_names": ["Sibo"], "jid": "111@lid"},
            "response_id": response_id,
            "source_key": source_key,
            "status": "draft",
        }
        of.hold_thread_reply(chat_id, new, answering="field parity on v3", label="Breakfast")
        return new

    env.mp.setattr("distr.core.incoming.source_reply.act_on_draft", act_on_draft)
    env.mp.setattr("distr.core.incoming.source_reply.create_draft", create_draft)

    body = "Breakfast, I can't access the code."
    key = of.hold_thread_reply(42, _draft(body), answering="field parity on v3", label="Breakfast")
    first_id = env.tg.sent[0]["message_id"]
    assert of.get_flow(key)["telegram_message_ids"] == [first_id]

    prompt = of.handle_telegram_text(f"oc:{key}:rework", chat_id=1, telegram_message_id=first_id)
    assert prompt["text"] == "What should I change?"
    assert prompt["reply_markup"] == {"force_reply": True, "selective": True}
    assert env.tg.edited and env.tg.edited[0]["text"] == of.CANCELLED_REPLACED_TEXT
    assert env.tg.edited[0]["message_id"] == first_id
    assert env.tg.edited[0]["reply_markup"] == {"inline_keyboard": []}
    assert of.get_flow(key)["status"] == "awaiting_rework"
    # The old draft cannot be sent while a rework is in progress.
    blocked = of.handle_telegram_text(f"oc:{key}:confirm", chat_id=1)
    assert blocked["text"] == "Couldn't send: waiting for what to change."
    assert cancelled == []

    result = of.handle_telegram_text(
        "Update the message to say: Sibo, I've pushed the change and it's live on devapi.",
        chat_id=1,
    )
    assert result == {"suppress": True}
    assert of.get_flow(key)["status"] == "superseded"
    # Tapping the old card after the new draft exists still does not send.
    assert of.handle_telegram_text(f"oc:{key}:confirm", chat_id=1)["text"] == of.CANCELLED_REPLACED_TEXT
    assert cancelled == []

    new_card = env.tg.sent[-1]
    assert "Ready to send to Sibo (WhatsApp chat)" in new_card["text"]
    assert "Sibo, I've pushed the change and it's live on devapi." in new_card["text"]
    assert _labels(new_card["reply_markup"]) == ["Send message to client", "Rework", "Not yet"]
    new_key = new_card["reply_markup"]["inline_keyboard"][0][0]["callback_data"].split(":")[1]
    assert new_key != key
    assert of.get_flow(new_key)["status"] == "awaiting_decision"
    assert of.get_flow(new_key)["draft_id"] == "d2"


def test_cancel_edits_card_to_superseded_and_blocks_send(env):
    cancelled = []

    def act_on_draft(chat_id, draft_id, action, text="", sender=None):
        cancelled.append((action, draft_id))
        return {"status": "cancelled"}

    env.mp.setattr("distr.core.incoming.source_reply.act_on_draft", act_on_draft)
    key = of.hold_thread_reply(42, _draft(), answering="v3", label="Breakfast")
    mid = env.tg.sent[0]["message_id"]
    out = of.handle_telegram_text(f"oc:{key}:cancel", chat_id=1, telegram_message_id=mid)
    assert out["text"] == of.CANCELLED_REPLACED_TEXT
    assert out.get("suppress") is True
    assert cancelled == [("cancel", "d1")]
    assert of.get_flow(key)["status"] == "cancelled"
    assert env.tg.edited[0]["text"] == of.CANCELLED_REPLACED_TEXT
    assert env.tg.edited[0]["message_id"] == mid
    assert env.tg.edited[0]["reply_markup"] == {"inline_keyboard": []}
    dead = of.handle_telegram_text(f"oc:{key}:send", chat_id=1, telegram_message_id=mid)
    assert dead["text"] == of.CANCELLED_REPLACED_TEXT
    assert of.handle_telegram_text(f"oc:{key}:confirm", chat_id=1, telegram_message_id=mid)["text"] == of.CANCELLED_REPLACED_TEXT


def test_yes_send_it_is_still_the_only_client_send(env):
    sent = []

    def act_on_draft(chat_id, draft_id, action, text="", sender=None):
        sent.append((action, text))
        return {"status": "sent"}

    env.mp.setattr("distr.core.incoming.source_reply.act_on_draft", act_on_draft)
    body = "Sibo, I've pushed the change and it's live on devapi."
    key = of.hold_thread_reply(42, _draft(body), answering="v3", label="Breakfast")
    assert sent == []
    of.handle_telegram_text(f"oc:{key}:rework", chat_id=1)
    assert sent == []
    of.handle_telegram_text(f"oc:{key}:send", chat_id=1)
    assert sent == []  # rework already moved the flow to awaiting_rework
    # Fresh card: send then confirm is the only path that delivers.
    key = of.hold_thread_reply(99, {**_draft(body), "id": "d-fresh"}, answering="v3", label="Breakfast")
    of.handle_telegram_text(f"oc:{key}:send", chat_id=1)
    assert sent == []
    done = of.handle_telegram_text(f"oc:{key}:confirm", chat_id=1)
    assert done["text"] == "Sent to Sibo on WhatsApp."
    assert sent == [("approve", body)]


def test_leading_greeting_becomes_name_first_opener():
    from distr.core.client_voice import apply_client_voice, strip_leading_client_greeting

    exact = (
        "Sibo, I've pushed the v3 field-parity change to api-v3 main and it's live on devapi. "
        "The device endpoints now return ble_address, feature_bits and the product type id, name and variant, "
        "plus is_primary and shared_users. Shout if anything's still missing."
    )
    assert apply_client_voice(exact) == exact
    assert strip_leading_client_greeting("Good afternoon, Sibo,\n\nI compared the v3 fields.") == "Sibo, I compared the v3 fields."
    assert apply_client_voice("Good afternoon, Sibo,\n\nWe've compared the v3 fields.") == "Sibo, I've compared the v3 fields."
    assert apply_client_voice("Hi Sibo, hope you're well. I've pushed the change.") == "Sibo, I've pushed the change."
    assert apply_client_voice("Hi, I've pushed the change.") == "I've pushed the change."


def test_exact_preset_text_skips_the_model(monkeypatch):
    from distr.core.incoming import source_reply

    exact = "Sibo, I've pushed the v3 field-parity change to api-v3 main and it's live on devapi."

    def complete(*_a, **_k):
        raise AssertionError("the model must not run when exact text is supplied")

    text, route = source_reply.resolve_draft_text(preset_text=exact, complete=complete)
    assert text == exact and route == {"provider": "owner", "model": "preset"}
    text, _route = source_reply.resolve_draft_text(
        preset_text="Good afternoon, Sibo,\n\nI've pushed the change.", complete=complete,
    )
    assert text == "Sibo, I've pushed the change."

    called = []

    def fake_generate(*_a, **_k):
        called.append(1)
        return "generated", {"provider": "m", "model": "m"}

    monkeypatch.setattr(source_reply, "generate_draft", fake_generate)
    text, _route = source_reply.resolve_draft_text(preset_text="  ", complete=complete)
    assert called == [1] and text == "generated"


def test_source_reply_route_forwards_preset_text():
    from inspect import getsource
    from distr.gui.web.routes.development import thread_tools

    src = getsource(thread_tools.register_routes)
    assert "preset_text" in src
    assert "body.get('preset_text', body.get('text'))" in src


def test_confirm_card_token_is_not_blocked_as_a_workflow_checkpoint(monkeypatch):
    from tests.telegram.test_telegram_delivery_stability import DummyTelegramSender

    sender = DummyTelegramSender()
    sender.telegram_user_id = 123
    authorized = []

    def forbid(**kwargs):
        authorized.append(kwargs)
        raise AssertionError("confirm cards must not go through checkpoint authorization")

    monkeypatch.setattr(
        "distr.core.workflow.interactions.authorize_telegram_checkpoint_delivery", forbid
    )
    markup = {"inline_keyboard": [[{"text": "Rework", "callback_data": "oc:c4da6bc1cdd9:rework"}]]}
    assert sender.send_to_telegram(
        text="Ready to send to Sibo (WhatsApp chat):",
        reply_markup=markup,
        interaction_token="oc-card:c4da6bc1cdd9",
    )
    sender._process_message_queue()
    assert authorized == []
    assert len(sender.websocket_attempts) == 1
    sent = sender.websocket_attempts[0]
    assert sent["client_message_id"] == "oc-card:c4da6bc1cdd9"
    assert sent["text"].startswith("Ready to send to Sibo")
    assert sent["reply_markup"] == markup


def test_yes_send_it_on_live_card_uses_approved_whatsapp_path(env):
    """Yes, send it on a live card (even still awaiting_decision) goes through act_on_draft approve."""
    whatsapp = []

    def act_on_draft(chat_id, draft_id, action, text="", sender=None):
        assert action == "approve"
        whatsapp.append({"jid": "111@lid", "text": text, "dry_run": False, "success": True})
        return {"status": "sent"}

    env.mp.setattr("distr.core.incoming.source_reply.act_on_draft", act_on_draft)
    body = "Sibo, I've pushed the change and it's live on devapi."
    key = of.hold_thread_reply(42, _draft(body), answering="v3", label="Breakfast")
    mid = env.tg.sent[0]["message_id"]
    # Owner tapped Yes on the original live card without the intermediate prompt.
    done = of.handle_telegram_text(f"oc:{key}:confirm", chat_id=1, telegram_message_id=mid)
    assert whatsapp == [{"jid": "111@lid", "text": body, "dry_run": False, "success": True}]
    assert done["text"] == "Sent to Sibo on WhatsApp."
    assert done.get("suppress") is True
    assert of.get_flow(key)["status"] == "sent"
    edited = [e for e in env.tg.edited if e["message_id"] == mid]
    assert edited[-1]["text"] == "Sent to Sibo on WhatsApp."
    assert edited[-1]["reply_markup"] == {"inline_keyboard": []}


def test_yes_send_it_dry_run_keeps_card_live_with_plain_reason(env):
    from distr.core.incoming.source_reply import ReplyPreflightError

    attempts = []

    def act_on_draft(chat_id, draft_id, action, text="", sender=None):
        attempts.append((action, text))
        raise ReplyPreflightError(
            "Nothing was sent. WhatsApp dry-run mode is on. Turn it off before approving again."
        )

    env.mp.setattr("distr.core.incoming.source_reply.act_on_draft", act_on_draft)
    body = "Sibo, I've pushed the change and it's live on devapi."
    key = of.hold_thread_reply(42, _draft(body), answering="v3", label="Breakfast")
    mid = env.tg.sent[0]["message_id"]
    of.handle_telegram_text(f"oc:{key}:send", chat_id=1, telegram_message_id=mid)
    failed = of.handle_telegram_text(f"oc:{key}:confirm", chat_id=1, telegram_message_id=mid)
    assert failed["text"] == "Couldn't send: WhatsApp dry-run mode is on. Turn it off before approving again."
    assert "Nothing was sent: Nothing was sent" not in failed["text"]
    assert _labels(failed["reply_markup"]) == ["Yes, send it", "Rework", "Not yet"]
    assert of.get_flow(key)["status"] == "confirming"
    assert attempts == [("approve", body)]
    # Card stays live so the owner can retap Yes after the block is cleared.
    attempts.clear()
    again = of.handle_telegram_text(f"oc:{key}:confirm", chat_id=1, telegram_message_id=mid)
    assert attempts == [("approve", body)]
    assert again["text"].startswith("Couldn't send:")
    assert of.get_flow(key)["status"] == "confirming"


def test_stale_cards_lose_buttons_and_dead_taps_are_plain(env):
    body = "Sibo, I've pushed the change and it's live on devapi."
    key = of.hold_thread_reply(42, _draft(body), answering="v3", label="Breakfast")
    first = env.tg.sent[0]["message_id"]
    of.record_card_message(key, 11, 1)
    of.record_card_message(key, 12, 1)
    of.handle_telegram_text(f"oc:{key}:send", chat_id=1, telegram_message_id=first)
    # Older copies of this card are retired when a newer reply is shown.
    retired = [e for e in env.tg.edited if e["message_id"] in (11, 12)]
    assert retired
    assert all(e["text"] == of.CANCELLED_REPLACED_TEXT for e in retired)
    assert all(e["reply_markup"] == {"inline_keyboard": []} for e in retired)

    of.handle_telegram_text(f"oc:{key}:cancel", chat_id=1, telegram_message_id=first)
    dead = of.handle_telegram_text(f"oc:{key}:send", chat_id=1, telegram_message_id=first)
    assert dead["text"] == of.CANCELLED_REPLACED_TEXT
    assert dead.get("suppress") is True
    last = [e for e in env.tg.edited if e["message_id"] == first][-1]
    assert last["text"] == of.CANCELLED_REPLACED_TEXT
    assert last["reply_markup"] == {"inline_keyboard": []}
