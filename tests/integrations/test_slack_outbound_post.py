"""Slack ``chat.postMessage`` helper (no network when mocked)."""

from __future__ import annotations

import json

import pytest

from distr.core.integrations.slack.outbound import post_slack_chat_message


def test_post_slack_chat_message_encodes_json(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    class Resp:
        def __enter__(self) -> Resp:
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def read(self) -> bytes:
            return b'{"ok":true}'

    def fake_urlopen(req: object, timeout: float = 45) -> Resp:
        captured["req"] = req
        return Resp()

    monkeypatch.setattr(
        "distr.core.integrations.slack.outbound.urllib.request.urlopen",
        fake_urlopen,
    )

    post_slack_chat_message(bot_token="xoxb-test", channel_id="C01234567", text="hello slack")

    req = captured["req"]
    assert req is not None
    data = json.loads(getattr(req, "data").decode("utf-8"))
    assert data["channel"] == "C01234567"
    assert data["text"] == "hello slack"


def test_post_slack_chat_message_raises_on_slack_error(monkeypatch: pytest.MonkeyPatch) -> None:
    class Resp:
        def __enter__(self) -> Resp:
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def read(self) -> bytes:
            return b'{"ok":false,"error":"channel_not_found"}'

    monkeypatch.setattr(
        "distr.core.integrations.slack.outbound.urllib.request.urlopen",
        lambda req, timeout=45: Resp(),
    )

    with pytest.raises(RuntimeError, match="channel_not_found"):
        post_slack_chat_message(bot_token="x", channel_id="C", text="x")
