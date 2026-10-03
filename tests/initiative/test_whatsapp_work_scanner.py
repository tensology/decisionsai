from datetime import datetime, timedelta
from types import SimpleNamespace

from distr.core.initiative.work_scanner import _WHATSAPP_BATCH_QUIET_SECONDS, _scan_whatsapp


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
    def __init__(self, *, messages, links, boards, projects=None, ticketed_message_ids=None):
        self.messages = messages
        self.links = links
        self.boards = boards
        self.projects = projects or []
        self.ticketed_message_ids = ticketed_message_ids or []

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
        if name == "Project":
            return _FakeQuery(self.projects)
        if name == "KanbanTicket":
            return _FakeQuery([(mid,) for mid in self.ticketed_message_ids])
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
        created_date=now - timedelta(seconds=_WHATSAPP_BATCH_QUIET_SECONDS + 15),
    )
    link = SimpleNamespace(board_id=7, phone_number="27820001111", auto_snapshot=False)
    board = SimpleNamespace(id=7, name="Client Board", default_project_id=17)
    project = SimpleNamespace(id=17, name="Client Project")

    monkeypatch.setattr(
        "distr.core.db.get_session",
        lambda: _FakeSession(messages=[msg], links=[link], boards=[board], projects=[project]),
    )

    scan = {"messages": {"whatsapp": [], "telegram": [], "email": []}, "proposals": []}
    _scan_whatsapp(scan)

    assert scan["messages"]["whatsapp"][0]["linked_board_name"] == "Client Board"
    proposal = scan["proposals"][0]
    assert proposal["action_type"] == "message_triage"
    assert proposal["payload"]["linked_board_id"] == 7
    assert proposal["payload"]["linked_project_id"] == 17
    assert proposal["payload"]["message_ids"] == [42]
    assert proposal["payload"]["notification_format"] == "text"
    assert proposal["payload"]["batch_quiet_seconds"] == 300
    assert _WHATSAPP_BATCH_QUIET_SECONDS == 300  # 5 minutes
    assert proposal["payload"]["approval_flow"] == "linked_intake_create_execute"
    assert proposal["payload"]["jid"] == "27820001111@s.whatsapp.net"
    assert "compact" in proposal["draft"].lower()
    assert "execute it end to end" in proposal["telegram_message"].lower()


def test_whatsapp_scan_ignores_fresh_unlinked_non_work_message(monkeypatch):
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

    assert scan["messages"]["whatsapp"] == []
    assert scan["proposals"] == []


def test_whatsapp_scan_groups_linked_project_messages_into_one_work_notice(monkeypatch):
    now = datetime.utcnow()
    messages = [
        SimpleNamespace(
            id=message_id,
            jid="27820005555@s.whatsapp.net",
            jid_phone="27820005555",
            sender_push_name="Client",
            sender_phone="27820005555",
            sender_jid="27820005555@s.whatsapp.net",
            text=text,
            caption="",
            media_type="",
            created_date=now - timedelta(seconds=_WHATSAPP_BATCH_QUIET_SECONDS + 15 + offset),
            processed=False,
            snapshot_group=None,
        )
        for message_id, text, offset in (
            (61, "Morning", 30),
            (62, "The checkout is broken", 20),
            (63, "Customers cannot pay", 10),
            (64, "Please fix this bug urgently", 0),
        )
    ]
    link = SimpleNamespace(board_id=10, phone_number="27820005555", auto_snapshot=False)
    board = SimpleNamespace(id=10, name="Payments", default_project_id=20)
    project = SimpleNamespace(id=20, name="Payments App")
    monkeypatch.setattr(
        "distr.core.db.get_session",
        lambda: _FakeSession(
            messages=messages,
            links=[link],
            boards=[board],
            projects=[project],
        ),
    )

    scan = {"messages": {"whatsapp": [], "telegram": [], "email": []}, "proposals": []}
    _scan_whatsapp(scan)

    assert len(scan["proposals"]) == 1
    proposal = scan["proposals"][0]
    assert proposal["payload"]["message_count"] == 4
    assert proposal["payload"]["message_ids"] == [61, 62, 63, 64]
    assert proposal["payload"]["notification_format"] == "text"
    assert proposal["payload"]["batch_quiet_seconds"] == 300
    assert "one batch" in proposal["description"]


def test_whatsapp_scan_ignores_stale_linked_board_message(monkeypatch):
    msg = SimpleNamespace(
        id=55,
        jid="27820003333@s.whatsapp.net",
        jid_phone="27820003333",
        sender_push_name="Old Client",
        sender_phone="27820003333",
        sender_jid="27820003333@s.whatsapp.net",
        text="Please fix the checkout bug.",
        caption="",
        media_type="",
        created_date=datetime.utcnow() - timedelta(days=2),
        processed=False,
        snapshot_group=None,
    )
    link = SimpleNamespace(board_id=8, phone_number="27820003333", auto_snapshot=False)
    board = SimpleNamespace(id=8, name="Old Board", default_project_id=18)
    project = SimpleNamespace(id=18, name="Old Project")
    monkeypatch.setattr(
        "distr.core.db.get_session",
        lambda: _FakeSession(messages=[msg], links=[link], boards=[board], projects=[project]),
    )

    scan = {"messages": {"whatsapp": [], "telegram": [], "email": []}, "proposals": []}
    _scan_whatsapp(scan)

    assert scan["messages"]["whatsapp"] == []
    assert scan["proposals"] == []


def test_whatsapp_scan_ignores_already_ticketed_or_snapshot_messages(monkeypatch):
    now = datetime.utcnow()
    snapshot_msg = SimpleNamespace(
        id=56,
        jid="27820004444@s.whatsapp.net",
        jid_phone="27820004444",
        sender_push_name="Ticketed Client",
        sender_phone="27820004444",
        sender_jid="27820004444@s.whatsapp.net",
        text="Please make a ticket for this urgent bug.",
        caption="",
        media_type="",
        created_date=now - timedelta(seconds=30),
        processed=False,
        snapshot_group="123_1",
    )
    ticketed_msg = SimpleNamespace(
        id=57,
        jid="27820004444@s.whatsapp.net",
        jid_phone="27820004444",
        sender_push_name="Ticketed Client",
        sender_phone="27820004444",
        sender_jid="27820004444@s.whatsapp.net",
        text="Another urgent ticketed bug.",
        caption="",
        media_type="",
        created_date=now - timedelta(seconds=20),
        processed=False,
        snapshot_group=None,
    )
    link = SimpleNamespace(board_id=9, phone_number="27820004444", auto_snapshot=False)
    board = SimpleNamespace(id=9, name="Ticketed Board", default_project_id=19)
    project = SimpleNamespace(id=19, name="Ticketed Project")
    monkeypatch.setattr(
        "distr.core.db.get_session",
        lambda: _FakeSession(
            messages=[snapshot_msg, ticketed_msg],
            links=[link],
            boards=[board],
            projects=[project],
            ticketed_message_ids=[57],
        ),
    )

    scan = {"messages": {"whatsapp": [], "telegram": [], "email": []}, "proposals": []}
    _scan_whatsapp(scan)

    assert scan["messages"]["whatsapp"] == []
    assert scan["proposals"] == []
