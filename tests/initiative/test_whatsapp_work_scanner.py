from datetime import datetime, timedelta
from types import SimpleNamespace

from distr.core.initiative.work_scanner import _scan_whatsapp


class _FakeQuery:
    def __init__(self, rows):
        self._rows = rows

    def filter(self, *args, **kwargs):
        return self

    def order_by(self, *args, **kwargs):
        return self

    def limit(self, *args, **kwargs):
        return self

    def all(self):
        return list(self._rows)

    def first(self):
        return self._rows[0] if self._rows else None


class _FakeSession:
    def __init__(self, *, messages, links, boards):
        self.messages = messages
        self.links = links
        self.boards = boards

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def query(self, model):
        name = getattr(model, "__name__", "")
        if name == "WhatsAppMessage":
            return _FakeQuery(self.messages)
        if name == "WhatsAppPhoneLink":
            return _FakeQuery(self.links)
        if name == "KanbanBoard":
            return _FakeQuery(self.boards)
        return _FakeQuery([])


def test_whatsapp_scan_proposes_linked_board_snapshot(monkeypatch):
    now = datetime.utcnow()
    msg = SimpleNamespace(
        id=42,
        jid="27820001111@s.whatsapp.net",
        jid_phone="27820001111",
        sender_push_name="Ava",
        sender_phone="27820001111",
        sender_jid="27820001111@s.whatsapp.net",
        text="Please fix the checkout bug and create a ticket.",
        caption="",
        media_type="",
        created_date=now - timedelta(seconds=20),
    )
    link = SimpleNamespace(board_id=7, phone_number="27820001111", auto_snapshot=False)
    board = SimpleNamespace(id=7, name="Client Board")

    monkeypatch.setattr(
        "distr.core.db.get_session",
        lambda: _FakeSession(messages=[msg], links=[link], boards=[board]),
    )

    scan = {"messages": {"whatsapp": [], "telegram": [], "email": []}, "proposals": []}
    _scan_whatsapp(scan)

    assert scan["messages"]["whatsapp"][0]["linked_board_name"] == "Client Board"
    proposal = scan["proposals"][0]
    assert proposal["action_type"] == "message_triage"
    assert proposal["payload"]["linked_board_id"] == 7
    assert proposal["payload"]["message_ids"] == [42]
    assert "snapshot" in proposal["draft"].lower()


def test_whatsapp_scan_notifies_fresh_non_work_message(monkeypatch):
    msg = SimpleNamespace(
        id=9,
        jid="27820002222@s.whatsapp.net",
        jid_phone="27820002222",
        sender_push_name="Maya",
        sender_phone="27820002222",
        sender_jid="27820002222@s.whatsapp.net",
        text="hey are you around?",
        caption="",
        media_type="",
        created_date=datetime.utcnow() - timedelta(seconds=30),
    )

    monkeypatch.setattr(
        "distr.core.db.get_session",
        lambda: _FakeSession(messages=[msg], links=[], boards=[]),
    )

    scan = {"messages": {"whatsapp": [], "telegram": [], "email": []}, "proposals": []}
    _scan_whatsapp(scan)

    proposal = scan["proposals"][0]
    assert proposal["payload"]["source"] == "whatsapp"
    assert proposal["payload"]["latest_sender"] == "Maya"
    assert "just got a WhatsApp message" in proposal["description"]
