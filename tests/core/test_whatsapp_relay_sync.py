from distr.core.kanban.whatsapp_relay_sync import build_whatsapp_sync_speech, sync_whatsapp_from_relay


def test_build_whatsapp_sync_speech_new_messages():
    assert build_whatsapp_sync_speech({"synced": 3}) == "Synced 3 new messages from WhatsApp."


def test_build_whatsapp_sync_speech_single_message():
    assert build_whatsapp_sync_speech({"synced": 1}) == "Synced 1 new message from WhatsApp."


def test_build_whatsapp_sync_speech_none():
    assert build_whatsapp_sync_speech({"synced": 0}) == "No new messages on WhatsApp."


def test_build_whatsapp_sync_speech_error():
    assert build_whatsapp_sync_speech({"synced": 0, "error": "offline"}) == (
        "WhatsApp sync did not complete."
    )


def test_sync_whatsapp_from_relay_uses_headless_client_without_manager(monkeypatch):
    monkeypatch.setattr(
        "distr.core.kanban.whatsapp_relay_sync.get_whatsapp_manager",
        lambda: None,
    )
    monkeypatch.setattr(
        "distr.core.kanban.whatsapp_relay_sync.is_whatsapp_account_connected",
        lambda: True,
    )
    monkeypatch.setattr(
        "distr.core.integrations.whatsapp.relay_client.sync_messages_from_relay",
        lambda mark_processed=False: {"synced": 2, "total": 2},
    )

    result = sync_whatsapp_from_relay(mark_processed=True)

    assert result == {"synced": 2, "total": 2}


def test_sync_whatsapp_from_relay_requires_settings_connection(monkeypatch):
    monkeypatch.setattr(
        "distr.core.kanban.whatsapp_relay_sync.get_whatsapp_manager",
        lambda: None,
    )
    monkeypatch.setattr(
        "distr.core.kanban.whatsapp_relay_sync.is_whatsapp_account_connected",
        lambda: False,
    )

    result = sync_whatsapp_from_relay()

    assert result["synced"] == 0
    assert result.get("error") == "WhatsApp not connected"
