"""Same-channel client replies (Paul, 12 Nov 2026 15:16 and 15:19 SAST).

WhatsApp in, WhatsApp out; Gmail in, Gmail out; Mailshot in, Mailshot out; Telegram in,
Telegram out. Proposals too: once the thread work is done, the reply goes back to the client
on the channel the proposal arrived on. No "best"/"preferred" channel, no silent switch.
Every send is mocked; nothing leaves the machine.
"""
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from distr.core.db import Chat, SourceReplyDraft, get_session
from distr.core.incoming import source_reply as replies
from distr.core.workflow import reply_channel as rc


# ------------------------------------------------------------------ ticket / workflow replies

@pytest.mark.parametrize("provider, extra, channel", [
    ("whatsapp", {"source_thread_id": "27820000000@s.whatsapp.net"}, "whatsapp"),
    ("gmail", {"source_thread_id": "t1", "source_contact": "client@example.com"}, "email"),
    ("mailshot", {"source_thread_id": "t2", "source_contact": "client@example.com"}, "mailshot"),
    ("telegram", {"source_thread_id": "-1001234"}, "telegram"),
    ("jira", {"source_external_id": "ABC-1", "source_url": "https://x.atlassian.net/browse/ABC-1"}, "jira"),
])
def test_ticket_reply_target_is_the_incoming_channel(provider, extra, channel):
    target = rc.resolve_reply_target({"source_provider": provider, **extra})
    assert target.channel == channel and target.can_send


@pytest.mark.parametrize("provider", ["", None, "slack", "sms", "carrier pigeon"])
def test_unknown_or_missing_channel_is_never_relabelled_as_email(provider):
    target = rc.resolve_reply_target({"source_provider": provider, "source_thread_id": "x",
                                      "source_contact": "client@example.com"})
    assert target.channel == "unknown" and not target.can_send
    assert "Draft the reply" in rc.build_reply_instruction({"source_provider": provider, "source_thread_id": "x"})


def test_outlook_is_not_answered_from_gmail():
    target = rc.resolve_reply_target({"source_provider": "outlook", "source_thread_id": "t",
                                      "source_contact": "client@example.com"})
    assert not target.can_send and "outlook_reply_not_supported" in target.missing


@pytest.mark.parametrize("source, thread, channel", [
    ("whatsapp", "27820000000@s.whatsapp.net", "whatsapp"),
    ("telegram", "-100777", "telegram"),
    ("gmail", "thread-9", "email"),
    ("mailshot", "ms-9", "mailshot"),
])
def test_proposal_born_ticket_replies_on_the_proposal_channel(source, thread, channel):
    ticket = SimpleNamespace(source_provider="incoming_proposal",
                             source_external_id=f"local:{source}:{thread}:abcdef1234567890",
                             source_contact="client@example.com", source_url="", source_chat_id=None)
    target = rc.resolve_reply_target(ticket)
    assert target.channel == channel and target.thread_id == thread


def test_multi_channel_person_each_reply_follows_its_own_message():
    wa = rc.resolve_reply_target({"source_provider": "whatsapp", "source_thread_id": "27820000000@s.whatsapp.net",
                                  "source_contact": "anna@example.com"})
    mail = rc.resolve_reply_target({"source_provider": "gmail", "source_thread_id": "t1",
                                    "source_contact": "anna@example.com"})
    assert (wa.channel, mail.channel) == ("whatsapp", "email")


# ------------------------------------------------------------------ proposal Message Client

def test_proposal_sources_keep_their_own_channels_including_telegram():
    proposal = {"source": "whatsapp", "source_scope": [
        {"source": "whatsapp", "source_thread_id": "g@g.us", "message_ids": ["w1"]},
        {"source": "telegram", "source_thread_id": "-100777", "message_ids": ["55"]},
        {"source": "gmail", "source_thread_id": "gt", "message_ids": ["m1"]},
        {"source": "mailshot", "source_thread_id": "mt", "message_ids": ["s1"]},
    ]}
    keys = {c["key"]: c["channel"] for c in replies.source_choices(proposal)}
    assert keys == {"whatsapp:g@g.us": "whatsapp", "telegram:-100777": "telegram",
                    "gmail:gt": "gmail", "mailshot:mt": "mailshot"}
    assert replies._valid_source_key("telegram:-100777") == "telegram:-100777"


