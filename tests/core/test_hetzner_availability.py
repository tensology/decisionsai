from __future__ import annotations

import base64
import copy
import json
from email import message_from_bytes
from pathlib import Path
from unittest.mock import Mock

import pytest
import requests

from distr.core.automation import hetzner_availability as monitor


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.delenv("HCLOUD_TOKEN", raising=False)
    token = tmp_path / "token"
    token.write_text("test-read-only-token")
    payload = {"server_types": [{
        "name": "cx43", "cores": 8, "memory": 16, "disk": 160,
        "architecture": "x86", "cpu_type": "shared",
        "locations": [{"id": 1, "name": "fsn1", "available": True},
                      {"id": 2, "name": "nbg1", "available": False}],
    }]}
    prices = {"pricing": {
        "currency": "EUR", "server_backup": {"percentage": "20.00"},
        "server_types": [{"name": "cx43", "prices": [
            {"location": "fsn1", "price_monthly": {"net": "9.49", "gross": "11.2931"}},
        ]}],
        "primary_ips": [{"type": "ipv4", "prices": [
            {"location": "fsn1", "price_monthly": {"net": "0.50"}},
        ]}],
    }}
    get = Mock(side_effect=lambda endpoint, token: copy.deepcopy(prices if endpoint == "/pricing" else payload))
    client = Mock()
    mail = Mock(return_value={"id": "sent-123"})
    connect = Mock(return_value=client)
    pause = Mock()
    monkeypatch.setattr(monitor, "_get", get)
    monkeypatch.setattr(monitor, "_connect_mail", connect)
    monkeypatch.setattr(monitor, "_mail_request", mail)
    monkeypatch.setattr(monitor, "_pause", pause)
    args = {"automation_id": "auto_13", "recipient": "owner@example.test",
            "token_file": str(token), "state_dir": str(tmp_path / "state")}
    return args, payload, prices, get, mail, connect, pause


def run(setup):
    return monitor.HetznerAvailabilityTool()._run(**setup[0])


def test_available_sends_one_priced_email_and_pauses(setup):
    result = run(setup)
    args, _, _, get, mail, _, pause = setup
    assert result["status"] == "sent"
    assert get.call_count == 2
    pause.assert_called_once_with("auto_13")
    call = mail.call_args
    assert call.args[1:] == ("POST", monitor.GMAIL + "/send")
    message = message_from_bytes(base64.urlsafe_b64decode(call.kwargs["json"]["raw"]))
    assert message["To"] == "owner@example.test"
    body = message.get_payload(decode=True).decode()
    assert "Falkenstein (FSN1)" in body
    assert "EUR 9.49/month" in body and "EUR 0.50/month" in body
    assert "EUR 11.49/month" in body  # (9.49 + .50) * 1.15
    assert "EUR 13.67/month" in body  # (9.49 * 1.20 + .50) * 1.15
    assert "https://console.hetzner.com/projects" in body
    run(setup)
    assert mail.call_count == 1
    assert get.call_count == 2
    state = json.loads((Path(args["state_dir"]) / "state.json").read_text())
    assert state["sent_message_id"] == "sent-123"


@pytest.mark.parametrize("availability", [False, None, "true", 1])
def test_unavailable_or_nonboolean_stock_is_quiet_and_never_connects_mail(setup, availability):
    setup[1]["server_types"][0]["locations"][0]["available"] = availability
    result = run(setup)
    assert result["status"] == "unavailable" and not result["notify"]
    assert setup[3].call_count == 1
    setup[5].assert_not_called()
    setup[6].assert_not_called()


@pytest.mark.parametrize("change", ["wrong_plan", "wrong_specs", "foreign_stock", "legacy_only"])
def test_never_alerts_on_similar_plan_wrong_specs_or_legacy_stock(setup, change):
    server = setup[1]["server_types"][0]
    if change == "wrong_plan":
        server["name"] = "ccx43"
    elif change == "wrong_specs":
        server["architecture"] = "arm"
    elif change == "foreign_stock":
        server["locations"][0]["name"] = "hel1"
    else:
        del server["locations"]
        setup[1]["datacenters"] = [{"server_types": {"available": [116]}}]
    assert not run(setup)["notify"]
    setup[5].assert_not_called()


def test_missing_token_and_network_failures_do_not_launch_agent_or_notify(setup):
    from pathlib import Path

    Path(setup[0]["token_file"]).unlink()
    assert run(setup)["status"] == "token_required"
    setup[3].assert_not_called()
    Path(setup[0]["token_file"]).write_text("token")
    setup[3].side_effect = requests.Timeout()
    assert run(setup)["status"] == "unconfirmed"
    setup[5].assert_not_called()


def test_incomplete_pricing_does_not_send_or_pause(setup):
    setup[2]["pricing"]["primary_ips"] = []
    assert run(setup)["status"] == "unconfirmed"
    setup[5].assert_not_called()
    setup[6].assert_not_called()


