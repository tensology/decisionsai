from distr.core.kanban.whatsapp_relay_sync import (
    build_whatsapp_sync_speech,
    enrich_sync_result_with_relay_status,
    is_relay_whatsapp_connected,
    relay_link_warning,
    sync_whatsapp_from_relay,
)


def _connected_status():
    return {"status": "connected", "phone": {"name": "Paul"}}


def test_build_whatsapp_sync_speech_new_messages():
    assert build_whatsapp_sync_speech({"synced": 3, "relay_link_ok": True}) == (
        "Synced 3 new messages from WhatsApp."
    )


def test_build_whatsapp_sync_speech_single_message():
    assert build_whatsapp_sync_speech({"synced": 1, "relay_link_ok": True}) == (
        "Synced 1 new message from WhatsApp."
    )


def test_build_whatsapp_sync_speech_none():
    assert build_whatsapp_sync_speech({"synced": 0, "relay_link_ok": True}) == (
        "No new messages on WhatsApp."
    )


def test_build_whatsapp_sync_speech_error():
    assert build_whatsapp_sync_speech({"synced": 0, "error": "offline"}) == (
        "WhatsApp sync did not complete."
    )


def test_build_whatsapp_sync_speech_relay_unlinked():
    speech = build_whatsapp_sync_speech(
        {
            "synced": 0,
            "relay_link_ok": False,
            "warning": "WhatsApp on the server needs a QR scan.",
        }
    )
    assert "not linked" in speech.lower()
    assert "settings" in speech.lower()


def test_build_whatsapp_sync_speech_relay_unlinked_with_older_messages():
    speech = build_whatsapp_sync_speech(
        {
            "synced": 4,
            "relay_link_ok": False,
            "warning": "needs QR",
        }
    )
    assert "4 older messages" in speech
    assert "not linked" in speech.lower()


def test_is_relay_whatsapp_connected():
    assert is_relay_whatsapp_connected({"status": "connected"}) is True
    assert is_relay_whatsapp_connected({"status": "qr_ready"}) is False


def test_relay_link_warning_qr_ready():
    msg = relay_link_warning({"status": "qr_ready"})
    assert msg is not None
    assert "QR" in msg


def test_enrich_sync_result_adds_warning_when_unlinked(monkeypatch):
    monkeypatch.setattr(
        "distr.core.kanban.whatsapp_relay_sync.relay_whatsapp_live_status",
        lambda: {"status": "qr_ready"},
    )
    out = enrich_sync_result_with_relay_status({"synced": 0, "total": 0})
    assert out["relay_link_ok"] is False
    assert out["relay_status"] == "qr_ready"
    assert "QR" in out["warning"]


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
        "distr.core.kanban.whatsapp_relay_sync.relay_whatsapp_live_status",
        _connected_status,
    )
    monkeypatch.setattr(
        "distr.core.integrations.whatsapp.relay_client.sync_messages_from_relay",
        lambda mark_processed=False: {"synced": 2, "total": 2},
    )

    result = sync_whatsapp_from_relay(mark_processed=True)

    assert result["synced"] == 2
    assert result["total"] == 2
    assert result["relay_link_ok"] is True


def test_sync_whatsapp_from_relay_warns_when_relay_unlinked(monkeypatch):
    monkeypatch.setattr(
        "distr.core.kanban.whatsapp_relay_sync.get_whatsapp_manager",
        lambda: None,
    )
    monkeypatch.setattr(
        "distr.core.kanban.whatsapp_relay_sync.is_whatsapp_account_connected",
        lambda: True,
    )
    monkeypatch.setattr(
        "distr.core.kanban.whatsapp_relay_sync.relay_whatsapp_live_status",
        lambda: {"status": "qr_ready"},
    )
    monkeypatch.setattr(
        "distr.core.integrations.whatsapp.relay_client.sync_messages_from_relay",
        lambda mark_processed=False: {"synced": 0, "total": 0},
    )

    result = sync_whatsapp_from_relay()

    assert result["relay_link_ok"] is False
    assert "QR" in result["warning"]


def test_sync_whatsapp_from_relay_requires_settings_connection(monkeypatch):
    monkeypatch.setattr(
        "distr.core.kanban.whatsapp_relay_sync.get_whatsapp_manager",
        lambda: None,
    )
    monkeypatch.setattr(
        "distr.core.kanban.whatsapp_relay_sync.is_whatsapp_account_connected",
        lambda: False,
    )
    monkeypatch.setattr(
        "distr.core.kanban.whatsapp_relay_sync.relay_whatsapp_live_status",
        _connected_status,
    )

    result = sync_whatsapp_from_relay()

    assert result["synced"] == 0
    assert result.get("error") == "WhatsApp not connected"
