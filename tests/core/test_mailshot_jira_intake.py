from distr.core.kanban import jira_intake as intake
from distr.core.kanban import work_ops


def test_mailshot_message_normalizes_to_intake_shape():
    msg = intake.mailshot_message_to_intake_dict({
        "_id": "email-abc",
        "from": '"msnyman (Jira)" <jira@snuzadev.atlassian.net>',
        "subject": "[JIRA] (PLAYER1-69) assigned you",
        "preview": "PLAYER1-69 on board",
        "date": "2026-08-11T20:20:51.000Z",
    })
    assert msg["id"] == "email-abc"
    assert "jira@snuzadev.atlassian.net" in msg["from"]
    assert "PLAYER1-69" in msg["subject"]
    assert msg["source"] == "mailshot"
    assert intake.is_jira_notification_email(from_addr=msg["from"], subject=msg["subject"])
    assert intake.collect_jira_keys_from_emails([msg]) == ["PLAYER1-69"]


def test_work_intake_prefers_mailshot_over_gmail(monkeypatch):
    mailshot_msgs = [
        {
            "id": "m1",
            "from": "jira@acme.atlassian.net",
            "subject": "(ACME-9) assigned you",
            "snippet": "ACME-9",
            "body": "",
            "source": "mailshot",
        }
    ]
    calls = {"gmail": 0}

    monkeypatch.setattr(
        intake,
        "fetch_mailshot_intake_messages",
        lambda **kwargs: mailshot_msgs,
    )

    def boom_gmail(**kwargs):
        calls["gmail"] += 1
        raise AssertionError("Gmail should not be consulted when Mailshot has Jira mail")

    monkeypatch.setattr(intake, "fetch_gmail_intake_messages", boom_gmail)
    monkeypatch.setattr(work_ops, "_in_use_board_id", lambda: 11)

    captured = {}

    def fake_run(**kwargs):
        captured.update(kwargs)
        return {"created": [{"id": 1}], "reason": "ok", "notified": False}

    monkeypatch.setattr(intake, "run_jira_morning_intake", fake_run)
    # work_ops imports run_jira_morning_intake inside the function; patch module attr used after import
    monkeypatch.setattr(
        "distr.core.kanban.jira_intake.run_jira_morning_intake",
        fake_run,
    )

    result = work_ops.work_intake(board_id=11, notify=False)
    assert result["mail_source"] == "mailshot"
    assert result["success"] is True
    assert captured["messages"] == mailshot_msgs
    assert calls["gmail"] == 0
