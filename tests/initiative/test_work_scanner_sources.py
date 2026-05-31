from distr.core.initiative.work_scanner import _connected_work_sources


def test_connected_work_sources_include_saved_work_connectors():
    settings = {
        "connected_accounts": [
            {"provider": "clickup", "name": "Product", "api_token": "cu-token"},
            {"provider": "monday", "api_token": "mo-token"},
            {"provider": "slack_app", "bot_token": "xoxb-token"},
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
    assert "openai" not in by_provider
