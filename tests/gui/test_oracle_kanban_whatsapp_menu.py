from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
MENU_PY = ROOT / "distr/gui/oracle/menu.py"
KANBAN_PY = ROOT / "distr/gui/web/routes/kanban.py"


def test_development_submenu_routes_whatsapp_through_incoming():
    menu = MENU_PY.read_text(encoding="utf-8")
    assert 'QMenu("Incoming", self.development_submenu)' in menu
    assert 'QMenu("Ticket Boards", self.menu)' not in menu
    assert 'QAction("Manage Incoming"' in menu
    assert 'QAction("Sync WhatsApp"' in menu
    assert '"/development/incoming/"' in menu
    assert "_sync_whatsapp_messages_from_menu" in menu
    assert "sync_whatsapp_from_relay_and_announce" in menu
    assert 'QAction("Messages"' not in menu


def test_web_whatsapp_sync_does_not_announce_tts():
    """Web sync is used by background auto-sync; TTS belongs on tray menu only."""
    py = KANBAN_PY.read_text(encoding="utf-8")
    assert "sync_whatsapp_from_relay" in py
    assert "announce_whatsapp_sync" not in py
