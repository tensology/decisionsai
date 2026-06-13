from distr.core.agent.tools.integrations.kanban_ticket import KanbanTicketInput, KanbanTicketTool
from distr.core.agent.tools.loader import TOOL_DESCRIPTIONS
from datetime import datetime


def test_kanban_tool_exposes_whatsapp_agent_actions():
    action_description = KanbanTicketInput.model_fields["action"].description
    tool_description = KanbanTicketTool().description

    for action in (
        "whatsapp_sync",
        "whatsapp_latest_activity",
        "whatsapp_work_overview",
        "whatsapp_list_contacts",
        "whatsapp_list_chats",
        "whatsapp_list_messages",
        "whatsapp_mark_processed",
        "whatsapp_snapshot_to_ticket",
        "whatsapp_send_message",
        "whatsapp_set_draft",
        "whatsapp_get_draft",
        "whatsapp_list_drafts",
    ):
        assert action in action_description
        assert action in tool_description


def test_kanban_tool_triggers_whatsapp_retrieval_phrases():
    triggers = KanbanTicketTool().get_triggers()

    for phrase in (
        "whatsapp messages",
        "whatsapp context",
        "whatsapp snapshot",
        "create ticket from whatsapp",
    ):
        assert phrase in triggers


def test_tool_retriever_description_mentions_whatsapp_intake_and_reply():
    description = TOOL_DESCRIPTIONS["KanbanTicketTool"].lower()

    for phrase in (
        "whatsapp",
        "sync",
        "work-related",
        "contacts",
        "handled",
        "snapshot",
        "tickets",
        "replies",
        "draft",
    ):
        assert phrase in description


def test_ticket_details_include_whatsapp_source_when_available():
    tool = KanbanTicketTool()
    source_line = tool._ticket_whatsapp_source_line(
        type(
            "Ticket",
            (),
            {
                "whatsapp_message_id": 42,
                "whatsapp_message_wa_id": "wa-abc",
            },
        )()
    )

    assert "WhatsApp source" in source_line
    assert "message_id=42" in source_line
    assert "wa_id=wa-abc" in source_line


def test_ticket_details_include_complexity_and_source_provider():
    tool = KanbanTicketTool()
    ticket = type(
        "Ticket",
        (),
        {
            "id": 7,
            "title": "Fix workflow routing",
            "lane": None,
            "priority": "medium",
            "complexity": "high",
            "description": "Wire source provenance into workflow responses.",
            "send_to_cli": True,
            "external_id": None,
            "source_provider": "gmail",
            "source_contact": "client@example.com",
            "source_external_id": "msg-123",
            "source_thread_id": "thread-456",
            "source_url": "",
            "whatsapp_message_id": None,
            "files": [],
            "todos": [],
            "links": [],
        },
    )()

    detail = "\n".join(tool._ticket_detail_parts(ticket))

    assert "Complexity: high" in detail
    assert "Source: gmail" in detail
    assert "contact=client@example.com" in detail


class _DetachedAfterCloseRow:
    def __init__(self, **values):
        object.__setattr__(self, "_detached", False)
        for key, value in values.items():
            object.__setattr__(self, key, value)

    def __getattribute__(self, name):
        if name.startswith("_") or name in {"detach"}:
            return object.__getattribute__(self, name)
        if object.__getattribute__(self, "_detached"):
            raise RuntimeError(f"detached row accessed: {name}")
        return object.__getattribute__(self, name)

    def detach(self):
        object.__setattr__(self, "_detached", True)


class _FakeQuery:
    def __init__(self, rows):
        self._rows = rows

    def filter(self, *args, **kwargs):
        return self

    def filter_by(self, *args, **kwargs):
        return self

    def order_by(self, *args, **kwargs):
        return self

    def limit(self, *args, **kwargs):
        return self

    def all(self):
        return list(self._rows)

    def first(self):
        return self._rows[0] if self._rows else None


class _DetachOnExitSession:
    def __init__(self, *, messages, links, boards, detach_on_exit=True):
        self.messages = messages
        self.links = links
        self.boards = boards
        self.projects = []
        self.detach_on_exit = detach_on_exit

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        if self.detach_on_exit:
            for row in [*self.messages, *self.links, *self.boards, *self.projects]:
                row.detach()
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
        return _FakeQuery([])


def test_whatsapp_latest_activity_snapshots_rows_before_session_closes(monkeypatch):
    message = _DetachedAfterCloseRow(
        id=91,
        jid="27820001111@s.whatsapp.net",
        jid_phone="27820001111",
        sender_push_name="Carmen",
        sender_phone="27820001111",
        sender_jid="27820001111@s.whatsapp.net",
        from_me=False,
        chat_type="private",
        text="Please check the missing invoice issue.",
        caption="",
        media_type="",
        processed=False,
        snapshot_group="",
        created_date=datetime(2026, 5, 22, 14, 36, 34),
    )
    link = _DetachedAfterCloseRow(phone_number="27820001111", board_id=7, auto_snapshot=True)
    board = _DetachedAfterCloseRow(id=7, name="Merrypak")

    tool = KanbanTicketTool()
    monkeypatch.setattr(
        tool,
        "_get_session",
        lambda: _DetachOnExitSession(messages=[message], links=[link], boards=[board]),
    )

    result = tool._action_whatsapp_latest_activity(limit=5)

    assert "last WhatsApp message" in result
    assert "Carmen" in result
    assert "linked_board=Merrypak" in result


def test_project_whatsapp_feed_resolves_project_board_and_asks_for_snapshot(monkeypatch):
    message = _DetachedAfterCloseRow(
        id=92,
        jid="27820002222@s.whatsapp.net",
        jid_phone="27820002222",
        sender_push_name="Carmen",
        sender_phone="27820002222",
        sender_jid="27820002222@s.whatsapp.net",
        from_me=False,
        chat_type="private",
        text="Invoice 13589W is missing from web.",
        caption="",
        media_type="photo",
        processed=False,
        snapshot_group="",
        created_date=datetime(2026, 5, 22, 15, 0, 0),
    )
    link = _DetachedAfterCloseRow(
        id=5,
        board_id=8,
        phone_jid="27820002222@s.whatsapp.net",
        phone_number="27820002222",
        contact_name="Merrypak WhatsApp",
        auto_snapshot=True,
    )
    board = _DetachedAfterCloseRow(
        id=8,
        name="Merrypak",
        description="",
        default_project_id=3,
        default_workflow_id=None,
        default_action_id=None,
        send_to_cli=False,
        agent_source_lane="Backlog",
        agent_done_lane="Done",
        archived=False,
        source="database",
        position=0,
    )
    project = _DetachedAfterCloseRow(id=3, name="Merrypak", kanban_board_id=8)
    session = _DetachOnExitSession(messages=[message], links=[link], boards=[board], detach_on_exit=False)
    session.projects = [project]

    tool = KanbanTicketTool()
    monkeypatch.setattr(tool, "_get_session", lambda: session)

    result = tool._action_whatsapp_project_feed(project_name="Merrypak", limit=10)

    assert "Would you like me to create one backlog ticket" in result
    assert "message_ids=[92]" in result
    assert "whatsapp_project_snapshot_to_ticket" in result
