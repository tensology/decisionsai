"""Integration message bus (R15) — mapping persistence and Telegram sink."""

from __future__ import annotations

from pathlib import Path

from distr.core.integrations.bus import IncomingMessage, IntegrationMessageBus


def test_deliver_telegram_user_input_calls_sink_and_persists_mapping(tmp_path: Path) -> None:
    mapping = tmp_path / "integration_message_bus_mapping.json"
    bus = IntegrationMessageBus(mapping_path=mapping)
    received: list[tuple] = []

    bus.set_text_sink(
        lambda text, is_telegram, uploaded_image_path, speak: received.append(
            (text, is_telegram, uploaded_image_path, speak)
        )
    )
    bus.set_chat_id_provider(lambda: 42)

    bus.deliver_telegram_user_input(
        text="hello",
        image_path="/tmp/x.png",
        telegram_chat_id=777001,
        speak=None,
    )

    assert received == [("hello", True, "/tmp/x.png", None)]

    bus2 = IntegrationMessageBus(mapping_path=mapping)
    assert bus2.resolve_mapped_chat_id("telegram", "777001") == 42


def test_deliver_without_telegram_thread_id_still_calls_sink(tmp_path: Path) -> None:
    """Rare path: transcription event without Telegram manager — no mapping key."""
    mapping = tmp_path / "m.json"
    bus = IntegrationMessageBus(mapping_path=mapping)
    received: list[tuple] = []
    bus.set_text_sink(
        lambda text, is_telegram, img, speak: received.append(
            (text, is_telegram, img, speak)
        )
    )
    bus.set_chat_id_provider(lambda: 1)
    bus.deliver_telegram_user_input(
        text="voice transcript",
        telegram_chat_id=None,
        speak=None,
    )
    assert received == [("voice transcript", True, None, None)]
    reloaded = IntegrationMessageBus(mapping_path=mapping)
    assert reloaded.resolve_mapped_chat_id("telegram", "1") is None


def test_deliver_telegram_user_passes_speak_false(tmp_path: Path) -> None:
    mapping = tmp_path / "m.json"
    bus = IntegrationMessageBus(mapping_path=mapping)
    calls: list[bool | None] = []
    bus.set_text_sink(
        lambda _t, _tg, _img, speak: calls.append(speak)
    )
    bus.set_chat_id_provider(lambda: 1)
    bus.deliver_telegram_user_input(
        text="cmd",
        telegram_chat_id=9,
        speak=False,
    )
    assert calls == [False]


def test_ingest_incoming_maps_platform_thread_and_calls_sink(tmp_path: Path) -> None:
    mapping = tmp_path / "ingest.json"
    bus = IntegrationMessageBus(mapping_path=mapping)
    received: list[tuple] = []
    bus.set_text_sink(
        lambda text, is_telegram, img, speak: received.append(
            (text, is_telegram, img, speak)
        )
    )
    bus.set_chat_id_provider(lambda: 99)
    bus.ingest_incoming(
        IncomingMessage(
            platform="slack",
            thread_id="C012",
            text="hello bus",
            attachments=["/tmp/a.png"],
            speak=True,
        )
    )
    assert received == [("hello bus", True, "/tmp/a.png", True)]
    bus2 = IntegrationMessageBus(mapping_path=mapping)
    assert bus2.resolve_mapped_chat_id("slack", "C012") == 99


def test_resolve_thread_id_for_chat_inverse_mapping(tmp_path: Path) -> None:
    mapping = tmp_path / "inverse.json"
    bus = IntegrationMessageBus(mapping_path=mapping)
    bus.remember_thread_chat("discord", "123456789012345678", 42)
    bus.remember_thread_chat("slack", "C0ABCDEF", 42)
    assert bus.resolve_thread_id_for_chat("discord", 42) == "123456789012345678"
    assert bus.resolve_thread_id_for_chat("slack", 42) == "C0ABCDEF"
    assert bus.resolve_thread_id_for_chat("discord", 99) is None


def test_ingest_incoming_without_chat_provider_still_calls_sink(tmp_path: Path) -> None:
    bus = IntegrationMessageBus(mapping_path=tmp_path / "n.json")
    received: list[tuple] = []
    bus.set_text_sink(
        lambda text, is_telegram, img, speak: received.append((text, is_telegram, img, speak))
    )
    bus.ingest_incoming(IncomingMessage(platform="discord", thread_id="7", text="z"))
    assert received == [("z", True, None, None)]
