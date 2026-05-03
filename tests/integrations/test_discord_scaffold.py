"""TASK 16 scaffold — package import without Discord deps."""

from distr.core.integrations.discord import PLATFORM_ID, DiscordOutboundQueue


def test_discord_platform_id_constant() -> None:
    assert PLATFORM_ID == "discord"


def test_discord_outbound_queue_bounded() -> None:
    q = DiscordOutboundQueue(max_items=2)
    assert q.push({"x": 1}) is True
    assert q.push({"x": 2}) is True
    assert q.push({"x": 3}) is False
    assert q.pop() == {"x": 1}
