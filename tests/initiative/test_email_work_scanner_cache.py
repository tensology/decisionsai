from unittest.mock import MagicMock, patch

from distr.core.initiative import work_scanner


def _empty_scan():
    return {
        "messages": {"email": []},
        "proposals": [],
        "unavailable_sources": [],
    }


def test_email_scan_reuses_short_lived_result_cache():
    work_scanner._email_scan_cache = None
    connector = MagicMock()
    connector.is_connected.return_value = True
    connector.check_inbox.return_value = [
        {
            "id": "mail-1",
            "threadId": "thread-1",
            "subject": "Urgent website bug",
            "snippet": "Please fix the customer checkout issue",
        }
    ]

    with patch(
        "distr.core.agent.services.integrations.google_workspace.GoogleWorkspaceConnector",
        return_value=connector,
    ) as connector_class:
        first = _empty_scan()
        second = _empty_scan()
        work_scanner._scan_email(first)
        work_scanner._scan_email(second)

    assert connector_class.call_count == 1
    assert connector.check_inbox.call_count == 1
    assert second["messages"]["email"] == first["messages"]["email"]
    assert second["proposals"] == first["proposals"]
    work_scanner._email_scan_cache = None