@pytest.mark.parametrize("bad_value", ["NaN", "Infinity", "not-a-price"])
def test_invalid_prices_remain_quiet(setup, bad_value):
    setup[2]["pricing"]["server_backup"]["percentage"] = bad_value
    result = run(setup)
    assert result["status"] == "unconfirmed" and not result["notify"]
    setup[5].assert_not_called()
    setup[6].assert_not_called()


def test_malformed_location_remains_quiet(setup):
    setup[1]["server_types"][0]["locations"] = [None]
    result = run(setup)
    assert result["status"] == "unconfirmed" and not result["notify"]
    setup[5].assert_not_called()


def test_google_reconnection_notice_once_then_recovers(setup):
    setup[5].side_effect = RuntimeError("expired credentials")
    assert run(setup)["notify"] is True
    assert run(setup)["notify"] is False
    setup[4].assert_not_called()
    setup[6].assert_not_called()
    setup[5].side_effect = None
    assert run(setup)["status"] == "sent"


def test_ambiguous_send_reconciles_without_duplicate_even_if_stock_disappears(setup):
    setup[4].return_value = None
    assert run(setup)["status"] == "mail_action_required"
    setup[1]["server_types"] = []
    setup[4].return_value = {"messages": [{"id": "recovered"}]}
    assert run(setup)["status"] == "sent"
    assert [c.args[1] for c in setup[4].call_args_list] == ["POST", "GET"]
    setup[6].assert_called_once()


def test_unresolved_send_does_not_retry_or_repeat_notification(setup):
    setup[4].return_value = None
    assert run(setup)["notify"] is True
    setup[4].return_value = {"messages": []}
    assert run(setup)["notify"] is False
    assert [c.args[1] for c in setup[4].call_args_list] == ["POST", "GET"]
    setup[6].assert_not_called()


def test_definite_gmail_rejection_retries_after_reconnection(setup):
    response = requests.Response()
    response.status_code = 403
    setup[4].side_effect = requests.HTTPError(response=response)
    assert run(setup)["notify"] is True
    assert run(setup)["notify"] is False
    setup[4].side_effect = None
    assert run(setup)["status"] == "sent"


def test_mail_transport_has_deadlines_and_does_not_follow_redirects(monkeypatch):
    request = Mock()
    monkeypatch.setattr(monitor.requests, "request", request)
    monitor._mail_request(Mock(access_token="secret"), "GET", monitor.GMAIL)
    assert request.call_args.kwargs["timeout"] == (3, 8)
    assert request.call_args.kwargs["allow_redirects"] is False


def test_pause_failure_cannot_cause_duplicate_email(setup):
    setup[6].side_effect = RuntimeError("database busy")
    with pytest.raises(RuntimeError):
        run(setup)
    setup[6].side_effect = None
    assert run(setup)["status"] == "sent"
    assert setup[4].call_count == 1


def test_file_lock_skips_concurrent_check(setup):
    import fcntl
    from pathlib import Path

    folder = Path(setup[0]["state_dir"])
    folder.mkdir()
    with (folder / "check.lock").open("a") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert run(setup)["status"] == "busy"
    setup[3].assert_not_called()


def test_corrupt_state_never_resets_send_protection(setup):
    from pathlib import Path

    folder = Path(setup[0]["state_dir"])
    folder.mkdir()
    (folder / "state.json").write_text("broken")
    with pytest.raises(ValueError):
        run(setup)
    setup[3].assert_not_called()


def test_official_requests_are_bounded_and_do_not_follow_redirects(monkeypatch):
    get = Mock()
    monkeypatch.setattr(monitor.requests, "get", get)
    monitor._get("/server_types?name=cx43", "secret")
    assert get.call_args.args == (monitor.API + "/server_types?name=cx43",)
    assert get.call_args.kwargs["timeout"] == (3, 8)
    assert get.call_args.kwargs["allow_redirects"] is False


def test_tool_registry_routes_without_agent(setup):
    from distr.core.automation_tool_runner import run_automation_tool

    setup[1]["server_types"] = []
    result = run_automation_tool("hetzner_availability", setup[0])
    assert result["success"] and result["raw"]["status"] == "unavailable"


def test_owned_thread_dispatch_uses_tool_instead_of_development_llm(monkeypatch):
    from distr.core import automation_orchestrator as orch

    monkeypatch.setattr(orch, "ensure_automation_thread", lambda _: 158)
    monkeypatch.setattr("distr.core.workflow.development_control.resume_thread_time", lambda _: None)
    tool = Mock(return_value={"status": "running"})
    llm = Mock(side_effect=AssertionError("must never launch Pi/Ollama"))
    monkeypatch.setattr(orch, "_dispatch_tool_bound_automation", tool)
    monkeypatch.setattr(orch, "_dispatch_development_automation", llm)
    orch.dispatch_automation_to_current_chat({
        "id": "auto_13", "record_id": 13, "thread_chat_id": 158,
        "action_config": {"tool": "hetzner_availability"},
    }, manual=False)
    tool.assert_called_once()
    llm.assert_not_called()
