"""Unit tests for oracle context menu helpers."""

import hashlib
import json

from distr.core.project_startup_terminals import (
    _build_start_speak_message,
    _build_stop_speak_message,
    parse_startup_command_lines,
)
from distr.gui.oracle.menu import (
    development_board_key,
    format_board_menu_label,
    format_chat_short_hash,
    format_skin_folder_label,
    get_available_skins_for_menu,
    get_recent_development_threads_for_menu,
    get_skin_display_name,
    is_whatsapp_enabled_in_settings,
    organize_board_menu_sections,
    resolve_action_play_name,
    truncate_menu_title,
    toggle_project_terminals_via_web,
    _is_automation_workflow,
)


def test_format_chat_short_hash_active_chat() -> None:
    chat_id = 42
    expected = f"Chat: #{hashlib.md5(str(chat_id).encode()).hexdigest()[:6]}"
    assert format_chat_short_hash(chat_id) == expected


def test_format_chat_short_hash_no_chat() -> None:
    assert format_chat_short_hash(None) == "No active chat"


def test_chat_submenu_orders_manage_before_new_chat() -> None:
    import inspect

    from distr.gui.oracle.menu import MenuTrayMixin

    source = inspect.getsource(MenuTrayMixin.create_menu)
    manage_idx = source.index('QAction("Manage Chats"')
    new_chat_idx = source.index('QAction("New Chat"')
    chat_id_idx = source.index("self.chat_id_menu_item")
    assert manage_idx < new_chat_idx < chat_id_idx


def test_new_chat_action_opens_the_chat_surface() -> None:
    import inspect

    from distr.gui.oracle.window import OracleWindow

    source = inspect.getsource(OracleWindow.handle_new_chat)
    assert 'self._open_web_url(f"/chat/?id={int(new_chat_id)}")' in source
    assert "/development/threads/" not in source


def test_system_tray_exposes_development_instead_of_legacy_workflow_loops() -> None:
    import inspect

    from distr.gui.oracle.menu import MenuTrayMixin

    source = inspect.getsource(MenuTrayMixin.create_menu)
    assert 'QMenu("Development"' in source
    assert 'QAction("Manage Development"' in source
    assert 'QAction("New Thread"' in source
    assert "Workflows/Loops" not in source


def test_system_tray_consolidates_development_submenus_in_approved_order() -> None:
    import inspect

    from distr.gui.oracle.menu import MenuTrayMixin

    source = inspect.getsource(MenuTrayMixin.create_menu)
    assert 'QMenu("Recent", self.development_submenu)' not in source
    assert '"Scheduled Actions", self.development_submenu' not in source
    assert 'QMenu("Projects", self.menu)' not in source
    assert 'QMenu("Ticket Boards", self.menu)' not in source
    assert 'QMenu("Automations", self.menu)' not in source


def test_development_board_and_terminal_submenus_use_direct_dynamic_actions() -> None:
    import inspect

    from distr.gui.oracle.menu import MenuTrayMixin

    board_source = inspect.getsource(MenuTrayMixin._rebuild_project_menu_items)
    terminal_source = inspect.getsource(MenuTrayMixin._rebuild_terminal_menu_items)
    assert "board_action = QAction(title" in board_source
    assert "board_action.setCheckable" not in board_source
    assert "QMenu(title" not in board_source
    assert '"Edit Board"' not in board_source
    assert "terminal_action.setCheckable(True)" in terminal_source
    assert "terminal_action.setChecked(bool(running))" in terminal_source


def test_development_board_key_uses_workspace_provider_names() -> None:
    assert development_board_key("database", "7") == "decisions:7"
    assert development_board_key("jira", "ABC") == "jira:ABC"


