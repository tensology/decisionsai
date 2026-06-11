from __future__ import annotations

import base64


def test_get_email_includes_attachment_metadata(monkeypatch):
    from distr.core.agent.services.integrations.google_workspace import GoogleWorkspaceConnector

    connector = GoogleWorkspaceConnector.__new__(GoogleWorkspaceConnector)
    connector.access_token = "token"
    monkeypatch.setattr(connector, "_ensure_valid_token", lambda: True)

    def fake_request(method, url, params=None, **kwargs):
        assert method == "GET"
        assert params == {"format": "full"}
        return {
            "threadId": "thread-1",
            "snippet": "See attached.",
            "labelIds": ["INBOX"],
            "payload": {
                "headers": [
                    {"name": "Subject", "value": "Changes"},
                    {"name": "From", "value": "Julie <julie@example.com>"},
                ],
                "parts": [
                    {
                        "mimeType": "text/plain",
                        "body": {"data": base64.urlsafe_b64encode(b"Body text").decode("ascii")},
                    },
                    {
                        "filename": "changes.pdf",
                        "mimeType": "application/pdf",
                        "body": {"attachmentId": "att-1", "size": 123},
                    },
                ],
            },
        }

    monkeypatch.setattr(connector, "_make_request", fake_request)

    email = connector.get_email("msg-1")

    assert email["id"] == "msg-1"
    assert email["body"] == "Body text"
    assert email["attachments"] == [
        {
            "message_id": "msg-1",
            "attachment_id": "att-1",
            "filename": "changes.pdf",
            "mime_type": "application/pdf",
            "size": 123,
        }
    ]


def test_download_email_attachment_writes_decoded_file(tmp_path, monkeypatch):
    from distr.core.agent.services.integrations.google_workspace import GoogleWorkspaceConnector

    connector = GoogleWorkspaceConnector.__new__(GoogleWorkspaceConnector)
    connector.access_token = "token"
    monkeypatch.setattr(connector, "_ensure_valid_token", lambda: True)

    encoded = base64.urlsafe_b64encode(b"pdf bytes").decode("ascii").rstrip("=")

    def fake_request(method, url, **kwargs):
        assert method == "GET"
        assert "messages/msg-1/attachments/att-1" in url
        return {"data": encoded}

    monkeypatch.setattr(connector, "_make_request", fake_request)

    path = connector.download_email_attachment(
        message_id="msg-1",
        attachment_id="att-1",
        filename="changes.pdf",
        destination_dir=str(tmp_path),
    )

    assert path.endswith("changes.pdf")
    assert (tmp_path / "changes.pdf").read_bytes() == b"pdf bytes"
