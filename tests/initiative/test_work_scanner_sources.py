from distr.core.initiative.work_scanner import (
    _add_board_proposals,
    _connected_work_sources,
    _scan_advanced_work_connectors,
)


def test_connected_work_sources_include_saved_work_connectors():
    settings = {
        "connected_accounts": [
            {"provider": "clickup", "name": "Product", "api_token": "cu-token"},
            {"provider": "monday", "api_token": "mo-token"},
            {"provider": "slack_app", "bot_token": "xoxb-token"},
            {"provider": "discord_bot", "bot_token": "discord-token"},
            {"provider": "openai", "key": "not-a-work-source"},
        ]
    }

    sources = _connected_work_sources(settings)

    by_provider = {source["provider"]: source for source in sources}
    assert by_provider["clickup"]["label"] == "ClickUp"
    assert by_provider["clickup"]["connected"] is True
    assert by_provider["monday"]["label"] == "Monday"
    assert by_provider["monday"]["connected"] is True
    assert by_provider["slack_app"]["label"] == "Slack"
    assert by_provider["discord_bot"]["label"] == "Discord"
    assert "openai" not in by_provider


def test_board_proposals_use_spoken_clean_plural_wording():
    scan = {"proposals": []}
    _add_board_proposals(
        scan,
        {
            "id": 1,
            "name": "Player1Sport",
            "source_lane": "Current",
            "lanes": [
                {
                    "name": "Backlog",
                    "tickets": [{"id": 10}, {"id": 11}, {"id": 12}, {"id": 13}, {"id": 14}],
                },
                {"name": "Current", "tickets": []},
            ],
        },
    )

    description = scan["proposals"][0]["description"]
    assert description == "Player1Sport has 5 backlog items that should move into Current."
    assert "item(s)" not in description


def test_advanced_work_connectors_scan_slack_clickup_and_monday(monkeypatch):
    calls = []

    def fake_get(url, *, headers=None, params=None, timeout=6.0):
        calls.append(("get", url, params or {}))
        if url.endswith("/conversations.list"):
            return {
                "ok": True,
                "channels": [{"id": "C1", "name": "product"}],
            }
        if url.endswith("/conversations.history"):
            return {
                "ok": True,
                "messages": [
                    {"user": "U1", "ts": "123.45", "text": "urgent client bug needs a fix"}
                ],
            }
        if url.endswith("/team"):
            return {"teams": [{"id": "T1", "name": "Workspace"}]}
        if url.endswith("/team/T1/task"):
            return {
                "tasks": [
                    {
                        "id": "cu1",
                        "name": "Fix checkout bug",
                        "status": {"status": "open"},
                        "priority": {"priority": "high"},
                        "url": "https://clickup.example/task/cu1",
                    }
                ]
            }
        if url.endswith("/users/@me/guilds"):
            return [{"id": "G1", "name": "Guild"}]
        if url.endswith("/guilds/G1/channels"):
            return [{"id": "D1", "name": "dev", "type": 0}]
        if url.endswith("/channels/D1/messages"):
            return [
                {
                    "id": "dm1",
                    "content": "client approval is blocked",
                    "author": {"username": "maya"},
                    "timestamp": "2026-06-05T08:10:00Z",
                }
            ]
        return {}

    def fake_post(url, *, headers=None, json_payload=None, timeout=8.0):
        calls.append(("post", url, json_payload or {}))
        return {
            "data": {
                "boards": [
                    {
                        "id": "mb1",
                        "name": "Launch Board",
                        "items_page": {
                            "items": [
                                {
                                    "id": "mi1",
                                    "name": "Client approval blocker",
                                    "updated_at": "2026-06-05T08:00:00Z",
                                    "group": {"title": "Today"},
                                    "column_values": [{"text": "urgent"}],
                                }
                            ]
                        },
                    }
                ]
            }
        }

    monkeypatch.setattr("distr.core.initiative.work_scanner._http_get_json", fake_get)
    monkeypatch.setattr("distr.core.initiative.work_scanner._http_post_json", fake_post)

    scan = {
        "boards": [],
        "proposals": [],
        "messages": {"whatsapp": [], "telegram": [], "email": [], "slack": [], "discord": []},
        "tasks": {"clickup": [], "monday": []},
        "connected_sources": [],
        "unavailable_sources": [],
    }
    _scan_advanced_work_connectors(
        scan,
        {
            "connected_accounts": [
                {"provider": "slack_app", "bot_token": "xoxb-token"},
                {"provider": "discord_bot", "bot_token": "discord-token"},
                {"provider": "clickup", "api_token": "cu-token"},
                {"provider": "monday", "api_token": "mo-token"},
            ]
        },
    )

    assert scan["messages"]["slack"][0]["channel_name"] == "product"
    assert scan["messages"]["discord"][0]["channel_name"] == "dev"
    assert scan["tasks"]["clickup"][0]["name"] == "Fix checkout bug"
    assert scan["tasks"]["monday"][0]["board_name"] == "Launch Board"
    proposal_sources = {proposal["payload"]["source"] for proposal in scan["proposals"]}
    assert {"slack", "discord", "clickup", "monday"}.issubset(proposal_sources)
    assert any(call[1].endswith("/conversations.history") for call in calls)