def test_system_tray_runtime_hierarchy_matches_finished_menu(monkeypatch) -> None:
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PyQt6 import QtCore, QtWidgets

    import distr.gui.oracle.menu as menu_module
    from distr.gui.oracle.menu import MenuTrayMixin

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    class _Tray(QtCore.QObject, MenuTrayMixin):
        def __init__(self):
            super().__init__()
            self.is_hands_free = False
            self.create_menu()

        def __getattr__(self, _name):
            return lambda *_args, **_kwargs: None

    tray = _Tray()
    top_level = [
        action.text()
        for action in tray.menu.actions()
        if not action.isSeparator()
    ]
    assert top_level == [
        "Listening",
        "Hands-Free Mode: OFF",
        "Stop Dictating",
        "Chat",
        "Development",
        "Actions",
        "Snippets",
        "Skin",
        "Preferences",
        "About DecisionsAI",
        "Quit",
    ]
    development_items = [
        action.text()
        for action in tray.development_submenu.actions()
        if not action.isSeparator()
    ]
    assert development_items == [
        "Manage Development",
        "Incoming",
        "Automations",
        "Workflows",
        "Boards",
        "Terminals",
        "Pinned Threads",
        "New Thread",
    ]
    assert tray.pinned_development_menu_action.isVisible() is False

    monkeypatch.setattr(
        menu_module,
        "get_development_boards_for_menu",
        lambda: [("database", "7", "Delivery", "", 7, True, False)],
    )
    terminal_rows = [(7, "Delivery project", True, True), (8, "Idle project", True, False)]
    monkeypatch.setattr(menu_module, "get_projects_for_menu", lambda: list(terminal_rows))
    tray.settings = {"accepted_eula": True}
    tray._check_eula_accepted = lambda: True
    opened = []
    terminals = []
    tray._open_web_url = opened.append
    tray._handle_project_from_menu = terminals.append
    tray._rebuild_project_menu_items()
    board_actions = [action for action in tray.projects_submenu.actions() if not action.isSeparator()]
    assert [action.text() for action in board_actions] == ["Manage Boards", "Delivery"]
    assert board_actions[1].menu() is None
    board_actions[0].trigger()
    board_actions[1].trigger()
    assert opened == ["/development/", "/development/boards/decisions/7/kanban/"]

    tray._rebuild_terminal_menu_items()
    terminal_actions = [action for action in tray.terminals_submenu.actions() if not action.isSeparator()]
    assert [action.text() for action in terminal_actions] == [
        "Manage Terminals",
        "Delivery project",
        "Idle project",
    ]
    assert terminal_actions[1].isCheckable() is True
    assert terminal_actions[1].isChecked() is True
    assert terminal_actions[2].isChecked() is False
    terminal_actions[0].trigger()
    terminal_actions[1].trigger()
    terminal_actions[2].trigger()
    assert opened[-1] == "/development/terminals/"
    assert terminals == [7, 8]

    terminal_rows[:] = [
        (7, "Delivery project", True, False),
        (8, "Idle project", True, True),
    ]
    tray._rebuild_terminal_menu_items()
    refreshed_terminal_actions = [
        action for action in tray.terminals_submenu.actions() if not action.isSeparator()
    ]
    assert refreshed_terminal_actions[1].isChecked() is False
    assert refreshed_terminal_actions[2].isChecked() is True

    monkeypatch.setattr(
        menu_module,
        "get_recent_development_threads_for_menu",
        lambda limit=5: [(index, f"Recent {index}") for index in range(1, 7)],
    )
    tray._rebuild_development_menu_items()
    development_items = [
        action.text()
        for action in tray.development_submenu.actions()
        if not action.isSeparator()
    ]
    assert development_items[-6:] == [
        "New Thread",
        "Recent 1",
        "Recent 2",
        "Recent 3",
        "Recent 4",
        "Recent 5",
    ]
    assert all(action.menu() is None for action in tray._development_thread_actions)
    menu_actions = tray.development_submenu.actions()
    new_thread_index = menu_actions.index(tray.new_development_action)
    assert menu_actions[new_thread_index + 1] is tray._recent_development_separator
    assert menu_actions[new_thread_index + 1].isSeparator()
    assert menu_actions[new_thread_index + 2] is tray._development_thread_actions[0]
    tray._development_thread_actions[0].trigger()
    assert opened[-1] == "/development/threads/1/"

    monkeypatch.setattr(menu_module, "is_whatsapp_enabled_in_settings", lambda: False)
    tray._rebuild_kanban_menu_items()
    assert tray.incoming_menu_action.isVisible() is True
    assert tray.manage_incoming_action.isVisible() is True
    assert tray.sync_incoming_whatsapp_action.isVisible() is False

    monkeypatch.setattr(
        menu_module,
        "get_pinned_development_threads_for_menu",
        lambda limit=15: [(17, "Pinned delivery")],
    )
    tray._refresh_development_menu_visibility()
    assert tray.pinned_development_menu_action.isVisible() is True
    tray._snippet_focus_timer.stop()
    tray.deleteLater()
    app.processEvents()


def test_system_tray_terminal_toggle_uses_the_shared_web_action(monkeypatch) -> None:
    import distr.core.web_runtime as web_runtime
    import distr.gui.oracle.menu as menu_module

    calls: list[tuple[str, str]] = []
    payloads = [
        {"projects": {"7": {"startup_count": 2}}},
        {"success": True, "action": "stopped", "stopped": 2},
    ]

    class Response:
        def __init__(self, payload):
            self.payload = payload

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return json.dumps(self.payload).encode()

    def urlopen(request, timeout):
        calls.append((request.get_method(), request.full_url))
        return Response(payloads.pop(0))

    monkeypatch.setattr(web_runtime, "resolve_local_web_base_url", lambda: "http://127.0.0.1:8765")
    monkeypatch.setattr(web_runtime, "internal_api_headers", lambda content_type="application/json": {})
    monkeypatch.setattr(menu_module.urllib.request, "urlopen", urlopen)

    result = toggle_project_terminals_via_web(7)

    assert result == {"success": True, "action": "stopped", "stopped": 2}
    assert calls == [
        ("GET", "http://127.0.0.1:8765/api/projects/terminal-status?project_ids=7"),
        ("POST", "http://127.0.0.1:8765/api/projects/7/startup-terminals/stop"),
    ]


