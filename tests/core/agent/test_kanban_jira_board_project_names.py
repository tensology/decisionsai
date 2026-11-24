"""Regression tests: Jira board metadata & project-link visibility.

Hayley (chat 1381, 2026-11-23) told Paul "There's no Spoortrack board on the
Jira side at all" because every Jira board fetch dropped ``location.projectName``
and only kept the raw board name ("STK board"). These tests pin the full fix
across board fetch, listing, matching, list_jira_tickets, open_board, and
get_ticket.
"""

import contextlib

import requests
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from distr.core.agent.tools.integrations.kanban_ticket import KanbanTicketTool
from distr.core.db import Base
from distr.core.db.kanban import KanbanBoard, KanbanLane, KanbanTicket
from distr.core.db.projects import Project


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


def _jira_board(board_id, name, project_key, project_name):
    return {
        "id": str(board_id),
        "name": name,
        "project_key": project_key,
        "project_name": project_name,
        "display_name": f"{project_name} ({project_key})",
    }


def test_fetch_external_boards_preserves_jira_project_metadata_and_paginates(monkeypatch):
    pages = [
        {
            "isLast": False,
            "values": [
                {
                    "id": 31,
                    "name": "SP2 board",
                    "location": {
                        "projectKey": "SP2",
                        "projectName": "Snuza Pico 2",
                        "displayName": "Snuza Pico 2 (SP2)",
                    },
                }
            ],
        },
        {
            "isLast": True,
            "values": [
                {
                    "id": 36,
                    "name": "STK board",
                    "location": {
                        "projectKey": "STK",
                        "projectName": "SpoorTrack Revamp",
                        "displayName": "SpoorTrack Revamp (STK)",
                    },
                }
            ],
        },
    ]
    seen_starts = []

    class FakeResponse:
        status_code = 200

        def __init__(self, payload):
            self._payload = payload

        def json(self):
            return self._payload

    def fake_get(*args, **kwargs):
        params = kwargs.get("params") or {}
        seen_starts.append(params.get("startAt", 0))
        return FakeResponse(pages[len(seen_starts) - 1])

    monkeypatch.setattr(requests, "get", fake_get)
    tool = KanbanTicketTool()
    monkeypatch.setattr(
        tool,
        "_load_connected_accounts",
        lambda: [
            {
                "provider": "jira",
                "email": "jira-user@example.com",
                "api_token": "token",
                "server_url": "https://jira.example.com",
            }
        ],
    )

    ext = tool._fetch_external_boards()

    assert seen_starts == [0, 1]
    assert ext["jira"] == [
        _jira_board(31, "SP2 board", "SP2", "Snuza Pico 2"),
        _jira_board(36, "STK board", "STK", "SpoorTrack Revamp"),
    ]


def test_find_external_board_matches_jira_project_name(monkeypatch):
    tool = KanbanTicketTool()
    boards = [
        _jira_board(36, "STK board", "STK", "SpoorTrack Revamp"),
        _jira_board(32, "SW board", "SW", "Snuza Website"),
    ]
    monkeypatch.setattr(tool, "_fetch_external_boards", lambda: {"jira": boards, "trello": []})

    by_name, _ = tool._find_external_board("jira", board_name="Spoortrack")
    assert by_name is boards[0]

    by_text, _ = tool._find_external_board("jira", text="move it to the Spoortrack jira board")
    assert by_text is boards[0]

    by_project, _ = tool._find_external_board("jira", board_name="Snuza Website")
    assert by_project is boards[1]


def test_find_external_board_does_not_over_match_ambiguous_project_word(monkeypatch):
    tool = KanbanTicketTool()
    boards = [
        _jira_board(31, "SP2 board", "SP2", "Snuza Pico 2"),
        _jira_board(32, "SW board", "SW", "Snuza Website"),
    ]
    monkeypatch.setattr(tool, "_fetch_external_boards", lambda: {"jira": boards, "trello": []})

    board, _ = tool._find_external_board("jira", text="create a ticket on the Snuza jira board")
    assert board is None


