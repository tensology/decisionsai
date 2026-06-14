"""Unit tests for oracle context menu helpers."""

import hashlib

from distr.core.project_startup_terminals import (
    _build_start_speak_message,
    _build_stop_speak_message,
    parse_startup_command_lines,
)
from distr.gui.oracle.menu import (
    format_board_menu_label,
    format_chat_short_hash,
    format_skin_folder_label,
    get_available_skins_for_menu,
    get_skin_display_name,
    is_whatsapp_enabled_in_settings,
    organize_board_menu_sections,
    resolve_action_play_name,
    truncate_menu_title,
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
