from distr.core.agent.tools.integrations.kanban_ticket import KanbanTicketTool


def test_create_trello_card_uses_remote_board_and_list(monkeypatch):
    created_calls = []

    class FakeTrelloAPI:
        def __init__(self, api_key, api_token):
            assert api_key == "key"
            assert api_token == "token"

        def get_lists(self, board_id):
            assert board_id == "board-1"
            return [
                {"id": "list-backlog", "name": "Backlog"},
                {"id": "list-doing", "name": "Doing"},
            ]

        def create_card(self, list_id, name, desc):
            created_calls.append({"list_id": list_id, "name": name, "desc": desc})
            return {
                "id": "card-1",
                "name": name,
                "url": "https://trello.example/cards/card-1",
            }

    monkeypatch.setattr(
        "distr.core.integrations.trello_api.TrelloAPI",
        FakeTrelloAPI,
    )

    tool = KanbanTicketTool()
    monkeypatch.setattr(
        tool,
        "_load_connected_accounts",
        lambda: [{"provider": "trello", "api_key": "key", "api_token": "token", "is_valid": True}],
    )
    monkeypatch.setattr(
        tool,
        "_fetch_external_boards",
        lambda: {"trello": [{"id": "board-1", "name": "Product"}], "jira": []},
    )

    result = tool._run(
        action="create_ticket",
        text="make a Trello card for fixing the flaky workflow validation",
        board_name="Product",
        lane_name="Doing",
        title="Fix workflow validation",
        description="Workflow validation gets stuck and needs a regression test.",
    )

    assert created_calls[0]["list_id"] == "list-doing"
    assert created_calls[0]["name"] == "Fix workflow validation"
    assert created_calls[0]["desc"].startswith("Workflow validation gets stuck and needs a regression test.")
    assert "## Recommended Skills" in created_calls[0]["desc"]
    assert "Created Trello card" in result
    assert "https://trello.example/cards/card-1" in result


def test_create_jira_issue_uses_remote_board_project_key(monkeypatch):
    post_calls = []

    class FakeResponse:
        status_code = 201
        text = ""

        def json(self):
            return {"id": "10001", "key": "DAI-42"}

    def fake_post(url, **kwargs):
        post_calls.append({"url": url, **kwargs})
        return FakeResponse()

    import requests

    monkeypatch.setattr(requests, "post", fake_post)

    tool = KanbanTicketTool()
    monkeypatch.setattr(
        tool,
        "_load_connected_accounts",
        lambda: [
            {
                "provider": "jira",
                "server_url": "https://decisions.atlassian.net",
                "email": "dev@example.com",
                "api_token": "token",
                "is_valid": True,
            }
        ],
    )
    monkeypatch.setattr(
        tool,
        "_fetch_external_boards",
        lambda: {"trello": [], "jira": [{"id": "77", "name": "Decisions", "project_key": "DAI"}]},
    )

    result = tool._run(
        action="create_ticket",
        text="create a Jira ticket for Decisions about TTS cutting out",
        board_name="Decisions",
        title="Fix TTS cutouts",
        description="Audio sometimes cuts out when swapping devices; validate the UI playback state.",
    )

    assert len(post_calls) == 1
    assert post_calls[0]["url"] == "https://decisions.atlassian.net/rest/api/3/issue"
    payload = post_calls[0]["json"]
    jira_description = payload["fields"]["description"]["content"][0]["content"][0]["text"]
    assert payload["fields"]["project"]["key"] == "DAI"
    assert payload["fields"]["summary"] == "Fix TTS cutouts"
    assert payload["fields"]["issuetype"]["name"] == "Task"
    assert "## Recommended Skills" in jira_description
    assert "Created Jira issue" in result
    assert "DAI-42" in result


def test_remote_ticket_request_asks_for_board_when_multiple_matchless(monkeypatch):
    tool = KanbanTicketTool()
    monkeypatch.setattr(
        tool,
        "_load_connected_accounts",
        lambda: [{"provider": "trello", "api_key": "key", "api_token": "token", "is_valid": True}],
    )
    monkeypatch.setattr(
        tool,
        "_fetch_external_boards",
        lambda: {
            "trello": [
                {"id": "board-1", "name": "Product"},
                {"id": "board-2", "name": "Ops"},
            ],
            "jira": [],
        },
    )

    result = tool._run(
        action="create_ticket",
        text="make a Trello card for fixing the flaky workflow validation",
        title="Fix workflow validation",
    )

    assert "Tell me which Trello board to use" in result
    assert "'Product' (ID board-1)" in result
    assert "'Ops' (ID board-2)" in result


