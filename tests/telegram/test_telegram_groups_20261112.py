"""Telegram: posting into explicitly linked groups, and inbound from third-party chats (12 Nov 2026).

Every relay frame is intercepted by a fake manager and recorded; nothing reaches Telegram.
No chat id is guessed: the only ids used are the ones in each test's fake provenance.
"""

import queue
from pathlib import Path
from types import SimpleNamespace

import pytest

from distr.core.incoming.source_reply import ReplyPreflightError
from distr.core.integrations.telegram import chat_send, third_party_intake as tpi
from distr.core.integrations.telegram.messages import TelegramMessagesMixin
from distr.core.workflow import reply_delivery

REPO = Path(__file__).resolve().parents[2]
PAUL = 4242
GROUP = "-1001234567"
OTHER_GROUP = "-1009999999"
LINKS = {("telegram", GROUP): {7}}


class FakeRelay:
    """Stands in for TelegramManager: records frames instead of writing to the socket."""

    def __init__(self, connected=True, accept=True):
        self.telegram_user_id = PAUL
        self.chat_id = None
        self.connected, self.accept = connected, accept
        self.frames, self.private = [], []
        self._last_send_time, self._min_send_interval = 0.0, 0.0

    def is_connected(self):
        return self.connected

    def _send_websocket_message(self, frame):
        self.frames.append(dict(frame))
        return self.accept

    def send_to_telegram(self, text, **_):
        self.private.append(text)
        return True


@pytest.fixture
def relay(monkeypatch):
    fake = FakeRelay()
    monkeypatch.setattr("distr.core.kanban.ticket_workflow_engagement._telegram_manager_from_app", lambda: fake)
    monkeypatch.setattr("distr.core.incoming.project_links.source_project_index", lambda refresh=False: LINKS)
    return fake


# ----------------------------------------------------------------- group send

def test_linked_group_reply_is_one_relay_frame_to_that_group(relay):
    out = reply_delivery._send_telegram(target=GROUP, body="Fixed and live now.")
    assert out["success"] is True and out["chat_id"] == GROUP
    assert relay.frames == [{"type": "send_message", "chat_id": int(GROUP), "text": "Fixed and live now.",
                             "client_message_id": out["client_message_id"]}]
    assert out["client_message_id"].startswith(chat_send.CORRELATION_PREFIX)
    assert relay.private == []


def test_group_last_seen_is_not_mistaken_for_pauls_chat(relay):
    relay.chat_id = int(OTHER_GROUP)  # messages.py tracks the last inbound chat, which can be a group
    with pytest.raises(ReplyPreflightError, match="can't reply to"):
        reply_delivery._send_telegram(target=OTHER_GROUP, body="Hi")
    assert relay.frames == [] and relay.private == []


def test_paired_chat_still_uses_the_private_sender(relay):
    assert reply_delivery._send_telegram(target=str(PAUL), body="Done")["success"] is True
    assert relay.private == ["Done"] and relay.frames == []


def test_last_seen_private_chat_cannot_redirect_a_reply_to_the_paired_owner(relay):
    relay.chat_id = 5551
    with pytest.raises(ReplyPreflightError, match="can't reply to"):
        reply_delivery._send_telegram(target="5551", body="Reviewed reply")
    assert relay.private == [] and relay.frames == []


def test_group_link_to_a_different_project_does_not_authorize_this_reply(relay):
    with pytest.raises(ReplyPreflightError, match="can't reply to"):
        reply_delivery._send_telegram(target=GROUP, body="Reviewed reply", project_id=8)
    assert relay.frames == []


@pytest.mark.parametrize("target", [OTHER_GROUP, "12345", "", "group-name", "-12"])
def test_unlinked_or_unverifiable_chats_are_refused(relay, target):
    with pytest.raises(ReplyPreflightError, match="Nothing was sent"):
        reply_delivery._send_telegram(target=target, body="Hi")
    assert relay.frames == []


def test_disconnected_relay_refuses_clearly(relay):
    relay.connected = False
    with pytest.raises(ReplyPreflightError, match="can't post into groups right now"):
        reply_delivery._send_telegram(target=GROUP, body="Hi")
    assert relay.frames == []


def test_link_is_rechecked_immediately_before_transport(relay):
    answers = iter([True, False])
    with pytest.raises(chat_send.GroupSendRefused, match="unlinked"):
        chat_send.send_to_chat(relay, GROUP, "Hi", link_check=lambda chat: next(answers))
    assert relay.frames == []


def test_send_to_chat_refuses_the_paired_chat_and_empty_text(relay):
    relay.telegram_user_id = int(GROUP)  # defensive: never post "as a group" into Paul's own id
    with pytest.raises(chat_send.GroupSendRefused):
        chat_send.send_to_chat(relay, GROUP, "Hi", link_check=lambda chat: True)
    relay.telegram_user_id = PAUL
    with pytest.raises(chat_send.GroupSendRefused, match="nothing to post"):
        chat_send.send_to_chat(relay, GROUP, "   ", link_check=lambda chat: True)
    assert relay.frames == []


