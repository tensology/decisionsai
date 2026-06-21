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

    assert received == [("hello", True, "/tmp/x.png", {"speak": None, "surface": "telegram", "chat_id": 42})]

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
    assert received == [("voice transcript", True, None, {"speak": None, "surface": "telegram", "chat_id": 1})]
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
    assert calls == [{"speak": False, "surface": "telegram", "chat_id": 1}]


def test_deliver_telegram_user_input_waits_for_sink_instead_of_dropping(tmp_path: Path) -> None:
    mapping = tmp_path / "pending_telegram.json"
    bus = IntegrationMessageBus(mapping_path=mapping)
    bus.set_chat_id_provider(lambda: 42)

    bus.deliver_telegram_user_input(
        text="message sent while startup is still wiring",
        telegram_chat_id=777001,
        input_type="text",
    )

    received: list[tuple] = []
    bus.set_text_sink(
        lambda text, is_telegram, img, speak: received.append((text, is_telegram, img, speak))
    )

    assert received == [
        (
            "message sent while startup is still wiring",
            True,
            None,
            {"speak": None, "input_type": "text", "surface": "telegram", "chat_id": 42},
        )
    ]
    assert IntegrationMessageBus(mapping_path=mapping).resolve_mapped_chat_id("telegram", "777001") == 42


def test_deliver_telegram_user_input_prefers_existing_thread_mapping_over_current_chat(tmp_path: Path) -> None:
    mapping = tmp_path / "existing_telegram.json"
    bus = IntegrationMessageBus(mapping_path=mapping)
    received: list[tuple] = []
    bus.remember_thread_chat("telegram", "777001", 42)
    bus.set_text_sink(
        lambda text, is_telegram, img, speak: received.append((text, is_telegram, img, speak))
    )
    bus.set_chat_id_provider(lambda: 99)

    bus.deliver_telegram_user_input(
        text="stay on mapped chat",
        telegram_chat_id=777001,
        speak=False,
    )

    assert received == [
        (
            "stay on mapped chat",
            True,
            None,
            {"speak": False, "surface": "telegram", "chat_id": 42},
        )
    ]
    assert IntegrationMessageBus(mapping_path=mapping).resolve_mapped_chat_id("telegram", "777001") == 42


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
    assert received == [("hello bus", True, "/tmp/a.png", {"speak": True, "surface": "slack", "chat_id": 99})]
    bus2 = IntegrationMessageBus(mapping_path=mapping)
    assert bus2.resolve_mapped_chat_id("slack", "C012") == 99


def test_ingest_incoming_waits_for_sink_instead_of_dropping(tmp_path: Path) -> None:
    mapping = tmp_path / "pending_connector.json"
    bus = IntegrationMessageBus(mapping_path=mapping)
    bus.set_chat_id_provider(lambda: 55)

    bus.ingest_incoming(
        IncomingMessage(
            platform="slack",
            thread_id="C-startup",
            text="arrived before sink",
            attachments=["/tmp/startup.png"],
            speak=True,
        )
    )

    received: list[tuple] = []
    bus.set_text_sink(
        lambda text, is_telegram, img, speak: received.append((text, is_telegram, img, speak))
    )

    assert received == [("arrived before sink", True, "/tmp/startup.png", {"speak": True, "surface": "slack", "chat_id": 55})]
    assert IntegrationMessageBus(mapping_path=mapping).resolve_mapped_chat_id("slack", "C-startup") == 55


def test_ingest_incoming_prefers_existing_thread_mapping_over_current_chat(tmp_path: Path) -> None:
    mapping = tmp_path / "existing_slack.json"
    bus = IntegrationMessageBus(mapping_path=mapping)
    received: list[tuple] = []
    bus.remember_thread_chat("slack", "C012", 42)
    bus.set_text_sink(
        lambda text, is_telegram, img, speak: received.append((text, is_telegram, img, speak))
    )
    bus.set_chat_id_provider(lambda: 99)

    bus.ingest_incoming(
        IncomingMessage(
            platform="slack",
            thread_id="C012",
            text="mapped connector thread",
            speak=True,
        )
    )

    assert received == [
        (
            "mapped connector thread",
            True,
            None,
            {"speak": True, "surface": "slack", "chat_id": 42},
        )
    ]
    assert IntegrationMessageBus(mapping_path=mapping).resolve_mapped_chat_id("slack", "C012") == 42


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
    assert received == [("z", True, None, {"speak": None, "surface": "discord"})]