def test_list_boards_includes_jira_project_name_and_local_project_links(monkeypatch):
    get_session = _memory_session(monkeypatch)
    with get_session() as s:
        proj = Project(
            name="Player1Sport",
            provider="jira",
            board_id="35",
            board_name="Player1Sport",
        )
        s.add(proj)
        s.flush()
        local_board = KanbanBoard(
            name="Player1Sport",
            source="database",
            default_project_id=proj.id,
        )
        s.add(local_board)

    tool = KanbanTicketTool()
    monkeypatch.setattr(
        tool,
        "_fetch_external_boards",
        lambda: {
            "jira": [
                _jira_board(36, "STK board", "STK", "SpoorTrack Revamp"),
                _jira_board(35, "Player1Sport", "PLAYER1", "Player1 Sport"),
            ],
            "trello": [],
        },
    )

    result = tool._action_list_boards()

    assert "STK board" in result
    assert "[project: SpoorTrack Revamp (STK)]" in result
    assert "[project linked to Jira board 35]" in result
    assert "[linked project: Player1Sport]" in result


def test_list_jira_tickets_resolves_board_by_project_name(monkeypatch):
    tool = KanbanTicketTool()
    monkeypatch.setattr(
        tool,
        "_fetch_external_boards",
        lambda: {"jira": [_jira_board(36, "STK board", "STK", "SpoorTrack Revamp")], "trello": []},
    )
    fetched = []

    def fake_fetch(provider, board_id):
        fetched.append((provider, board_id))
        return [{"id": "STK-12", "title": "Allow customers to manage devices", "status": "To Do"}]

    monkeypatch.setattr(tool, "_fetch_external_tickets", fake_fetch)

    result = tool._run(action="list_jira_tickets", board_name="Spoortrack")

    assert fetched == [("jira", "36")]
    assert "STK-12" in result
    assert "SpoorTrack Revamp (STK)" in result


def test_open_board_disambiguates_local_spoortrack_and_jira_stk_with_project_label(monkeypatch):
    get_session = _memory_session(monkeypatch)
    with get_session() as s:
        s.add(KanbanBoard(name="Spoortrack", source="database"))

    tool = KanbanTicketTool()
    monkeypatch.setattr(
        tool,
        "_fetch_external_boards",
        lambda: {"jira": [_jira_board(36, "STK board", "STK", "SpoorTrack Revamp")], "trello": []},
    )

    result = tool._run(action="open_board", board_name="Spoortrack", text="open Spoortrack board")

    assert "Multiple boards match" in result
    assert "'Spoortrack' (local" in result
    assert "'STK board' (jira, ID 36) [project: SpoorTrack Revamp (STK)]" in result


def test_get_ticket_surfaces_board_and_project_external_link(monkeypatch):
    get_session = _memory_session(monkeypatch)
    with get_session() as s:
        proj = Project(
            name="Player1Sport",
            provider="jira",
            board_id="35",
            board_name="Player1Sport",
        )
        s.add(proj)
        s.flush()
        board = KanbanBoard(name="Player1Sport", source="database", default_project_id=proj.id)
        s.add(board)
        s.flush()
        bid = board.id
        lane = KanbanLane(board_id=bid, name="Backlog", position=0)
        s.add(lane)
        s.flush()
        ticket = KanbanTicket(
            lane_id=lane.id,
            title="Investigate sync",
            linked_project_id=proj.id,
        )
        s.add(ticket)
        s.flush()
        tid = ticket.id

    tool = KanbanTicketTool()
    result = tool._run(action="get_ticket", ticket_id=tid)

    assert f"Board: Player1Sport (ID {bid}, local)" in result
    assert "Project external board: Jira board 35 (Player1Sport)" in result


def test_create_ticket_fallback_lists_more_than_8_jira_boards_with_project_labels(monkeypatch):
    tool = KanbanTicketTool()
    jira_boards = [
        _jira_board(i, f"B{i} board", f"K{i}", f"Project {i}")
        for i in range(1, 13)
    ] + [_jira_board(36, "STK board", "STK", "SpoorTrack Revamp")]
    monkeypatch.setattr(tool, "_fetch_external_boards", lambda: {"jira": jira_boards, "trello": []})

    result = tool._run(action="create_ticket", text="make a jira ticket for something vague")

    assert "STK board" in result
    assert "[project: SpoorTrack Revamp (STK)]" in result