def test_internal_prompts_never_reach_a_group(relay):
    leaked = "Done. DECISIONS DEVELOPMENT THREAD. Work as the active IDE coding agent and use the project tools directly."
    reply_delivery._send_telegram(target=GROUP, body=leaked)
    assert "DECISIONS DEVELOPMENT THREAD" not in relay.frames[0]["text"]


def test_socket_refusal_is_reported_not_retried(relay):
    relay.accept = False
    out = reply_delivery._send_telegram(target=GROUP, body="Hi")
    assert out["success"] is False and len(relay.frames) == 1


def test_group_send_stays_behind_the_posture_gate():
    # Telegram replies to clients only go out under Own or a reply Paul confirmed (force=True).
    assert reply_delivery.POSTURE_ADAPTERS["telegram"] is reply_delivery._send_telegram
    assert "telegram" not in reply_delivery.ADAPTERS


def test_group_sync_drops_stale_connection_results_and_preserves_links_on_fetch_failure():
    from distr.core.integrations.telegram.manager import TelegramWebSocketManager

    current = object()
    frames = []
    manager = SimpleNamespace(_group_link_generation=current, _group_link_sync_running=True,
                              _group_links_supported=True, is_connected=lambda: True,
                              _send_websocket_message=frames.append)
    publish = TelegramWebSocketManager._publish_group_links
    publish(manager, object(), [int(GROUP)])
    assert manager._group_link_sync_running and frames == []
    publish(manager, current, None)
    assert not manager._group_link_sync_running and frames == []
    publish(manager, current, [int(GROUP)])
    publish(manager, current, [])
    assert frames == [{'type': 'group_links', 'chat_ids': [int(GROUP)]}, {'type': 'group_links', 'chat_ids': []}]


# ----------------------------------------------------------------- third-party inbound

def msg(chat=GROUP, ctype="supergroup", mid=11, sender=777, is_bot=False, text="Can you check the invoice?"):
    data = {"chat_id": chat, "chat_type": ctype, "message_id": mid, "text": text,
            "chat": {"type": ctype, "title": "Shop team"}}
    if sender is not None:
        data["from"] = {"id": sender, "is_bot": is_bot, "first_name": "Anna"}
    return data


@pytest.mark.parametrize("data, kind, reason", [
    (msg(chat=str(PAUL), ctype="private", sender=PAUL), "owner_private", "paired"),
    (msg(chat="5551", ctype="private", sender=5551), "refused", "isn't the paired one"),
    (msg(ctype="channel", sender=None), "refused", "unsupported chat type"),
    (msg(mid=None), "refused", "no message id"),
    (msg(sender=None), "refused", "no sender"),
    (msg(is_bot=True), "refused", "bot"),
    (msg(chat=None), "refused", "no chat id"),
    (msg(chat=OTHER_GROUP), "unlinked_group", "no project link"),
    (msg(), "linked_group", "explicitly linked"),
])
def test_classify_inbound(data, kind, reason):
    d = tpi.classify_inbound(data, paired_user_id=PAUL, index=LINKS)
    assert d.kind == kind and reason in d.reason
    assert d.actionable is (kind == "linked_group")


def test_linked_group_carries_its_projects_and_never_becomes_paul():
    d = tpi.classify_inbound(msg(), paired_user_id=PAUL, index=LINKS)
    assert d.project_ids == frozenset({7}) and d.sender_label == "Anna" and d.from_owner is False
    mine = tpi.classify_inbound(msg(sender=PAUL), paired_user_id=PAUL, index=LINKS)
    assert mine.kind == "linked_group" and mine.from_owner is True  # still a group, not Paul's channel


def test_only_linked_groups_with_full_provenance_lead_to_one_heads_up(monkeypatch):
    tpi.reset_for_tests()
    calls = []
    notify = lambda d, text, settings: calls.append((d.chat_id, text))
    monkeypatch.setattr(tpi, "_assistant_on", lambda: (True, {}))
    for data in (msg(chat=OTHER_GROUP), msg(sender=None), msg(is_bot=True), msg(sender=PAUL), msg(ctype="channel")):
        tpi.handle_group_inbound(data, paired_user_id=PAUL, index=LINKS, notify=notify)
    assert calls == []
    tpi.handle_group_inbound(msg(), paired_user_id=PAUL, index=LINKS, notify=notify)
    tpi.handle_group_inbound(msg(mid=12, text="Any news?"), paired_user_id=PAUL, index=LINKS, notify=notify)
    assert calls == [(GROUP, "Can you check the invoice?")]  # once per group per half hour
    tpi.reset_for_tests()