def test_about_menu_item_above_quit_with_separator() -> None:
    import inspect

    from distr.gui.oracle.menu import MenuTrayMixin

    source = inspect.getsource(MenuTrayMixin.create_menu)
    about_idx = source.index('QAction("About DecisionsAI"')
    about_sep_idx = source.index("self.menu.addAction(self.about_action)")
    quit_sep_idx = source.index("self.menu.addAction(self.exit_action)")
    assert about_idx < about_sep_idx < quit_sep_idx
    assert source.index("self.menu.addSeparator()", about_sep_idx) < quit_sep_idx


def test_truncate_menu_title_short() -> None:
    assert truncate_menu_title("Hello") == "Hello"


def test_truncate_menu_title_long() -> None:
    title = "A" * 60
    truncated = truncate_menu_title(title)
    assert len(truncated) == 48
    assert truncated.endswith("…")


def test_truncate_menu_title_empty_fallback() -> None:
    assert truncate_menu_title("") == "New Chat"
    assert truncate_menu_title("   ") == "New Chat"


def test_skin_display_name_unchanged() -> None:
    assert get_skin_display_name("Oracle") == "Oracle"
    assert get_skin_display_name(None) == "Avatar"


def test_resolve_action_play_name_uses_title() -> None:
    assert resolve_action_play_name(7, "Open Slack", "[]") == "Open Slack"


def test_resolve_action_play_name_uses_trigger_word() -> None:
    assert resolve_action_play_name(7, "", '["slack"]') == "slack"


def test_resolve_action_play_name_fallback() -> None:
    assert resolve_action_play_name(7, "", "[]") == "action_7"


def test_parse_startup_command_lines_skips_comments_and_blanks() -> None:
    text = "npm run dev\n\n# comment\npython manage.py runserver"
    assert parse_startup_command_lines(text) == ["npm run dev", "python manage.py runserver"]


def test_parse_startup_command_lines_empty() -> None:
    assert parse_startup_command_lines("") == []
    assert parse_startup_command_lines("# only comments\n  \n") == []


def test_build_start_speak_message_success() -> None:
    assert _build_start_speak_message("DecisionsAI", 2, 0) == (
        "Project DecisionsAI startup terminals started."
    )


def test_build_stop_speak_message_success() -> None:
    assert _build_stop_speak_message("DecisionsAI", 3) == (
        "Project DecisionsAI startup terminals stopped."
    )


def test_is_whatsapp_enabled_in_settings_false_when_missing() -> None:
    assert is_whatsapp_enabled_in_settings() in (True, False)


def test_format_board_menu_label_omits_provider_prefix() -> None:
    assert format_board_menu_label("Sprint board", "jira") == "Sprint board"
    assert format_board_menu_label("Backlog", "trello") == "Backlog"
    assert format_board_menu_label("Local work", "database") == "Local work"


def test_organize_board_menu_sections_local_first_then_external_headers() -> None:
    boards = [
        ("trello", "t1", "Alpha", ""),
        ("database", "1", "Local B", ""),
        ("jira", "j1", "Beta", ""),
        ("database", "2", "Local A", ""),
        ("jira", "j2", "Alpha", ""),
    ]
    sections = organize_board_menu_sections(boards)
    assert [header for header, _ in sections] == [None, "Jira", "Trello"]
    assert [row[2] for row in sections[0][1]] == ["Local A", "Local B"]
    assert [row[2] for row in sections[1][1]] == ["Alpha", "Beta"]
    assert [row[2] for row in sections[2][1]] == ["Alpha"]


def test_format_skin_folder_label() -> None:
    assert format_skin_folder_label("oracle") == "Oracle"
    assert format_skin_folder_label("my_avatar") == "My Avatar"


def test_get_available_skins_for_menu_oracle_first(tmp_path) -> None:
    (tmp_path / "oracle").mkdir()
    (tmp_path / "zebra").mkdir()
    (tmp_path / "adam").mkdir()
    skins = get_available_skins_for_menu(str(tmp_path))
    assert skins[0] == ("oracle", "Oracle")
    assert [name for name, _ in skins] == ["oracle", "adam", "zebra"]


def test_is_automation_workflow_requires_surface_marker() -> None:
    class _Workflow:
        workflow_type = "scheduled"
        context_rules = '{"decisions_surface": "automation"}'

    assert _is_automation_workflow(_Workflow()) is True

    class _Other:
        workflow_type = "scheduled"
        context_rules = '{"decisions_surface": "workflow"}'

    assert _is_automation_workflow(_Other()) is False
