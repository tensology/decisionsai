"""TASK 16–17 — Discord / Slack bridges call MessageBus without optional SDKs."""

from __future__ import annotations

from pathlib import Path

import pytest

from distr.core.integrations.bus import IntegrationMessageBus
from distr.core.integrations.discord.bridge import (
    discord_message_to_incoming,
    route_discord_inbound_to_agent,
)
from distr.core.integrations.slack.bridge import slack_event_to_incoming, route_slack_inbound_to_agent


def test_discord_message_to_incoming_shape() -> None:
    m = discord_message_to_incoming(
        channel_id="123",
        author_id="u1",
        content="ping",
        attachment_paths=["/x.jpg"],
        raw={"ts": "1"},
        speak=False,
    )
    assert m.platform == "discord"
    assert m.thread_id == "123"
    assert m.sender_id == "u1"
    assert m.text == "ping"
    assert m.attachments == ["/x.jpg"]
    assert m.raw["ts"] == "1"
    assert m.speak is False


@pytest.mark.parametrize(
    "route_fn,kw",
    [
        (
            route_discord_inbound_to_agent,
            {
                "channel_id": "ch",
                "author_id": None,
                "content": "hi",
                "attachment_paths": None,
            },
        ),
        (
            route_slack_inbound_to_agent,
            {"channel_id": "ch", "user_id": "U1", "text": "hi"},
        ),
    ],
)
def test_route_helpers_invoke_sink(
    route_fn,
    kw: dict,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    bus = IntegrationMessageBus(mapping_path=tmp_path / "bridge.json")
    seen: list[tuple] = []

    def fake_get_bus():
        return bus

    monkeypatch.setattr(
        "distr.core.integrations.discord.bridge.get_integration_message_bus",
        fake_get_bus,
    )
    monkeypatch.setattr(
        "distr.core.integrations.slack.bridge.get_integration_message_bus",
        fake_get_bus,
    )

    bus.set_chat_id_provider(lambda: 5)
    bus.set_text_sink(
        lambda text, is_telegram, img, metadata: seen.append(
            (text, is_telegram, img, metadata)
        )
    )
    route_fn(**kw)
    expected_surface = "discord" if "author_id" in kw else "slack"
    assert seen == [
        ("hi", True, None, {"speak": None, "surface": expected_surface, "chat_id": 5})
    ]


def test_slack_event_to_incoming_platform() -> None:
    m = slack_event_to_incoming(channel_id="C99", user_id=None, text="x")
    assert m.platform == "slack"
    assert m.thread_id == "C99"
    assert m.sender_id is None
