from types import SimpleNamespace

from distr.core.initiative import work_scanner


def _scan():
    return {
        "messages": {"email": []},
        "proposals": [],
        "unavailable_sources": [],
    }


def _patch_inbox(monkeypatch):
    monkeypatch.setattr(
        "distr.core.kanban.jira_intake.fetch_mailshot_intake_messages",
        lambda **kwargs: [],
    )
    monkeypatch.setattr(
        "distr.core.kanban.jira_intake.fetch_gmail_intake_messages",
        lambda **kwargs: [{
            "id": "mail-1",
            "thread_id": "thread-1",
            "from": "Client <client@example.com>",
            "subject": "Checkout failure",
            "snippet": "Please fix the payment bug urgently",
            "body": "Please fix the payment bug urgently",
            "source": "gmail",
        }],
    )


def test_unlinked_email_never_creates_work_proposal(monkeypatch):
    _patch_inbox(monkeypatch)
    monkeypatch.setattr(
        "distr.core.kanban.email_intake_rules.resolve_email_link",
        lambda sender: SimpleNamespace(board_id=None, project_id=None),
    )
    scan = _scan()

    work_scanner._scan_email_uncached(scan)

    assert scan["proposals"] == []


def test_linked_email_creates_one_text_create_execute_proposal(monkeypatch):
    _patch_inbox(monkeypatch)
    monkeypatch.setattr(
        "distr.core.kanban.email_intake_rules.resolve_email_link",
        lambda sender: SimpleNamespace(
            board_id=7,
            project_id=9,
            board_name="Client Delivery",
            project_name="Client Site",
            sender_email="client@example.com",
        ),
    )
    scan = _scan()

    work_scanner._scan_email_uncached(scan)

    proposal = scan["proposals"][0]
    assert proposal["payload"]["linked_board_id"] == 7
    assert proposal["payload"]["linked_project_id"] == 9
    assert proposal["payload"]["approval_flow"] == "linked_intake_create_execute"
    assert proposal["payload"]["notification_format"] == "text"
    assert "execute it end to end" in proposal["telegram_message"].lower()