def test_telegram_reply_goes_only_to_the_original_linked_group(monkeypatch):
    calls = []

    def fake_send(**kw):
        calls.append(kw)
        return {"success": True, "chat_id": kw["target"]}

    monkeypatch.setattr("distr.core.workflow.reply_delivery._send_telegram", fake_send)
    target = {"channel": "telegram", "thread_id": "-100777", "project_id": 9, "label": "Shop team"}
    assert replies.send_reply(target, "Hi Anna, it's fixed.", "draft-1")["success"] is True
    assert calls == [{"target": "-100777", "body": "Hi Anna, it's fixed.", "project_id": 9, "key": "draft-1"}]


@pytest.mark.parametrize("target", [
    {"channel": "telegram", "thread_id": "4242", "project_id": 9},     # a private chat (Paul's own)
    {"channel": "telegram", "thread_id": "-100777", "project_id": None},  # no project to check the link
    {"channel": "telegram", "thread_id": "", "project_id": 9},
])
def test_telegram_refusals_are_visible_and_send_nothing(monkeypatch, target):
    sender = Mock()
    monkeypatch.setattr("distr.core.workflow.reply_delivery._send_telegram", sender)
    with pytest.raises(replies.ReplyPreflightError):
        replies.send_reply(target, "text", "k")
    sender.assert_not_called()


def test_telegram_unconfirmed_send_is_not_reported_as_sent(monkeypatch):
    monkeypatch.setattr("distr.core.workflow.reply_delivery._send_telegram",
                        lambda **kw: {"success": False, "error": "Telegram did not accept the message"})
    with pytest.raises(RuntimeError):
        replies.send_reply({"channel": "telegram", "thread_id": "-100777", "project_id": 9}, "t", "k")


@pytest.mark.parametrize("refusal", [None, "unverified_relay", "unlinked_group", "changed_response", "expired_before_transport"])
def test_manual_telegram_approval_waits_for_exact_provider_receipt(monkeypatch, refusal):
    from distr.core.workflow import telegram_reply_receipts as receipts

    group = "-100777"
    sent_frames = []

    def queue(frame):
        sent_frames.append(frame)
        return True

    manager = SimpleNamespace(telegram_user_id=12345, queue_closing_reply=queue,
                              is_connected=lambda: True, _send_websocket_message=lambda frame: True)
    monkeypatch.setattr("distr.core.kanban.ticket_workflow_engagement._telegram_manager_from_app", lambda: manager)
    links = {("telegram", group): {9}}
    monkeypatch.setattr("distr.core.incoming.project_links.source_project_index", lambda: links)
    monkeypatch.setattr(replies, "resolve_target", lambda source: {"channel": "telegram", "thread_id": group, "label": "Shop team"})
    with get_session() as db:
        proposal = {"source": "telegram", "source_thread_id": group, "project_id": 9, "message_ids": ["55"]}
        root = Chat(title="Telegram receipt fixture", project_id=9, params=json.dumps({"incoming_reply_context": proposal}))
        db.add(root)
        db.flush()
        turn = Chat(parent_id=root.id, response="The checkout has been repaired.")
        db.add(turn)
        db.commit()
        thread = (root.id, turn.id)
    draft = replies.create_draft(*thread, f"telegram:{group}", complete=lambda *_: "The checkout is fixed.")
    assert replies.act_on_draft(thread[0], draft["id"], "approve", "Reviewed text.")["status"] == "sending"
    assert len(sent_frames) == 1
    frame = sent_frames[0]
    token = frame["client_message_id"]
    if refusal:
        if refusal == "unlinked_group":
            links.clear()
        elif refusal == "changed_response":
            with get_session() as db:
                db.get(Chat, thread[1]).response = "The repair has been withdrawn."
                db.commit()
        if refusal == "expired_before_transport":
            with get_session() as db:
                row, payload = receipts._match(db, token)
                payload["telegram_attempt"]["deadline"] = 0
                row.payload = json.dumps(payload)
                db.commit()
            assert replies.reply_drafts(*thread)[0]["status"] == "unconfirmed"
            assert token not in receipts.recovery_page(0, limit=100)[1]
        else:
            assert not receipts.authorize(frame, owner=12345, relay_verified=refusal != "unverified_relay")
        assert not receipts.confirm(token, telegram_chat_id=int(group), telegram_message_id=44)
        retained = replies.reply_drafts(*thread)[0]
        assert retained["status"] == "draft" and retained["text"] == "Reviewed text."
        if refusal == "changed_response":
            assert retained["rewrite_required"]
            return
        links[("telegram", group)] = {9}
        assert replies.act_on_draft(thread[0], draft["id"], "approve", "Reviewed text.")["status"] == "sending"
        frame = sent_frames[1]
        assert frame["client_message_id"] != token
        assert not receipts.authorize(sent_frames[0], owner=12345)
        token = frame["client_message_id"]
    expected_frames = 2 if refusal else 1
    assert not receipts.confirm(token, telegram_chat_id=int(group), telegram_message_id=44)
    assert receipts.authorize(frame, owner=12345)
    assert not receipts.authorize(frame, owner=12345)
    assert token in receipts.recovery_page(0)[1]
    assert not receipts.confirm(token, telegram_chat_id=-100999, telegram_message_id=44)
    assert receipts.confirm(token, uncertain=True)
    assert replies.reply_drafts(*thread)[0]["status"] == "unconfirmed"
    with pytest.raises(ValueError, match="unconfirmed"):
        replies.act_on_draft(thread[0], draft["id"], "approve", "Another send.")
    assert receipts.confirm(token, telegram_chat_id=int(group), telegram_message_id=44)
    assert not receipts.confirm(token, telegram_chat_id=int(group), telegram_message_id=44)
    saved = replies.act_on_draft(thread[0], draft["id"], "approve", "Another send.")
    assert saved["status"] == "sent" and saved["already_sent"] and saved["text"] == "Reviewed text."
    assert len(sent_frames) == expected_frames


