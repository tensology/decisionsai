import json
from unittest.mock import Mock

import pytest

from distr.core.agent.tools.integrations.whatsapp_toolkit import WhatsAppToolkitTool
from distr.core.integrations.whatsapp import relay_client as relay


AUDIENCE = ["27820001111@s.whatsapp.net"]


def test_status_requests_discover_tool_without_computer_use():
    from distr.core.agent.tool_intents import forced_tool_names_for_text

    for text in ["Publish my WhatsApp Status", "WhatsApp status", "Post this to WhatsApp", "WhatsApp stories"]:
        assert "whatsapp_toolkit" in forced_tool_names_for_text(text)


def test_status_draft_never_sends_and_exact_approval_is_required(monkeypatch, tmp_path):
    publish = Mock(return_value={"success": True, "state": "submitted", "message_id": "s1"})
    monkeypatch.setattr(relay, "publish_status_via_relay", publish)
    tool = WhatsAppToolkitTool()
    media = tmp_path / "status.jpg"
    media.write_bytes(b"test-image")
    args = dict(text="Exact caption ", audience_jids=AUDIENCE, media_path=str(media))
    draft = json.loads(tool._run(action="draft_status", **args))
    assert draft["audience_jids"] == AUDIENCE
    publish.assert_not_called()
    assert tool._run(action="publish_status", **args).startswith("Not published.")
    approval = dict(approved=True, approval_phrase="send it", approval_token=draft["approval_token"])
    assert tool._run(action="publish_status", **{**args, "text": "changed"}, **approval).startswith("Not published.")
    assert tool._run(action="publish_status", **{**args, "audience_jids": ["27820002222@s.whatsapp.net"]}, **approval).startswith("Not published.")
    media.write_bytes(b"changed-image")
    assert tool._run(action="publish_status", **args, **approval).startswith("Not published.")
    publish.assert_not_called()
    media.write_bytes(b"test-image")
    result = json.loads(tool._run(action="publish_status", **args, **approval))
    assert result["result"]["message_id"] == "s1"
    publish.assert_called_once()
    assert publish.call_args.args[0]["text"] == "Exact caption "


@pytest.mark.parametrize("args", [{}, {"text": "hello"}, {"text": "hello", "audience_jids": ["123@g.us"]},
                                  {"text": "x" * 701, "audience_jids": AUDIENCE}])
def test_invalid_status_drafts(args):
    assert WhatsAppToolkitTool()._run(action="draft_status", **args).startswith("Not published.")


def test_connection_status_action_keeps_existing_meaning(monkeypatch):
    monkeypatch.setattr(relay, "fetch_relay_whatsapp_status", lambda: {"status": "connected"})
    assert json.loads(WhatsAppToolkitTool()._run(action="status")) == {"status": "connected"}


def test_relay_dry_run_and_missing_authorization_never_post(monkeypatch):
    post = Mock()
    monkeypatch.setattr(relay.requests, "post", post)
    monkeypatch.setattr(relay, "whatsapp_send_dry_run_enabled", lambda: True)
    assert relay.publish_status_via_relay({})["state"] == "dry_run"
    monkeypatch.setattr(relay, "whatsapp_send_dry_run_enabled", lambda: False)
    monkeypatch.setattr(relay, "relay_auth_headers", lambda: {})
    assert relay.publish_status_via_relay({})["success"] is False
    post.assert_not_called()


def test_relay_submission_and_uncertain_results_are_distinct(monkeypatch):
    monkeypatch.setattr(relay, "whatsapp_send_dry_run_enabled", lambda: False)
    monkeypatch.setattr(relay, "relay_auth_headers", lambda: {"X-Relay-Internal-Token": "test"})
    monkeypatch.setattr(relay, "relay_api_base", lambda: "http://relay/api/whatsapp")
    payload = {"text": "test", "audience_jids": AUDIENCE}
    response = Mock(status_code=200)
    response.json.return_value = {"success": True, "state": "submitted", "message_id": "s1"}
    post = Mock(return_value=response)
    monkeypatch.setattr(relay.requests, "post", post)
    assert relay.publish_status_via_relay(payload)["delivery_confirmed"] is False
    post.assert_called_once_with("http://relay/api/whatsapp/publish-status", json=payload,
                                 headers={"X-Relay-Internal-Token": "test"}, timeout=65)
    response.json.return_value = {"success": True}
    assert relay.publish_status_via_relay(payload)["state"] == "unknown"
    post.reset_mock()
    post.side_effect = relay.requests.Timeout("timeout")
    assert relay.publish_status_via_relay(payload)["state"] == "unknown"
    post.assert_called_once()
