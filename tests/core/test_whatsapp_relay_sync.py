from distr.core.kanban.whatsapp_relay_sync import build_whatsapp_sync_speech


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
