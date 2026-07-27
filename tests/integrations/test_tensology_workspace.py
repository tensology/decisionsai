import json
from unittest.mock import MagicMock, patch

from distr.core.agent.tools.integrations.tensology_workspace import TensologyWorkspaceTool
from distr.core.tensology_client import TensologyClient


def test_tensology_client_sends_scoped_headers_and_unwraps_data():
    response = MagicMock()
    response.__enter__.return_value = response
    response.status = 200
    response.read.return_value = json.dumps({"data": {"mail": True}, "error": None}).encode()
    client = TensologyClient("https://example.test", "tns_decisionsai_secret")

    with patch("urllib.request.urlopen", return_value=response) as urlopen:
        assert client.get("capabilities") == {"mail": True}

    request = urlopen.call_args.args[0]
    assert request.full_url == "https://example.test/api/integrations/v1/capabilities"
    assert request.get_header("Authorization") == "Bearer tns_decisionsai_secret"


def test_send_mail_stops_before_api_without_explicit_approval():
    fake_client = MagicMock()
    with patch(
        "distr.core.agent.tools.integrations.tensology_workspace.configured_tensology_client",
        return_value=fake_client,
    ):
        result = TensologyWorkspaceTool()._run(
            action="send_mail",
            params={"to": ["person@example.com"], "subject": "Hello", "text": "Body"},
            approved=False,
        )
    assert "Approval required" in result
    fake_client.post.assert_not_called()


def test_draft_is_written_without_send_approval():
    fake_client = MagicMock()
    fake_client.post.return_value = {"id": "draft-1"}
    with patch(
        "distr.core.agent.tools.integrations.tensology_workspace.configured_tensology_client",
        return_value=fake_client,
    ):
        result = TensologyWorkspaceTool()._run(
            action="save_draft",
            params={"to": ["person@example.com"], "subject": "Hello", "text": "Body"},
            idempotency_key="draft-test",
        )
    assert json.loads(result)["id"] == "draft-1"
    fake_client.post.assert_called_once_with(
        "mail/drafts",
        {"to": ["person@example.com"], "subject": "Hello", "text": "Body"},
        idempotency_key="draft-test",
    )


def test_invoice_creation_stops_before_api_without_explicit_approval():
    fake_client = MagicMock()
    with patch(
        "distr.core.agent.tools.integrations.tensology_workspace.configured_tensology_client",
        return_value=fake_client,
    ):
        result = TensologyWorkspaceTool()._run(
            action="create_invoice",
            params={"invoice_number": "TEST-001", "customer_id": 1},
            approved=False,
        )
    assert "Approval required" in result
    fake_client.post.assert_not_called()