def test_update_move_and_comment_trello_card(monkeypatch):
    calls = []

    class FakeTrelloAPI:
        def __init__(self, api_key, api_token):
            pass

        def update_card(self, card_id, name=None, desc=None, idList=None, **kwargs):
            calls.append(("update", card_id, name, desc, idList))
            return {"id": card_id, "url": f"https://trello.example/cards/{card_id}"}

        def get_lists(self, board_id):
            return [{"id": "todo-list", "name": "To Do"}, {"id": "done-list", "name": "Done"}]

        def move_card(self, card_id, list_id):
            calls.append(("move", card_id, list_id))
            return {"id": card_id, "idList": list_id}

        def add_comment_to_card(self, card_id, text):
            calls.append(("comment", card_id, text))
            return {"id": "comment-1"}

    monkeypatch.setattr("distr.core.integrations.trello_api.TrelloAPI", FakeTrelloAPI)

    tool = KanbanTicketTool()
    monkeypatch.setattr(
        tool,
        "_load_connected_accounts",
        lambda: [{"provider": "trello", "api_key": "key", "api_token": "token", "is_valid": True}],
    )
    monkeypatch.setattr(
        tool,
        "_fetch_external_boards",
        lambda: {"trello": [{"id": "board-1", "name": "Product"}], "jira": []},
    )

    update_result = tool._run(
        action="update_external_ticket",
        text="update this Trello card",
        external_item_id="card-1",
        title="New title",
        description="New description",
    )
    move_result = tool._run(
        action="move_external_ticket",
        text="move this Trello card",
        board_name="Product",
        external_item_id="card-1",
        lane_name="Done",
    )
    comment_result = tool._run(
        action="comment_external_ticket",
        text="comment on this Trello card",
        external_item_id="card-1",
        comment_text="Looks good.",
    )

    assert calls == [
        ("update", "card-1", "New title", "New description", None),
        ("move", "card-1", "done-list"),
        ("comment", "card-1", "Looks good."),
    ]
    assert "Updated Trello card card-1" in update_result
    assert "Moved Trello card card-1" in move_result
    assert "Commented on Trello card card-1" in comment_result


def test_update_transition_and_comment_jira_issue(monkeypatch):
    calls = []

    class FakeResponse:
        def __init__(self, status_code=204, payload=None, text=""):
            self.status_code = status_code
            self._payload = payload or {}
            self.text = text

        def json(self):
            return self._payload

    def fake_put(url, **kwargs):
        calls.append(("put", url, kwargs.get("json")))
        return FakeResponse(204)

    def fake_get(url, **kwargs):
        calls.append(("get", url, None))
        return FakeResponse(
            200,
            {
                "transitions": [
                    {"id": "11", "name": "In Progress"},
                    {"id": "21", "name": "Done"},
                ]
            },
        )

    def fake_post(url, **kwargs):
        calls.append(("post", url, kwargs.get("json")))
        return FakeResponse(204)

    import requests

    monkeypatch.setattr(requests, "put", fake_put)
    monkeypatch.setattr(requests, "get", fake_get)
    monkeypatch.setattr(requests, "post", fake_post)

    tool = KanbanTicketTool()
    monkeypatch.setattr(
        tool,
        "_load_connected_accounts",
        lambda: [
            {
                "provider": "jira",
                "server_url": "https://decisions.atlassian.net",
                "email": "dev@example.com",
                "api_token": "token",
                "is_valid": True,
            }
        ],
    )

    update_result = tool._run(
        action="update_external_ticket",
        text="update Jira issue DAI-42",
        external_issue_key="DAI-42",
        title="New issue title",
        description="New issue body",
    )
    move_result = tool._run(
        action="move_external_ticket",
        text="move Jira issue DAI-42",
        external_issue_key="DAI-42",
        lane_name="Done",
    )
    comment_result = tool._run(
        action="comment_external_ticket",
        text="comment on Jira issue DAI-42",
        external_issue_key="DAI-42",
        comment_text="Validated in staging.",
    )

    assert calls[0][0] == "put"
    assert calls[0][1] == "https://decisions.atlassian.net/rest/api/3/issue/DAI-42"
    assert calls[0][2]["fields"]["summary"] == "New issue title"
    assert calls[1] == (
        "get",
        "https://decisions.atlassian.net/rest/api/3/issue/DAI-42/transitions",
        None,
    )
    assert calls[2] == (
        "post",
        "https://decisions.atlassian.net/rest/api/3/issue/DAI-42/transitions",
        {"transition": {"id": "21"}},
    )
    assert calls[3][0] == "post"
    assert calls[3][1] == "https://decisions.atlassian.net/rest/api/3/issue/DAI-42/comment"
    assert "Updated Jira issue DAI-42" in update_result
    assert "Transitioned Jira issue DAI-42" in move_result
    assert "Commented on Jira issue DAI-42" in comment_result
