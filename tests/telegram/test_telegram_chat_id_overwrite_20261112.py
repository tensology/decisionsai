"""Desktop messages.py: a Telegram group id must never replace Paul's personal chat_id (12 Nov 2026).

No network, no DB, nothing reaches Telegram: reuses the fake harness from the groups test.
"""

import pytest

from distr.core.integrations.telegram import third_party_intake as tpi
from tests.telegram.test_telegram_groups_20261112 import GROUP, OTHER_GROUP, PAUL, GroupHarness, msg


@pytest.fixture
def harness(monkeypatch):
    seen = []
    monkeypatch.setattr(tpi, "handle_group_inbound", lambda data, **_: seen.append(data["chat_id"]))
    monkeypatch.setattr("distr.core.db.get_session", lambda: (_ for _ in ()).throw(RuntimeError("no db in tests")))
    h = GroupHarness()
    h.chat_id = PAUL
    h.seen = seen
    return h


@pytest.mark.parametrize("chat, ctype", [(int(GROUP), "supergroup"), (GROUP, "group"), (int(OTHER_GROUP), None)])
def test_group_message_keeps_personal_chat_id(harness, chat, ctype):
    data = msg(chat=chat, ctype=ctype or "supergroup")
    if ctype is None:  # type unknown: negative id alone marks it as a group
        data.pop("chat"); data.pop("chat_type")
    harness._handle_telegram_message({"data": data})
    assert harness.chat_id == PAUL
    assert harness.telegram_user_id == PAUL
    assert harness.seen == [chat]  # still routed through the group intake
    assert harness.sent == [] and harness.enqueued == []


def test_group_message_before_any_dm_leaves_chat_id_unset(harness):
    harness.chat_id = None
    harness._handle_telegram_message({"data": msg(chat=int(GROUP))})
    assert harness.chat_id is None
    assert harness._get_chat_id() is None if hasattr(harness, "_get_chat_id") else True


def test_personal_dm_still_sets_chat_id(harness):
    harness.chat_id = None
    harness._handle_telegram_message({"data": msg(chat=PAUL, ctype="private", sender=PAUL, text="hello")})
    assert harness.chat_id == PAUL


def test_unpaired_private_sender_still_rejected(harness):
    harness._handle_telegram_message({"data": msg(chat=5551, ctype="private", sender=5551, text="hi")})
    assert harness.chat_id == PAUL and harness.sent == [] and harness.enqueued == []
