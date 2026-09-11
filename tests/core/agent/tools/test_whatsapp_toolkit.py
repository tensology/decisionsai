from __future__ import annotations

from distr.core.agent.tools.integrations.whatsapp_toolkit import (
    WhatsAppToolkitTool,
    _approval_is_explicit,
    _approval_token,
)


def test_approval_phrases_are_explicit_and_narrow():
    assert _approval_is_explicit("approved")
    assert _approval_is_explicit("go ahead")
    assert _approval_is_explicit("SEND IT")
    assert not _approval_is_explicit("looks good")
    assert not _approval_is_explicit("")


def test_approval_token_changes_when_exact_draft_changes():
    first = _approval_token("27820001111", "Thanks, I will check this.")
    second = _approval_token("27820001111", "Thanks, I will check this!")
    assert len(first) == 16
    assert first != second


def test_send_requires_approval_before_contact_resolution(monkeypatch):
    tool = WhatsAppToolkitTool()
    called = {"resolve": False}

    def fail_resolve(*args, **kwargs):
        called["resolve"] = True
        raise AssertionError("contact resolution should not happen before approval")

    monkeypatch.setattr(
        "distr.core.agent.tools.integrations.whatsapp_toolkit._resolve_contact",
        fail_resolve,
    )
    result = tool._run(action="send", query="Greg", approved=False, approval_phrase="")
    assert result.startswith("Not sent.")
    assert called["resolve"] is False


def test_tool_is_registered():
    from distr.core.agent.tools.loader import TOOL_REGISTRY, TOOL_DESCRIPTIONS

    assert "WhatsAppToolkitTool" in TOOL_REGISTRY
    assert "WhatsAppToolkitTool" in TOOL_DESCRIPTIONS
