"""Incoming attachment previews may be framed by the app itself; every other route stays DENY.

2026-11-11 pdf-preview: the PDF preview modal loads the attachment in an <iframe>, which the
global X-Frame-Options: DENY / frame-ancestors 'none' blocked.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from distr.gui.web.server import _allows_same_origin_framing

TOKEN = "test-token-pdf-preview-0123456789abcdef0123456789"


@pytest.mark.parametrize("path", [
    "/api/workflows/studio/incoming/mail/attachment",
    "/api/workflows/studio/incoming/proposals/attachment",
    "/api/workflows/studio/incoming/proposals/files/abc123",
])
def test_preview_routes_allow_same_origin_framing(path):
    assert _allows_same_origin_framing(path)


@pytest.mark.parametrize("path", [
    "/",
    "/development/incoming/",
    "/api/workflows/studio/incoming",
    "/api/workflows/studio/incoming/mail/attachment/extra",
    "/api/workflows/studio/incoming/mail/attachmentx",
    "/api/workflows/studio/incoming/proposals/attachments",
    "/api/workflows/studio/incoming/proposals/files/",
    "/api/workflows/studio/incoming/proposals/files/ensure",
    "/api/workflows/studio/incoming/proposals/files/a/b",
    "/api/workflows/studio/incoming/proposals/details",
    "/workflows/studio/incoming/mail/attachment",
])
def test_other_routes_do_not_allow_framing(path):
    assert not _allows_same_origin_framing(path)


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setenv("DECISIONSAI_INTERNAL_API_TOKEN", TOKEN)
    from distr.gui.web.server import create_app
    return TestClient(create_app(), client=("127.0.0.1", 50000))


def test_live_headers_on_preview_route_vs_normal_routes(client):
    # No query params: the endpoint answers 422 before touching Gmail/Mailshot.
    preview = client.get("/api/workflows/studio/incoming/mail/attachment")
    assert preview.status_code == 422
    assert preview.headers["x-frame-options"] == "SAMEORIGIN"
    assert "frame-ancestors 'self';" in preview.headers["content-security-policy"]
    assert "frame-ancestors 'none'" not in preview.headers["content-security-policy"]

    for path in ("/api/workflows/studio/incoming/mail/attachmentx",
                 "/api/workflows/studio/incoming/proposals/files/ensure",
                 "/api/kanban"):
        normal = client.get(path)
        assert normal.headers["x-frame-options"] == "DENY", path
        assert normal.headers["content-security-policy"].endswith("frame-ancestors 'none';"), path
    # The rest of the policy is identical on both.
    strip = lambda value: value.rsplit("frame-ancestors", 1)[0]
    assert strip(preview.headers["content-security-policy"]) == strip(normal.headers["content-security-policy"])


def test_mail_preview_url_carries_real_filename():
    from urllib.parse import parse_qs, urlparse
    from distr.core.incoming.service import _mail_attachment_preview_url

    url = _mail_attachment_preview_url(
        {"attachment_id": "ANG-1", "filename": "Crystal_Logic_Service_Level_Agreement_Draft_v0.6.pdf"},
        message_id="1a1cab76218b6393",
    )
    query = parse_qs(urlparse(url).query)
    assert query["filename"] == ["Crystal_Logic_Service_Level_Agreement_Draft_v0.6.pdf"]
    assert query["message_id"] == ["1a1cab76218b6393"] and query["attachment_id"] == ["ANG-1"]
    assert "filename" not in parse_qs(urlparse(_mail_attachment_preview_url({"attachment_id": "x"}, message_id="m")).query)


def test_inline_content_disposition_encodes_non_ascii():
    from distr.gui.web.routes.development.incoming import _inline_content_disposition

    plain = _inline_content_disposition("Crystal_Logic_Service_Level_Agreement_Draft_v0.6.pdf")
    assert plain == 'inline; filename="Crystal_Logic_Service_Level_Agreement_Draft_v0.6.pdf"'
    fancy = _inline_content_disposition("Überblick – Angebot.pdf")
    assert fancy == "inline; filename=\"berblick  Angebot.pdf\"; filename*=UTF-8''%C3%9Cberblick%20%E2%80%93%20Angebot.pdf"
    fancy.encode("latin-1")  # Starlette header values must be latin-1 encodable.
    assert _inline_content_disposition('a"b\\c\r\n.pdf') == 'inline; filename="abc.pdf"'


@pytest.mark.parametrize("resolved,hint,expected", [
    ("attachment", "Crystal_Logic_Service_Level_Agreement_Draft_v0.6.pdf", "Crystal_Logic_Service_Level_Agreement_Draft_v0.6.pdf"),
    ("", "../../etc/Report.pdf", "Report.pdf"),
    ("Attachment", "", "attachment"),
    ("Real name.pdf", "Other.pdf", "Real name.pdf"),
    ("attachment", 'quo"te.pdf', "quote.pdf"),
])
def test_preview_filename_prefers_link_name_over_generic_default(resolved, hint, expected):
    from distr.gui.web.routes.development.incoming import _preview_filename

    assert _preview_filename(resolved, hint) == expected


def test_gmail_preview_serves_link_filename_when_lookup_is_generic(client, monkeypatch):
    import distr.core.agent.services.integrations.google_workspace as gw

    class FakeConnector:
        def is_connected(self):
            return True

        def fetch_email_attachment_bytes(self, message_id, attachment_id, *, content_id=""):
            assert (message_id, attachment_id) == ("1a1cab76218b6393", "ANG-1")
            return b"%PDF-1.7\n", "application/pdf", "attachment"

    monkeypatch.setattr(gw, "GoogleWorkspaceConnector", FakeConnector)
    response = client.get("/api/workflows/studio/incoming/mail/attachment", params={
        "message_id": "1a1cab76218b6393", "attachment_id": "ANG-1",
        "filename": "Crystal_Logic_Service_Level_Agreement_Draft_v0.6.pdf",
    })
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert response.headers["content-disposition"] == 'inline; filename="Crystal_Logic_Service_Level_Agreement_Draft_v0.6.pdf"'
    assert response.headers["x-frame-options"] == "SAMEORIGIN"
