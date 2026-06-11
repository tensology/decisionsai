"""Snippet tray-menu paste should restore the user's previous foreground app."""

import time

import distr.core.actions.desktop as desktop


def test_is_own_app_name_matches_decisions_process_names():
    assert desktop.is_own_app_name("Python") is True
    assert desktop.is_own_app_name("DecisionsAI") is True
    assert desktop.is_own_app_name("Google Chrome") is False


def test_remember_frontmost_app_if_external_stores_non_decisions_app(monkeypatch):
    monkeypatch.setattr(desktop, "get_frontmost_app_name", lambda: "Slack")
    desktop._last_external_frontmost_app = None
    desktop._last_external_frontmost_at = 0.0

    remembered = desktop.remember_frontmost_app_if_external()

    assert remembered == "Slack"
    assert desktop.get_remembered_external_frontmost_app() == "Slack"


def test_remember_frontmost_app_if_external_ignores_decisions_app(monkeypatch):
    monkeypatch.setattr(desktop, "get_frontmost_app_name", lambda: "Python")
    desktop._last_external_frontmost_app = "Slack"
    desktop._last_external_frontmost_at = time.time()

    remembered = desktop.remember_frontmost_app_if_external()

    assert remembered == "Slack"
    assert desktop.get_remembered_external_frontmost_app() == "Slack"


def test_get_remembered_external_frontmost_app_expires_stale_target():
    desktop._last_external_frontmost_app = "Slack"
    desktop._last_external_frontmost_at = time.time() - 500

    assert desktop.get_remembered_external_frontmost_app() is None


def test_paste_snippet_from_menu_requests_focus_restore():
    import inspect

    from distr.gui.oracle.menu import MenuTrayMixin

    source = inspect.getsource(MenuTrayMixin._paste_snippet_from_menu)
    assert "restore_focus=True" in source


def test_snippet_menu_shows_hotkey_without_binding_qaction_shortcut():
    import inspect

    from distr.gui.oracle.menu import MenuTrayMixin

    source = inspect.getsource(MenuTrayMixin._rebuild_snippet_menu_items)
    assert "format_remote_hotkey_display" in source
    assert "_apply_menu_shortcut(item, combo[0], combo[1])" not in source
    assert "setShortcutVisibleInContextMenu(False)" in source
