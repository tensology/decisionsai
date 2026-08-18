import contextlib

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from distr.core.agent.services.llm.fast_action_detector import ActionType, detect_fast_action
from distr.core.agent.tool_intents import forced_tool_names_for_text
from distr.core.agent.tools.integrations.kanban_ticket import KanbanTicketTool
from distr.core.db import Base
from distr.core.db.kanban import KanbanBoard


def _memory_session(monkeypatch):
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)

    @contextlib.contextmanager
    def get_session():
        session = factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    monkeypatch.setattr("distr.core.db.get_session", get_session)
    return get_session


def test_ticket_board_path_local():
    assert KanbanTicketTool._ticket_board_path("database", 42) == "/tickets/?board_id=42"


def test_ticket_board_path_jira():
    assert KanbanTicketTool._ticket_board_path("jira", 99) == "/tickets/?source=jira&board_id=99"


def test_ticket_board_path_trello_with_url():
    path = KanbanTicketTool._ticket_board_path("trello", "abc", "https://trello.com/b/abc/board")
    assert path.startswith("/tickets/?")
    assert "source=trello" in path
    assert "board_id=abc" in path
    assert "board_url=" in path


def test_open_board_local_deep_link(monkeypatch):
    get_session = _memory_session(monkeypatch)
    opened = []

    with get_session() as session:
        board = KanbanBoard(name="Player One Sport", source="database")
        session.add(board)
        session.flush()
        board_id = board.id

    monkeypatch.setattr(
        "distr.core.agent.tools.integrations.kanban_ticket.KanbanTicketTool._resolve_web_base_url",
        lambda self: "http://127.0.0.1:8765",
    )
    monkeypatch.setattr(
        "distr.core.agent.tools.integrations.kanban_ticket.webbrowser.open",
        lambda url: opened.append(url),
    )

    tool = KanbanTicketTool()
    result = tool._run(
        action="open_board",
        board_name="Player One Sport",
        text="open the Player One Sport local board",
        source_provider="local",
    )

    assert opened == [f"http://127.0.0.1:8765/tickets/?board_id={board_id}"]
    assert "Player One Sport" in result
    assert "opened" in result.lower()


def test_open_board_disambiguates_local_and_jira(monkeypatch):
    _memory_session(monkeypatch)
    monkeypatch.setattr(
        "distr.core.agent.tools.integrations.kanban_ticket.KanbanTicketTool._fetch_external_boards",
        lambda self: {
            "jira": [{"id": "55", "name": "Player One Sport", "project_key": "POS"}],
            "trello": [],
        },
    )
    monkeypatch.setattr(
        "distr.core.agent.tools.integrations.kanban_ticket.KanbanTicketTool._all_boards",
        lambda self: [{"id": 7, "name": "Player One Sport"}],
    )
    monkeypatch.setattr(
        "distr.core.agent.tools.integrations.kanban_ticket.KanbanTicketTool._find_board",
        lambda self, board_id=None, board_name=None: {"id": 7, "name": "Player One Sport"},
    )

    tool = KanbanTicketTool()
    result = tool._run(action="open_board", board_name="Player One Sport", text="open Player One Sport board")

    assert "Multiple boards match" in result
    assert "local" in result.lower()
    assert "jira" in result.lower()
    assert "/tickets/?board_id=7" in result
    assert "/tickets/?source=jira&board_id=55" in result


def test_open_ticket_board_fast_action():
    result = detect_fast_action("open the ticket board in Brave")
    assert result.action_type == ActionType.OPEN_WINDOW
    assert result.tool_name == "create_ticket"
    assert result.tool_args.get("action") == "open_board"


def test_open_named_board_fast_action():
    result = detect_fast_action("open the jira Player One Sport board")
    assert result.tool_name == "create_ticket"
    assert result.tool_args.get("action") == "open_board"
    assert result.tool_args.get("source_provider") == "jira"
    assert result.tool_args.get("board_name") == "Player One Sport"


def test_open_board_forces_create_ticket_tool():
    forced = forced_tool_names_for_text("open the ticket board")
    assert "create_ticket" in forced