def test_no_heads_up_while_be_my_assistant_is_off(monkeypatch):
    tpi.reset_for_tests()
    calls = []
    monkeypatch.setattr(tpi, "_assistant_on", lambda: (False, {}))
    tpi.handle_group_inbound(msg(), paired_user_id=PAUL, index=LINKS, notify=lambda *a: calls.append(a))
    assert calls == []


def test_heads_up_goes_to_one_place_through_the_channel_selector(monkeypatch, tmp_path):
    import distr.core.initiative.outcome_flow as of

    tpi.reset_for_tests()
    monkeypatch.setenv("DECISIONS_DB_DIR", str(tmp_path))
    relay = FakeRelay()
    voice = []
    monkeypatch.setattr(of, "_telegram_manager", lambda: relay)
    monkeypatch.setattr("distr.core.orchestrator.emit_event", lambda **kw: None)
    monkeypatch.setattr("distr.core.signals.speak_text_directly_event_queue", lambda text: voice.append(text) or True)
    monkeypatch.setattr(of, "_in_meeting_now", lambda now: False)
    monkeypatch.setattr(of, "_project_name_map", lambda: {7: "Shop"})
    # Same-channel rule: the heads-up answers no message of Paul's, so it stays in the app.
    monkeypatch.setattr(tpi, "_assistant_on", lambda: (True, {"initiative_level": "operate"}))
    tpi.handle_group_inbound(msg(), paired_user_id=PAUL, index=LINKS)
    shown = of.assistant_nudges()
    assert len(shown) == 1 and shown[0]["text"].startswith("Anna wrote in Shop team (Shop)")
    assert relay.private == [] and relay.frames == [] and voice == []  # nothing pushed, nothing to the group
    tpi.reset_for_tests()


class GroupHarness(TelegramMessagesMixin):
    def __init__(self):
        self._processed_message_ids, self._processed_message_hashes = set(), set()
        self._max_processed_cache_size = 100
        self.chat_id, self.telegram_user_id = None, PAUL
        self.sent, self.enqueued = [], []
        self._group_message_queue = queue.Queue()
        self._group_messages_storage, self._max_stored_group_messages = [], 50
        self._pending_telegram_files, self._telegram_file_downloads = [], set()
        self._telegram_batch_buffer = []
        self.message_received = SimpleNamespace(emit=lambda data: None)

    def send_to_telegram(self, text, **_):
        self.sent.append(text)
        return True

    def _enqueue_telegram_batch(self, *args, **kwargs):
        self.enqueued.append(args)

    def _track_telegram_group(self, *a, **k):
        pass

    def _update_stored_connection_data(self, **k):
        pass

    def _mark_message_as_read(self, *a, **k):
        pass

    def _stop_typing_loop(self):
        pass

    def _start_typing_loop(self, *_):
        pass


def test_group_messages_reach_the_intake_and_never_the_owner_paths(monkeypatch):
    seen, owner_paths = [], []
    monkeypatch.setattr(tpi, "handle_group_inbound", lambda data, paired_user_id: seen.append((data["chat_id"], paired_user_id)))
    monkeypatch.setattr("distr.core.initiative.outcome_flow.handle_telegram_text",
                        lambda *a, **k: owner_paths.append(a))
    monkeypatch.setattr("distr.core.db.get_session", lambda: (_ for _ in ()).throw(RuntimeError("no db in tests")))
    h = GroupHarness()
    h._handle_telegram_message({"data": {**msg(sender=PAUL, text="remote"), "chat_id": int(GROUP)}})
    assert seen == [(int(GROUP), PAUL)]
    assert h.sent == [] and h.enqueued == [] and owner_paths == []
    assert len(h._group_messages_storage) == 1


def test_group_message_identity_survives_shared_ids_and_desktop_restart(monkeypatch, tmp_path):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from distr.core.db import TelegramGroupMessage

    engine = create_engine(f'sqlite:///{tmp_path / "groups.sqlite"}')
    TelegramGroupMessage.__table__.create(engine)
    factory = sessionmaker(bind=engine)
    monkeypatch.setattr('distr.core.db.get_session', factory)
    seen = []
    monkeypatch.setattr(tpi, 'handle_group_inbound', lambda data, **_: seen.append(data['chat_id']))
    first, second = (msg(chat=int(chat), mid=42, text='Same text from the same sender.')
                     for chat in (GROUP, OTHER_GROUP))
    desktop = GroupHarness()
    for data in (first, second, {**first, 'message_id': '42'}):
        desktop._handle_telegram_message({'data': data})
    reopened = GroupHarness()
    reopened._handle_telegram_message({'data': second})
    with factory() as db:
        saved = db.query(TelegramGroupMessage).order_by(TelegramGroupMessage.id).all()
        assert [(row.chat_id, row.telegram_message_id) for row in saved] == [(GROUP, 42), (OTHER_GROUP, 42)]
    assert seen == [int(GROUP), int(OTHER_GROUP)]
    assert len(desktop._group_messages_storage) == 2
    assert reopened._group_message_queue.empty()
    engine.dispose()
