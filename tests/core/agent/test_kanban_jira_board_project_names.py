"""Regression tests: Jira board metadata must survive and be matchable.

Hayley (chat 1381, 2026-11-23) told Paul "There's no Spoortrack board on the
Jira side at all" because every Jira board fetch dropped ``location.projectName``
and only kept the raw board name ("STK board"). These tests pin the fix.
"""

import requests

from distr.core.agent.tools.integrations.kanban_ticket import KanbanTicketTool


def _jira_board(board_id, name, project_key, project_name):
    return {
        "id": str(board_id),
        "name": name,
        "project_key": project_key,
        "project_name": project_name,
        "display_name": f"{project_name} ({project_key})",
    }


def test_fetch_external_boards_preserves_jira_project_metadata(monkeypatch):
    class FakeResponse:
        status_code = 200

        def json(self):
            return {
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
                ]
            }

    monkeypatch.setattr(requests, "get", lambda *args, **kwargs: FakeResponse())
    tool = KanbanTicketTool()
    monkeypatch.setattr(
        tool,
        "_load_connected_accounts",
        lambda: [
            {
                "provider": "jira",
                "email": "paul@snuza.com",
                "api_token": "token",
                "server_url": "https://snuzadev.atlassian.net",
            }
        ],
    )

    ext = tool._fetch_external_boards()

    assert ext["jira"] == [
        {
            "id": "36",
            "name": "STK board",
            "project_key": "STK",
            "project_name": "SpoorTrack Revamp",
            "display_name": "SpoorTrack Revamp (STK)",
        }
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


def test_list_boards_includes_jira_project_name(monkeypatch):
    tool = KanbanTicketTool()
    monkeypatch.setattr(tool, "_get_active_board", lambda auto_recover=False: (None, False))
    monkeypatch.setattr(tool, "_all_boards", lambda: [])
    monkeypatch.setattr(
        tool,
        "_fetch_external_boards",
        lambda: {"jira": [_jira_board(36, "STK board", "STK", "SpoorTrack Revamp")], "trello": []},
    )

    result = tool._action_list_boards()

    assert "STK board" in result
    assert "SpoorTrack Revamp (STK)" in result
