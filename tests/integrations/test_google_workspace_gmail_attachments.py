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
    assert email["text_body"] == "Body text"
    assert email["html_body"] == ""
    assert email["attachments"] == [
        {
            "message_id": "msg-1",
            "attachment_id": "att-1",
            "filename": "changes.pdf",
            "mime_type": "application/pdf",
            "size": 123,
            "content_id": "",
            "inline": False,
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


def test_read_email_tool_lists_attachment_metadata(monkeypatch):
    from distr.core.agent.tools.integrations.google_workspace_tool import GoogleWorkspaceTool

    tool = GoogleWorkspaceTool.__new__(GoogleWorkspaceTool)
    connector = type("Conn", (), {})()
    connector.is_connected = lambda: True
    connector.get_email = lambda message_id: {
        "id": message_id,
        "from": "Julie <julie@example.com>",
        "to": "me@example.com",
        "subject": "Order updates",
        "date": "Tue, 9 Jun 2026",
        "body": "See attached.",
        "attachments": [
            {
                "message_id": message_id,
                "attachment_id": "att-1",
                "filename": "changes.pdf",
                "mime_type": "application/pdf",
                "size": 123,
            }
        ],
    }
    object.__setattr__(tool, "connector", connector)
    monkeypatch.setattr(tool, "_ensure_initialized", lambda: None)

    result = tool._run(action="read_email", params={"message_id": "msg-1"})

    assert "changes.pdf" in result
    assert "attachment_id=att-1" in result
    assert "download_email_attachment" in result


def test_download_email_attachments_tool_saves_all_files(tmp_path, monkeypatch):
    from distr.core.agent.tools.integrations.google_workspace_tool import GoogleWorkspaceTool

    tool = GoogleWorkspaceTool.__new__(GoogleWorkspaceTool)
    connector = type("Conn", (), {})()
    connector.is_connected = lambda: True
    connector.get_email = lambda message_id: {
        "id": message_id,
        "attachments": [
            {
                "attachment_id": "att-1",
                "filename": "changes.pdf",
            }
        ],
    }
    connector.download_email_attachment = (
        lambda message_id, attachment_id, filename, destination_dir: str(
            tmp_path / filename
        )
    )
    object.__setattr__(tool, "connector", connector)
    monkeypatch.setattr(tool, "_ensure_initialized", lambda: None)
    monkeypatch.setattr(tool, "_default_downloads_dir", lambda: str(tmp_path))

    result = tool._run(
        action="download_email_attachments",
        params={"message_id": "msg-1", "destination_dir": str(tmp_path)},
    )

    assert "Saved attachment to" in result
    assert str(tmp_path / "changes.pdf") in result


def test_get_email_captures_html_and_inline_cid(monkeypatch):
    from distr.core.agent.services.integrations.google_workspace import GoogleWorkspaceConnector
    import base64

    connector = GoogleWorkspaceConnector.__new__(GoogleWorkspaceConnector)
    connector.access_token = "token"
    monkeypatch.setattr(connector, "_ensure_valid_token", lambda: True)

    def fake_request(method, url, params=None, **kwargs):
        return {
            "threadId": "thread-1",
            "snippet": "Hello",
            "labelIds": ["INBOX"],
            "payload": {
                "headers": [{"name": "Subject", "value": "Hi"}],
                "parts": [
                    {
                        "mimeType": "text/plain",
                        "body": {"data": base64.urlsafe_b64encode(b"Plain hello").decode("ascii")},
                    },
                    {
                        "mimeType": "text/html",
                        "body": {"data": base64.urlsafe_b64encode(b"<p>Hi <img src=\"cid:img1\"></p>").decode("ascii")},
                    },
                    {
                        "mimeType": "image/png",
                        "filename": "",
                        "headers": [{"name": "Content-ID", "value": "<img1>"}],
                        "body": {"attachmentId": "att-inline", "size": 10},
                    },
                ],
            },
        }

    monkeypatch.setattr(connector, "_make_request", fake_request)
    email = connector.get_email("msg-html")
    assert email["text_body"] == "Plain hello"
    assert "cid:img1" in email["html_body"]
    assert email["body"] == "Plain hello"
    assert email["attachments"][0]["content_id"] == "img1"
    assert email["attachments"][0]["inline"] is True


def test_fetch_email_attachment_bytes_decodes(monkeypatch):
    from distr.core.agent.services.integrations.google_workspace import GoogleWorkspaceConnector

    connector = GoogleWorkspaceConnector.__new__(GoogleWorkspaceConnector)
    connector.access_token = "token"
    monkeypatch.setattr(connector, "_ensure_valid_token", lambda: True)
    encoded = base64.urlsafe_b64encode(b"png-bytes").decode("ascii").rstrip("=")

    def fake_request(method, url, params=None, **kwargs):
        if "attachments/att-inline" in url:
            return {"data": encoded}
        return {
            "threadId": "t",
            "snippet": "",
            "labelIds": [],
            "payload": {
                "headers": [],
                "parts": [
                    {
                        "mimeType": "image/png",
                        "filename": "logo.png",
                        "headers": [{"name": "Content-ID", "value": "<img1>"}],
                        "body": {"attachmentId": "att-inline", "size": 9},
                    }
                ],
            },
        }

    monkeypatch.setattr(connector, "_make_request", fake_request)
    data, mime, name = connector.fetch_email_attachment_bytes("msg-1", "", content_id="img1")
    assert data == b"png-bytes"
    assert mime == "image/png"
    assert name.endswith("logo.png") or "logo" in name