@pytest.mark.parametrize("channel", ["slack", "sms", "", "outlook"])
def test_unknown_channel_never_falls_through_to_mailshot_or_gmail(monkeypatch, channel):
    def boom(*a, **k):
        raise AssertionError("no other provider may be used")

    monkeypatch.setattr("distr.core.tensology_client.configured_tensology_client", boom)
    monkeypatch.setattr("distr.core.integrations.whatsapp.relay_client.send_message_via_relay", boom)
    monkeypatch.setattr("distr.core.workflow.reply_delivery._send_telegram", boom)
    with pytest.raises(replies.ReplyPreflightError):
        replies.send_reply({"channel": channel, "thread_id": "x", "message_id": "m", "from": "a@b.c",
                            "to": ["c@d.e"], "cc": []}, "t", "k")


def test_resolve_target_refuses_a_non_group_telegram_chat():
    with pytest.raises(ValueError):
        replies.resolve_target({"channel": "telegram", "thread_id": "4242", "message_ids": ["1"],
                                "primary_message_ids": ["1"], "label": "x"})
    with pytest.raises(ValueError):
        replies.resolve_target({"channel": "sms", "thread_id": "x", "message_ids": ["1"], "label": "x"})


@pytest.fixture
def telegram_thread(monkeypatch):
    proposal = {"source": "telegram", "project_id": 9, "source_thread_id": "-100777",
                "message_ids": ["55"], "participant": "Shop team",
                "evidence": [{"id": "55", "body": "Can you fix the checkout?"}]}
    with get_session() as db:
        root = Chat(title="Telegram proposal fixture", project_id=9,
                    params=json.dumps({"incoming_proposal_handoff": {"proposal": proposal}}))
        db.add(root); db.flush()
        response = Chat(parent_id=root.id, response="I fixed the checkout button. It is not deployed yet.")
        db.add(response); db.commit()
        ids = root.id, response.id
    monkeypatch.setattr(replies, "resolve_target", lambda source: {"channel": "telegram", "thread_id": source["thread_id"],
                                                                    "label": source["label"], "recipient_names": ["Anna"]})
    yield ids
    with get_session() as db:
        db.query(SourceReplyDraft).filter(SourceReplyDraft.chat_id == ids[0]).delete()
        for cid in reversed(ids):
            row = db.get(Chat, cid)
            if row:
                db.delete(row)
        db.commit()


def test_finished_telegram_proposal_reply_goes_back_to_the_same_group(telegram_thread):
    with get_session() as db:
        assert replies.has_reply_source(db.get(Chat, telegram_thread[0])) is True  # Message Client is offered
    draft = replies.create_draft(*telegram_thread, "telegram:-100777",
                                 complete=lambda *a: "Hi Anna, the checkout button is fixed. I'll let you know when it's live.")
    assert draft["target"]["channel"] == "telegram" and draft["target"]["thread_id"] == "-100777"
    assert draft["target"]["project_id"] == 9
    sender = Mock()
    result = replies.act_on_draft(telegram_thread[0], draft["id"], "approve", "Hi Anna, it's fixed.", sender=sender)
    assert result["status"] == "sent"
    sender.assert_called_once()
    sent_target = sender.call_args.args[0]
    assert sent_target["channel"] == "telegram" and sent_target["thread_id"] == "-100777"


# ------------------------------------------------------------------ voice / push-to-talk untouched

def test_voice_and_push_to_talk_replies_stay_spoken():
    from distr.core.initiative import channel_select as cs
    assert cs.decide(incoming="ptt").channel == "spoken"
    assert cs.decide(incoming="voice").channel == "spoken"
