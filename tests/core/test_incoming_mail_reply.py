from distr.core.incoming.mail_reply import send_incoming_mail_reply


def test_quick_reply_dry_run_gmail_never_sends():
    result = send_incoming_mail_reply(
        channel="gmail",
        to="client@example.com",
        subject="Re: Quote",
        body="Thanks — looking into this.",
        message_id="abc123",
        dry_run=True,
    )
    assert result["success"] is True
    assert result["dry_run"] is True
    assert result["channel"] == "gmail"
    assert result["to"] == "client@example.com"
    assert result["subject"] == "Re: Quote"
    assert result["message_id"] == "abc123"


def test_quick_reply_dry_run_mailshake_alias_maps_to_mailshot():
    result = send_incoming_mail_reply(
        channel="mailshake",
        to="ops@example.com",
        subject="Re: Invoice",
        body="Received, thank you.",
        dry_run=True,
    )
    assert result["success"] is True
    assert result["channel"] == "mailshot"


def test_quick_reply_requires_recipient_and_body():
    try:
        send_incoming_mail_reply(channel="gmail", to="", subject="Re:", body="Hi", dry_run=True)
        assert False, "expected ValueError"
    except ValueError as exc:
        assert "recipient" in str(exc).lower()
    try:
        send_incoming_mail_reply(channel="gmail", to="a@b.com", subject="Re:", body="  ", dry_run=True)
        assert False, "expected ValueError"
    except ValueError as exc:
        assert "reply text" in str(exc).lower()
