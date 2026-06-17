"""Menu and system-tray mixin for OracleWindow.

Handles context-menu creation, tray-icon management, visibility toggling,
EULA gating of menu items, and recording/dictation menu state.
"""

import hashlib
import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Optional
from urllib.parse import quote

from PyQt6 import QtWidgets, QtGui, QtCore
from PyQt6.QtGui import QAction, QActionGroup, QKeySequence
from PyQt6.QtWidgets import QApplication

from distr.core.hotkeys import (
    DEFAULTS as HOTKEY_DEFAULTS,
    chord_to_qt_sequence,
    format_remote_hotkey_display,
)
from sqlalchemy import desc, nulls_last

from distr.core.db import Action, Chat, Snippet, get_session
from distr.core.db.kanban import KanbanBoard
from distr.core.db.projects import Project
from distr.core.db.workflow import AutoWorkflow
from distr.core.project_startup_terminals import (
    parse_startup_command_lines,
    project_startup_terminals_running,
    start_project_startup_terminals,
    stop_project_startup_terminals,
)
from distr.core.actions.desktop import remember_frontmost_app_if_external
from distr.core.paths import AVATARS_DIR, ICONS_DIR
from distr.core.signals import signal_manager
from distr.core.utils import load_settings_from_db


# ---------------------------------------------------------------------------
# Pure helper – extractable for testing without PyQt6
# ---------------------------------------------------------------------------

def get_skin_display_name(skin_name: Optional[str]) -> str:
    """Return the display name for context menu items from a skin name.

    If *skin_name* is a non-empty string it is returned as-is; otherwise
    the fallback ``"Avatar"`` is used.
    """
    if skin_name and isinstance(skin_name, str) and skin_name.strip():
        return skin_name
    return "Avatar"


def format_chat_short_hash(chat_id: Optional[int]) -> str:
    """Return the short hash label shown for a chat ID in the context menu."""
    if not chat_id:
        return "No active chat"
    md5_hash = hashlib.md5(str(chat_id).encode()).hexdigest()
    return f"Chat: #{md5_hash[:6]}"


def truncate_menu_title(title: str, max_len: int = 48) -> str:
    """Truncate long chat titles for menu display."""
    cleaned = (title or "New Chat").strip() or "New Chat"
    if len(cleaned) <= max_len:
        return cleaned
    return cleaned[: max_len - 1] + "…"


def get_recent_chats_for_menu(limit: int = 5) -> list[tuple[int, str]]:
    """Return the most recently modified root chats for the context menu."""
    with get_session() as session:
        chats = (
            session.query(Chat)
            .filter(
                Chat.parent_id.is_(None),
                Chat.is_archived.is_(False),
                Chat.is_hidden.is_(False),
            )
            .order_by(Chat.created_date.desc(), Chat.id.desc())
            .limit(limit)
            .all()
        )
        return [
            (chat.id, truncate_menu_title(chat.title or "New Chat"))
            for chat in chats
        ]


def resolve_action_play_name(
    action_id: int,
    title: Optional[str],
    additional_trigger_words: Optional[str],
) -> str:
    """Return the name passed to the action playback service."""
    name = (title or "").strip()
    if name:
        return name
    try:
        words = json.loads(additional_trigger_words or "[]")
        if isinstance(words, list) and words:
            first = str(words[0]).strip()
            if first:
                return first
    except (json.JSONDecodeError, TypeError, ValueError):
        pass
    return f"action_{action_id}"


def action_is_playable(action: Action) -> bool:
    """Return whether an action can be run from the recorded playback service."""
    if action.is_instruction:
        return False
    return bool((action.recording_filename or "").strip())


def get_projects_for_menu() -> list[tuple[int, str, bool, bool]]:
    """Return all projects for the context menu.

    Each row is ``(project_id, label, has_startup_commands, terminals_running)``.
    """
    with get_session() as session:
        projects = session.query(Project).order_by(Project.name.asc()).all()
        rows: list[tuple[int, str, bool, bool]] = []
        for project in projects:
            has_startup = bool(parse_startup_command_lines(project.startup_instructions or ""))
            terminals_running = project_startup_terminals_running(project.id)
            rows.append(
                (
                    project.id,
                    truncate_menu_title(project.name or "Untitled", max_len=40),
                    has_startup,
                    terminals_running,
                )
            )
        return rows


def get_recent_actions_for_menu(limit: int = 10) -> list[tuple[int, str, bool]]:
    """Return recent actions ordered by last run, then modified date."""
    with get_session() as session:
        actions = (
            session.query(Action)
            .order_by(nulls_last(desc(Action.last_run_date)), desc(Action.modified_date))
            .limit(limit)
            .all()
        )
        rows: list[tuple[int, str, bool]] = []
        for action in actions:
            label = truncate_menu_title(action.title or "Untitled", max_len=40)
            if action.is_instruction:
                label = f"{label} (instruction)"
            rows.append((action.id, label, action_is_playable(action)))
        return rows


logger = logging.getLogger(__name__)

RECENT_CHATS_MENU_LIMIT = 5
RECENT_ACTIONS_MENU_LIMIT = 10
AUTOMATION_MENU_SURFACE = "automation"
SNIPPETS_MENU_LIMIT = 20
AUTOMATIONS_MENU_LIMIT = 15
KANBAN_BOARDS_MENU_LIMIT = 20


def _json_menu_config(raw: Any) -> dict:
    if isinstance(raw, dict):
        return raw
    if not raw:
        return {}
    try:
        loaded = json.loads(str(raw))
        return loaded if isinstance(loaded, dict) else {}
    except Exception:
        return {}


def is_whatsapp_enabled_in_settings() -> bool:
    """Return True when WhatsApp is connected in app settings."""
    from distr.core.kanban.whatsapp_relay_sync import is_whatsapp_account_connected

    return is_whatsapp_account_connected()


def _is_automation_workflow(workflow: AutoWorkflow) -> bool:
    marker = _json_menu_config(workflow.context_rules)
    return (
        workflow.workflow_type == "scheduled"
        and str(marker.get("decisions_surface") or "").strip().lower() == AUTOMATION_MENU_SURFACE
    )


def _automation_instruction(workflow: AutoWorkflow) -> str:
    steps = sorted(list(workflow.steps or []), key=lambda step: step.position or 0)
    if not steps:
        return ""
    return (steps[0].instruction or "").strip()


def get_snippets_for_menu(limit: int = SNIPPETS_MENU_LIMIT) -> list[tuple[int, str, str]]:
    """Return snippets for the tray menu as ``(snippet_id, label, remote_hotkey)``."""
    with get_session() as session:
        snippets = (
            session.query(Snippet)
            .order_by(Snippet.title.asc(), Snippet.id.asc())
            .limit(limit)
            .all()
        )
        return [
            (
                int(snippet.id),
                truncate_menu_title(snippet.title or "Untitled snippet", max_len=40),
                str(snippet.remote_hotkey or "").strip(),
            )
            for snippet in snippets
        ]


def _apply_menu_shortcut(
    action: QAction,
    modifier: str,
    key: str = "",
    *,
    enabled: bool = True,
) -> None:
    """Show a configured shortcut on the right side of a context-menu action."""
    if not enabled:
        action.setShortcut(QKeySequence())
        action.setShortcutVisibleInContextMenu(False)
        return
    seq = chord_to_qt_sequence(modifier, key)
    if seq:
        action.setShortcut(QKeySequence(seq))
        action.setShortcutVisibleInContextMenu(True)
    else:
        action.setShortcut(QKeySequence())
        action.setShortcutVisibleInContextMenu(False)


def _apply_menu_ptt_shortcut(action: QAction, combo_str: str, *, enabled: bool = True) -> None:
    """Show a modifier-only hold shortcut (push-to-talk / dictation)."""
    modifier = "_".join(
        mod for mod in ("control", "option", "shift", "command")
        if mod in str(combo_str or "").split("_")
    )
    _apply_menu_shortcut(action, modifier, "", enabled=enabled and bool(modifier))


def get_automations_for_menu(limit: int = AUTOMATIONS_MENU_LIMIT) -> list[tuple[str, str, bool]]:
    """Return automations as ``(automation_id, label, runnable)``."""
    with get_session() as session:
        rows = (
            session.query(AutoWorkflow)
            .order_by(desc(AutoWorkflow.modified_date))
            .limit(max(limit * 3, limit))
            .all()
        )
        items: list[tuple[str, str, bool]] = []
        for workflow in rows:
            if not _is_automation_workflow(workflow):
                continue
            automation_id = f"wf_{int(workflow.id)}"
            label = truncate_menu_title(workflow.name or "Untitled Automation", max_len=40)
            runnable = bool(_automation_instruction(workflow))
            items.append((automation_id, label, runnable))
            if len(items) >= limit:
                break
        return items


def format_skin_folder_label(folder_name: str) -> str:
    """Human-readable label for a skin folder in the tray menu."""
    folder = (folder_name or "").strip()
    if not folder or folder.lower() == "oracle":
        return "Oracle"
    return folder.replace("_", " ").replace("-", " ").strip().title() or folder


def get_available_skins_for_menu(avatars_dir: Optional[str] = None) -> list[tuple[str, str]]:
    """Return installed skins as ``(folder_name, label)`` with Oracle first."""
    base = avatars_dir or AVATARS_DIR
    try:
        all_dirs = [
            d
            for d in os.listdir(base)
            if os.path.isdir(os.path.join(base, d))
        ]
    except Exception:
        all_dirs = []
    avatars = sorted([d for d in all_dirs if d.lower() != "oracle"])
    order = ["oracle"] + avatars
    return [(name, format_skin_folder_label(name)) for name in order]


def format_board_menu_label(name: str, source: Optional[str] = None) -> str:
    """Return a tray-menu board title without embedding the provider in the label."""
    _ = source
    return truncate_menu_title(name or "Untitled board", max_len=34)


def organize_board_menu_sections(
    boards: list[tuple[str, str, str, str]],
) -> list[tuple[Optional[str], list[tuple[str, str, str, str]]]]:
    """Group ticket boards for the tray menu: local first, then Jira, then Trello."""
    local_boards = [row for row in boards if row[0] == "database"]
    jira_boards = sorted(
        [row for row in boards if row[0] == "jira"],
        key=lambda row: row[2].lower(),
    )
    trello_boards = sorted(
        [row for row in boards if row[0] == "trello"],
        key=lambda row: row[2].lower(),
    )
    sections: list[tuple[Optional[str], list[tuple[str, str, str, str]]]] = []
    if local_boards:
        sections.append((None, sorted(local_boards, key=lambda row: row[2].lower())))
    if jira_boards:
        sections.append(("Jira", jira_boards))
    if trello_boards:
        sections.append(("Trello", trello_boards))
    return sections


def get_project_linked_boards_for_menu(
    limit: int = KANBAN_BOARDS_MENU_LIMIT,
) -> list[tuple[str, str, str, str]]:
    """Return project-linked boards as ``(source, board_key, label, external_url)``."""
    with get_session() as session:
        boards = (
            session.query(KanbanBoard)
            .filter(
                KanbanBoard.archived.is_(False),
                KanbanBoard.default_project_id.isnot(None),
            )
            .order_by(KanbanBoard.name.asc(), KanbanBoard.id.asc())
            .limit(limit)
            .all()
        )
        rows: list[tuple[str, str, str, str]] = []
        for board in boards:
            src = (board.source or "database").strip().lower()
            if src in ("jira", "trello"):
                board_key = (board.external_board_id or "").strip()
                if not board_key:
                    continue
            else:
                src = "database"
                board_key = str(int(board.id))
            rows.append(
                (
                    src,
                    board_key,
                    format_board_menu_label(board.name or "Untitled board", src),
                    (board.external_url or "").strip(),
                )
            )
        return rows


class MenuTrayMixin:
    """Context-menu and system-tray handling for OracleWindow."""

    def create_menu(self):
        # Create a single menu instance that will be shared
        self.menu = QtWidgets.QMenu()

        self.listen_action = QAction("Listening", self.menu)
        self.listen_action.setCheckable(True)
        self.listen_action.setChecked(True)
        self.listen_action.triggered.connect(self.toggle_listening)
        self.menu.addAction(self.listen_action)

        # Add hands-free action after listening action
        self.hands_free_action = QAction("Hands-Free Mode: OFF", self.menu)
        self.hands_free_action.setCheckable(True)
        self.hands_free_action.setChecked(self.is_hands_free)
        self.hands_free_action.triggered.connect(self.toggle_hands_free)
        self.menu.addAction(self.hands_free_action)

        self.menu.addSeparator()

        # Stop dictating action (initially hidden, only visible when dictating)
        self.stop_dictating_action = QAction("Stop Dictating", self.menu)
        self.stop_dictating_action.triggered.connect(self.stop_dictating)
        self.stop_dictating_action.setVisible(False)
        self.menu.addAction(self.stop_dictating_action)

        self.menu.addSeparator()

        self.chat_submenu = QtWidgets.QMenu("Chat", self.menu)
        self.chat_menu_action = self.menu.addMenu(self.chat_submenu)

        self.chats_action = QAction("Manage Chats", self.chat_submenu)
        self.chats_action.triggered.connect(lambda: self._open_web_url("/chat/"))
        self.chat_submenu.addAction(self.chats_action)

        self.chat_submenu.addSeparator()

        self.new_chat_action = QAction("New Chat", self.chat_submenu)
        self.new_chat_action.triggered.connect(self.handle_new_chat)
        self.chat_submenu.addAction(self.new_chat_action)

        self.chat_submenu.addSeparator()

        self.chat_id_menu_item = QAction("No active chat", self.chat_submenu)
        self.chat_id_menu_item.setEnabled(False)
        self.chat_submenu.addAction(self.chat_id_menu_item)

        self._recent_chats_separator = self.chat_submenu.addSeparator()
        self._recent_chat_actions: list[QAction] = []
        self._recent_chats_action_group = QActionGroup(self.chat_submenu)
        self._recent_chats_action_group.setExclusive(True)

        self.chat_submenu.aboutToShow.connect(self._rebuild_recent_chat_menu_items)

        self.projects_submenu = QtWidgets.QMenu("Projects", self.menu)
        self.projects_menu_action = self.menu.addMenu(self.projects_submenu)
        self.manage_projects_action = QAction("Manage Projects", self.projects_submenu)
        self.manage_projects_action.triggered.connect(lambda: self._open_web_url("/projects/"))
        self.projects_submenu.addAction(self.manage_projects_action)
        self._projects_list_separator = self.projects_submenu.addSeparator()
        self._project_menu_actions: list[QAction] = []
        self.projects_submenu.aboutToShow.connect(self._rebuild_project_menu_items)

        self.actions_submenu = QtWidgets.QMenu("Actions", self.menu)
        self.actions_menu_action = self.menu.addMenu(self.actions_submenu)

        self.record_action_action = QAction("Start Recording", self.actions_submenu)
        self.record_action_action.triggered.connect(self.start_recording_action)
        self.actions_submenu.addAction(self.record_action_action)

        self.stop_recording_action = QAction("Stop Recording", self.actions_submenu)
        self.stop_recording_action.triggered.connect(self.stop_recording_action_handler)
        self.stop_recording_action.setVisible(False)
        self.actions_submenu.addAction(self.stop_recording_action)

        self.actions_submenu.addSeparator()

        self.manage_actions_action = QAction("Manage Actions", self.actions_submenu)
        self.manage_actions_action.triggered.connect(lambda: self._open_web_url("/actions/"))
        self.actions_submenu.addAction(self.manage_actions_action)

        self._recent_actions_separator = self.actions_submenu.addSeparator()
        self._recent_action_actions: list[QAction] = []
        self.actions_submenu.aboutToShow.connect(self._rebuild_recent_action_menu_items)

        self.snippets_submenu = QtWidgets.QMenu("Snippets", self.menu)
        self.snippets_menu_action = self.menu.addMenu(self.snippets_submenu)
        self.manage_snippets_action = QAction("Manage Snippets", self.snippets_submenu)
        self.manage_snippets_action.triggered.connect(lambda: self._open_web_url("/snippets/"))
        self.snippets_submenu.addAction(self.manage_snippets_action)
        self._snippets_list_separator = self.snippets_submenu.addSeparator()
        self._snippet_menu_actions: list[QAction] = []
        self.snippets_submenu.aboutToShow.connect(self._rebuild_snippet_menu_items)

        self.automations_submenu = QtWidgets.QMenu("Automations", self.menu)
        self.automations_menu_action = self.menu.addMenu(self.automations_submenu)
        self.manage_automations_action = QAction("Manage Automations", self.automations_submenu)
        self.manage_automations_action.triggered.connect(
            lambda: self._open_web_url("/automations/")
        )
        self.automations_submenu.addAction(self.manage_automations_action)
        self._automations_list_separator = self.automations_submenu.addSeparator()
        self._automation_menu_actions: list[QAction] = []
        self.automations_submenu.aboutToShow.connect(self._rebuild_automation_menu_items)

        self.kanban_submenu = QtWidgets.QMenu("Ticket Boards", self.menu)
        self.kanban_menu_action = self.menu.addMenu(self.kanban_submenu)
        self.manage_kanban_action = QAction("Manage Ticket Boards", self.kanban_submenu)
        self.manage_kanban_action.triggered.connect(lambda: self._open_web_url("/tickets/"))
        self.kanban_submenu.addAction(self.manage_kanban_action)
        self._kanban_whatsapp_separator = self.kanban_submenu.addSeparator()
        self.kanban_manage_messages_action = QAction("Manage Messages", self.kanban_submenu)
        self.kanban_manage_messages_action.triggered.connect(
            lambda: self._open_web_url("/tickets/?tab=messages")
        )
        self.kanban_submenu.addAction(self.kanban_manage_messages_action)
        self.kanban_sync_messages_action = QAction("Sync Messages", self.kanban_submenu)
        self.kanban_sync_messages_action.triggered.connect(self._sync_whatsapp_messages_from_menu)
        self.kanban_submenu.addAction(self.kanban_sync_messages_action)
        self._kanban_list_separator = self.kanban_submenu.addSeparator()
        self._kanban_board_menu_actions: list[QAction] = []
        self.kanban_submenu.aboutToShow.connect(self._rebuild_kanban_menu_items)

        self.step_runner_action = QAction("Workflows/Loops", self.menu)
        self.step_runner_action.triggered.connect(lambda: self._open_web_url("/workflows/"))
        self.menu.addAction(self.step_runner_action)

        self.menu.addSeparator()

        self.skin_submenu = QtWidgets.QMenu("Skin", self.menu)
        self.skin_menu_action = self.menu.addMenu(self.skin_submenu)
        self.manage_skins_action = QAction("Manage Skins", self.skin_submenu)
        self.manage_skins_action.triggered.connect(lambda: self._open_web_url("/settings#skins"))
        self.skin_submenu.addAction(self.manage_skins_action)
        self.skin_submenu.addSeparator()
        self.toggle_visibility_skin_action = QAction("Hide Avatar", self.skin_submenu)
        self.toggle_visibility_skin_action.triggered.connect(self.toggle_visibility)
        self.skin_submenu.addAction(self.toggle_visibility_skin_action)
        self.skin_submenu.addSeparator()
        self.skin_prev_action = QAction("Previous Skin", self.skin_submenu)
        self.skin_prev_action.triggered.connect(self._skin_previous_from_menu)
        self.skin_submenu.addAction(self.skin_prev_action)
        self.skin_next_action = QAction("Next Skin", self.skin_submenu)
        self.skin_next_action.triggered.connect(self._skin_next_from_menu)
        self.skin_submenu.addAction(self.skin_next_action)
        self.oracle_prev_bg_action = QAction("Previous Oracle Background", self.skin_submenu)
        self.oracle_prev_bg_action.triggered.connect(self._oracle_background_previous_from_menu)
        self.skin_submenu.addAction(self.oracle_prev_bg_action)
        self.oracle_next_bg_action = QAction("Next Oracle Background", self.skin_submenu)
        self.oracle_next_bg_action.triggered.connect(self._oracle_background_next_from_menu)
        self.skin_submenu.addAction(self.oracle_next_bg_action)
        self.skin_decrease_size_action = QAction("Decrease Size", self.skin_submenu)
        self.skin_decrease_size_action.triggered.connect(self._skin_decrease_size_from_menu)
        self.skin_submenu.addAction(self.skin_decrease_size_action)
        self.skin_increase_size_action = QAction("Increase Size", self.skin_submenu)
        self.skin_increase_size_action.triggered.connect(self._skin_increase_size_from_menu)
        self.skin_submenu.addAction(self.skin_increase_size_action)
        self._skin_list_separator = self.skin_submenu.addSeparator()
        self._skin_menu_actions: list[QAction] = []
        self._skins_action_group = QActionGroup(self.skin_submenu)
        self._skins_action_group.setExclusive(True)
        self.skin_submenu.aboutToShow.connect(self._update_skin_submenu_items)

        self.preferences_submenu = QtWidgets.QMenu("Preferences", self.menu)
        self.preferences_menu_action = self.menu.addMenu(self.preferences_submenu)
        self.manage_preferences_action = QAction("Manage Preferences", self.preferences_submenu)
        self.manage_preferences_action.triggered.connect(
            lambda: self._open_web_url("/settings#general")
        )
        self.preferences_submenu.addAction(self.manage_preferences_action)
        self.preferences_submenu.addSeparator()
        preference_section_groups = [
            [
                ("General", "/settings#general"),
                ("API Keys", "/settings#thirdparty"),
                ("Audio", "/settings#audio"),
                ("LLMs", "/settings#llms"),
                ("Initiative", "/settings#initiative"),
            ],
            [
                ("Shortcuts", "/settings#shortcuts"),
                ("Skins", "/settings#skins"),
            ],
            [
                ("Advanced", "/settings#advanced"),
                ("MCP Servers", "/settings#mcp"),
                ("Activity Logs", "/settings#logs"),
            ],
            [
                ("Download Manager", "/downloads/"),
                ("Mermaid JS Viewer", "/diagram/"),
            ],
        ]
        self._preferences_section_actions: list[QAction] = []
        for group_index, group in enumerate(preference_section_groups):
            if group_index > 0:
                self.preferences_submenu.addSeparator()
            for label, url in group:
                action = QAction(label, self.preferences_submenu)
                action.triggered.connect(lambda checked=False, path=url: self._open_web_url(path))
                self.preferences_submenu.addAction(action)
                self._preferences_section_actions.append(action)

        self.menu.addSeparator()

        self.about_action = QAction("About DecisionsAI", self.menu)
        self.about_action.triggered.connect(self._show_about_from_menu)
        self.menu.addAction(self.about_action)

        self.menu.addSeparator()

        self.exit_action = QAction("Quit", self.menu)
        # QAction.triggered emits (checked: bool); do not pass it to exit_app(confirm=...)
        self.exit_action.triggered.connect(lambda: self.exit_app())
        self.menu.addAction(self.exit_action)

        # Connect the aboutToShow signal to update the menu
        self.menu.aboutToShow.connect(self.update_menu)

        self._snippet_focus_timer = QtCore.QTimer(self)
        self._snippet_focus_timer.setInterval(1500)
        self._snippet_focus_timer.timeout.connect(remember_frontmost_app_if_external)
        self._snippet_focus_timer.start()

        return self.menu

    def _show_about_from_menu(self) -> None:
        """Show About window and play splash sound (same as chat llama click)."""
        try:
            signal_manager.show_about_window.emit()
        except Exception as exc:
            logger.error("Failed to show about window from menu: %s", exc, exc_info=True)

    def toggle_listening(self):
        if self.listen_action.isChecked():
            self.enable_tray()
        else:
            self.disable_tray()
        self.save_listening_state()

    def on_eula_accepted(self):
        """Handle EULA acceptance - update menu to enable all features."""
        import time
        logging.info("EULA accepted, updating oracle menu to enable all features")
        # Force reload settings from database to get latest EULA status
        time.sleep(0.1)  # Small delay to ensure database commit completes
        self.settings = load_settings_from_db()
        eula_status = self.settings.get('accepted_eula', False)
        logging.info(f"EULA status after reload: {eula_status}")
        # Force menu update to enable all actions
        self.update_menu()
        # Also update the tray icon menu if it exists
        if hasattr(self, 'tray_icon') and self.tray_icon and self.tray_icon.contextMenu():
            self.tray_icon.contextMenu().update()

    def _get_skin_display_name(self) -> str:
        """Get the display name for context menu items from the active skin."""
        skin_name = None
        if hasattr(self, '_skin_config') and self._skin_config is not None:
            skin_name = self._skin_config.name
        return get_skin_display_name(skin_name)

    def update_menu(self):
        # Don't update menu during exit
        if hasattr(self, 'is_exiting') and self.is_exiting:
            return

        remember_frontmost_app_if_external()

        # Always load fresh settings from DB to ensure we have latest EULA status
        fresh_settings = load_settings_from_db()
        eula_accepted = fresh_settings.get('accepted_eula', False)
        logging.debug(f"update_menu: eula_accepted={eula_accepted} (from fresh DB load)")

        # Update cached settings
        self.settings = fresh_settings

        # Update skin submenu labels from active skin name
        self._update_skin_submenu_items()

        # Enable/disable features based on EULA acceptance
        features_requiring_eula = [
            self.actions_menu_action,
            self.record_action_action,
            self.manage_actions_action,
            self.chat_menu_action,
            self.new_chat_action,
            self.chats_action,
            self.snippets_menu_action,
            self.manage_snippets_action,
            self.automations_menu_action,
            self.manage_automations_action,
            self.kanban_menu_action,
            self.manage_kanban_action,
            self.kanban_manage_messages_action,
            self.kanban_sync_messages_action,
            self.step_runner_action,
            self.projects_menu_action,
            self.manage_projects_action,
            self.skin_menu_action,
            self.manage_skins_action,
            self.toggle_visibility_skin_action,
            self.skin_prev_action,
            self.skin_next_action,
            self.oracle_prev_bg_action,
            self.oracle_next_bg_action,
            self.skin_decrease_size_action,
            self.skin_increase_size_action,
            self.preferences_menu_action,
            self.manage_preferences_action,
            *self._preferences_section_actions,
            self.hands_free_action,
        ]

        # Hands-free availability depends on listening state
        self.hands_free_action.setEnabled(eula_accepted and self.is_listening)

        for action in features_requiring_eula:
            if action != self.hands_free_action:  # Handle hands_free_action separately
                action.setEnabled(eula_accepted)

        # If EULA not accepted, add tooltips explaining why
        if not eula_accepted:
            tooltip = "Accept EULA in Preferences to enable this feature"
            for action in features_requiring_eula:
                if action != self.hands_free_action:
                    action.setToolTip(tooltip)
        else:
            # Clear tooltips when EULA is accepted, set hands-free specific tooltip
            for action in features_requiring_eula:
                if action != self.hands_free_action:
                    action.setToolTip("")

            # Set hands-free specific tooltip when listening is disabled
            if not self.is_listening:
                self.hands_free_action.setToolTip("Enable listening first to use hands-free mode")
            else:
                self.hands_free_action.setToolTip("")

        # Check for unsubmitted new chat and disable "New Chat" if exists
        try:
            if self._has_unsubmitted_new_chat():
                self.new_chat_action.setEnabled(False)
                self.new_chat_action.setToolTip("Complete the current new chat first")
            elif eula_accepted:
                # Only re-enable if EULA is accepted
                self.new_chat_action.setEnabled(True)
                self.new_chat_action.setToolTip("")
        except Exception as e:
            logger.error(f"Error checking unsubmitted chat in menu update: {e}")

        # Update recording menu state
        self._update_recording_menu_state()

        # Update dictation menu state
        self._update_dictation_menu_state()

        self._refresh_menu_shortcuts()

        self._update_chat_id_display(
            self.chat_manager.get_current_chat() if getattr(self, "chat_manager", None) else None
        )

    def _refresh_menu_shortcuts(self) -> None:
        """Apply configured shortcut labels to context-menu actions."""
        settings = getattr(self, "settings", None) or load_settings_from_db()

        ptt_enabled = bool(settings.get("global_ptt_hotkey_enabled", True))
        ptt_combo = str(
            settings.get("global_ptt_hotkey_combo", HOTKEY_DEFAULTS["global_ptt_hotkey_combo"])
        )
        _apply_menu_ptt_shortcut(self.listen_action, ptt_combo, enabled=ptt_enabled)

        dict_combo = None
        if hasattr(self, "_get_dictation_hotkey_combo"):
            dict_combo = self._get_dictation_hotkey_combo()
        if dict_combo:
            mod, key = dict_combo
            _apply_menu_shortcut(self.stop_dictating_action, mod, key)
        else:
            _apply_menu_shortcut(self.stop_dictating_action, "", "", enabled=False)

        recording_enabled = bool(settings.get("recording_hotkey_enabled", True))
        if hasattr(self, "_get_recording_hotkey_combo"):
            rec_mod, rec_key = self._get_recording_hotkey_combo()
            _apply_menu_shortcut(
                self.record_action_action,
                rec_mod,
                rec_key,
                enabled=recording_enabled,
            )

        if hasattr(self, "_get_oracle_size_down_hotkey_combo"):
            mod, key = self._get_oracle_size_down_hotkey_combo()
            _apply_menu_shortcut(self.skin_decrease_size_action, mod, key)
        if hasattr(self, "_get_oracle_size_up_hotkey_combo"):
            mod, key = self._get_oracle_size_up_hotkey_combo()
            _apply_menu_shortcut(self.skin_increase_size_action, mod, key)

        if hasattr(self, "_get_hotkey_action_combos"):
            combos = self._get_hotkey_action_combos() or {}
            submenu_shortcuts = {
                self.chat_menu_action: "open_chat",
                self.projects_menu_action: "open_projects",
                self.actions_menu_action: "open_actions",
                self.snippets_menu_action: "open_snippets",
                self.step_runner_action: "open_workflows",
                self.preferences_menu_action: "open_preferences",
            }
            for action, combo_name in submenu_shortcuts.items():
                combo = combos.get(combo_name)
                if combo and combo[0]:
                    _apply_menu_shortcut(action, combo[0], combo[1])
                else:
                    _apply_menu_shortcut(action, "", "", enabled=False)

            prev = combos.get("skin_prev")
            nxt = combos.get("skin_next")
            if prev and prev[0]:
                _apply_menu_shortcut(self.skin_prev_action, prev[0], prev[1])
                _apply_menu_shortcut(self.oracle_prev_bg_action, prev[0], prev[1])
            if nxt and nxt[0]:
                _apply_menu_shortcut(self.skin_next_action, nxt[0], nxt[1])
                _apply_menu_shortcut(self.oracle_next_bg_action, nxt[0], nxt[1])

    def _update_chat_id_display(self, chat_id: Optional[int]) -> None:
        """Update the disabled chat ID row inside the Chat submenu."""
        if not getattr(self, "chat_id_menu_item", None):
            return
        self.chat_id_menu_item.setText(format_chat_short_hash(chat_id))

    def _rebuild_recent_chat_menu_items(self) -> None:
        """Refresh the five most recent chats with a tick on the active one."""
        if not getattr(self, "chat_submenu", None):
            return

        for action in self._recent_chat_actions:
            self._recent_chats_action_group.removeAction(action)
            self.chat_submenu.removeAction(action)
            action.deleteLater()
        self._recent_chat_actions.clear()

        current_chat_id = None
        if getattr(self, "chat_manager", None):
            current_chat_id = self.chat_manager.get_current_chat()

        self._update_chat_id_display(current_chat_id)

        try:
            recent_chats = get_recent_chats_for_menu(RECENT_CHATS_MENU_LIMIT)
        except Exception as e:
            logger.error("Failed to load recent chats for menu: %s", e)
            recent_chats = []

        self._recent_chats_separator.setVisible(bool(recent_chats))

        eula_accepted = bool((getattr(self, "settings", None) or {}).get("accepted_eula", False))

        for chat_id, title in recent_chats:
            action = QAction(title, self.chat_submenu)
            action.setCheckable(True)
            action.setChecked(chat_id == current_chat_id)
            action.setEnabled(eula_accepted)
            action.setActionGroup(self._recent_chats_action_group)
            action.triggered.connect(
                lambda checked=False, cid=chat_id: self._load_chat_from_menu(cid)
            )
            self.chat_submenu.addAction(action)
            self._recent_chat_actions.append(action)

    def _rebuild_project_menu_items(self) -> None:
        """Refresh all projects with a tick when startup terminals are running."""
        if not getattr(self, "projects_submenu", None):
            return

        for action in self._project_menu_actions:
            self.projects_submenu.removeAction(action)
            action.deleteLater()
        self._project_menu_actions.clear()

        try:
            projects = get_projects_for_menu()
        except Exception as e:
            logger.error("Failed to load projects for menu: %s", e)
            projects = []

        self._projects_list_separator.setVisible(bool(projects))

        eula_accepted = bool((getattr(self, "settings", None) or {}).get("accepted_eula", False))

        for project_id, title, _has_startup, terminals_running in projects:
            item = QAction(title, self.projects_submenu)
            item.setCheckable(True)
            item.setChecked(terminals_running)
            item.setEnabled(eula_accepted)
            item.triggered.connect(
                lambda checked=False, pid=project_id: self._handle_project_from_menu(pid)
            )
            self.projects_submenu.addAction(item)
            self._project_menu_actions.append(item)

    def _handle_project_from_menu(self, project_id: int) -> None:
        """Start/stop startup terminals or open the project in the web UI."""
        if not self._check_eula_accepted():
            return

        try:
            if project_startup_terminals_running(project_id):
                result = stop_project_startup_terminals(project_id, announce=True)
                logger.info(
                    "Oracle menu: stopped project %s terminals stopped=%s",
                    project_id,
                    result.stopped,
                )
                return

            with get_session() as session:
                project = session.query(Project).filter(Project.id == project_id).first()
                if not project:
                    logger.warning("Oracle menu: project %s not found", project_id)
                    return
                commands = parse_startup_command_lines(project.startup_instructions or "")

            if commands:
                result = start_project_startup_terminals(project_id, announce=True)
                logger.info(
                    "Oracle menu: project %s startup result action=%s started=%s failed=%s",
                    project_id,
                    result.action,
                    result.started,
                    result.failed,
                )
                self._open_web_url(f"/projects/?project_id={project_id}&tab=startup")
                return

            self._open_web_url(f"/projects/?project_id={project_id}")
        except Exception as e:
            logger.error(
                "Oracle menu: failed to handle project %s: %s",
                project_id,
                e,
                exc_info=True,
            )

    def _load_chat_from_menu(self, chat_id: int) -> None:
        """Switch the agent to the selected chat (same path as web Load Chat)."""
        if not self._check_eula_accepted():
            return

        current_chat_id = None
        if getattr(self, "chat_manager", None):
            current_chat_id = self.chat_manager.get_current_chat()
        if current_chat_id == chat_id:
            return

        try:
            signal_manager.web_load_chat_in_agent_requested.emit(chat_id)
            logger.info("Oracle menu: loading chat %s into agent", chat_id)
        except Exception as e:
            logger.error("Oracle menu: failed to load chat %s: %s", chat_id, e, exc_info=True)

    def _rebuild_recent_action_menu_items(self) -> None:
        """Refresh the ten most recent actions for quick playback."""
        if not getattr(self, "actions_submenu", None):
            return

        self._update_recording_menu_state()

        for action in self._recent_action_actions:
            self.actions_submenu.removeAction(action)
            action.deleteLater()
        self._recent_action_actions.clear()

        try:
            recent_actions = get_recent_actions_for_menu(RECENT_ACTIONS_MENU_LIMIT)
        except Exception as e:
            logger.error("Failed to load recent actions for menu: %s", e)
            recent_actions = []

        self._recent_actions_separator.setVisible(bool(recent_actions))

        eula_accepted = bool((getattr(self, "settings", None) or {}).get("accepted_eula", False))

        for action_id, title, playable in recent_actions:
            item = QAction(title, self.actions_submenu)
            item.setEnabled(eula_accepted and playable)
            if not playable:
                item.setToolTip("This action cannot be played from the menu")
            item.triggered.connect(
                lambda checked=False, aid=action_id: self._play_action_from_menu(aid)
            )
            self.actions_submenu.addAction(item)
            self._recent_action_actions.append(item)

    def _play_action_from_menu(self, action_id: int) -> None:
        """Run a saved action (same path as the Actions page play button)."""
        if not self._check_eula_accepted():
            return

        try:
            with get_session() as session:
                action = session.query(Action).filter(Action.id == action_id).first()
                if not action:
                    logger.warning("Oracle menu: action %s not found", action_id)
                    return
                if not action_is_playable(action):
                    logger.warning("Oracle menu: action %s is not playable", action_id)
                    return
                action.last_run_date = datetime.now(timezone.utc)
                play_name = resolve_action_play_name(
                    action.id,
                    action.title,
                    action.additional_trigger_words,
                )
                session.commit()

            signal_manager.play_action_by_name.emit(play_name)
            logger.info("Oracle menu: playing action %s (%s)", action_id, play_name)
        except Exception as e:
            logger.error(
                "Oracle menu: failed to play action %s: %s",
                action_id,
                e,
                exc_info=True,
            )

    def _is_recording_active(self):
        """Check if action recording is currently active (via headless recorder host on app)."""
        try:
            app = QApplication.instance()
            if not app or not getattr(app, 'recorder_host', None):
                return False
            rp = getattr(app.recorder_host, 'recorder_process', None)
            if not rp:
                return False
            is_alive = rp.is_alive()
            return bool(is_alive) if is_alive is not None else False
        except (AttributeError, RuntimeError, TypeError):
            pass
        return False

    def _update_tray_icon(self):
        """Update tray icon based on current state (recording > listening > disabled)"""
        if not hasattr(self, 'tray_icon') or not self.tray_icon:
            return

        # Priority: recording > listening state
        if self._is_recording_active():
            icon_path = os.path.join(ICONS_DIR, "tray-recording.png")
        elif self.is_listening:
            icon_path = os.path.join(ICONS_DIR, "tray.png")
        else:
            icon_path = os.path.join(ICONS_DIR, "tray-disabled.png")

        icon = QtGui.QIcon(icon_path)
        self.tray_icon.setIcon(icon)
        logger.debug(f"Updated tray icon to: {os.path.basename(icon_path)} (recording={self._is_recording_active()}, listening={self.is_listening})")

    def _on_tray_icon_activated(self, reason):
        """Handle tray icon activation (click events)."""
        from PyQt6.QtWidgets import QSystemTrayIcon
        from PyQt6.QtCore import QTimer
        import sys

        if reason in (
            QSystemTrayIcon.ActivationReason.Context,
            QSystemTrayIcon.ActivationReason.Trigger,
        ):
            remember_frontmost_app_if_external()

        # If recording was just stopped, ignore this click (don't show menu)
        if self._recording_just_stopped:
            logger.info("Tray icon clicked right after stopping recording - ignoring (no menu shown)")
            self._recording_just_stopped = False
            original_menu = self.tray_icon.contextMenu()
            self.tray_icon.setContextMenu(None)
            QTimer.singleShot(100, lambda: self.tray_icon.setContextMenu(original_menu) if original_menu else None)
            return

        # If recording is active, stop it on ANY click (left or right)
        if self._is_recording_active():
            logger.info("Tray icon clicked while recording - stopping recording (no menu shown)")
            original_menu = self.tray_icon.contextMenu()
            self.tray_icon.setContextMenu(None)
            self.stop_recording_action_handler()
            self._recording_just_stopped = True
            QTimer.singleShot(100, lambda: self.tray_icon.setContextMenu(original_menu) if original_menu else None)
            return

        # Windows: left-click on tray should also show the context menu
        # (Qt only auto-shows it on right-click; Windows users expect left-click too)
        if sys.platform == 'win32' and reason == QSystemTrayIcon.ActivationReason.Trigger:
            if self.menu:
                self._update_recording_menu_state()
                geo = self.tray_icon.geometry()
                self.menu.popup(geo.topLeft())

    def create_tray_icon(self):
        """Create and configure the system tray icon"""
        self._update_tray_icon()
        try:
            self.tray_icon.activated.disconnect(self._on_tray_icon_activated)
        except TypeError:
            pass
        self.tray_icon.activated.connect(self._on_tray_icon_activated)
        if self.menu:
            self.tray_icon.setContextMenu(self.menu)
        self.tray_icon.show()

    def _is_oracle_skin_active(self) -> bool:
        return bool(
            hasattr(self, "_skin_config")
            and self._skin_config is not None
            and getattr(self._skin_config, "type", None) == "oracle"
        )

    def _current_skin_folder(self) -> str:
        folder = getattr(self, "_skin_folder", None)
        if folder and str(folder).strip():
            return str(folder).strip().lower()
        return "oracle"

    def _update_skin_submenu_items(self) -> None:
        if not getattr(self, "skin_submenu", None):
            return
        skin_name = self._get_skin_display_name()
        is_oracle = self._is_oracle_skin_active()
        if self.isVisible():
            self.toggle_visibility_skin_action.setText(f"Hide {skin_name}")
        else:
            self.toggle_visibility_skin_action.setText(f"Show {skin_name}")
        self.skin_prev_action.setVisible(not is_oracle)
        self.skin_next_action.setVisible(not is_oracle)
        self.oracle_prev_bg_action.setVisible(is_oracle)
        self.oracle_next_bg_action.setVisible(is_oracle)
        self._rebuild_skin_list_menu_items()

    def _rebuild_skin_list_menu_items(self) -> None:
        if not getattr(self, "skin_submenu", None):
            return

        for action in self._skin_menu_actions:
            self._skins_action_group.removeAction(action)
            self.skin_submenu.removeAction(action)
            action.deleteLater()
        self._skin_menu_actions.clear()

        try:
            skins = get_available_skins_for_menu()
        except Exception as e:
            logger.error("Failed to load skins for menu: %s", e)
            skins = [("oracle", "Oracle")]

        self._skin_list_separator.setVisible(bool(skins))
        current_folder = self._current_skin_folder()
        eula_accepted = bool((getattr(self, "settings", None) or {}).get("accepted_eula", False))

        skin_select_modifier = str(
            (getattr(self, "settings", None) or {}).get(
                "skin_select_hotkey_modifier",
                HOTKEY_DEFAULTS["skin_select_hotkey_modifier"],
            )
        )

        for index, (folder_name, label) in enumerate(skins, start=1):
            item = QAction(label, self.skin_submenu)
            item.setCheckable(True)
            item.setChecked(folder_name.lower() == current_folder)
            item.setEnabled(eula_accepted)
            item.setActionGroup(self._skins_action_group)
            if index <= 9:
                _apply_menu_shortcut(item, skin_select_modifier, str(index))
            item.triggered.connect(
                lambda checked=False, skin=folder_name: self._activate_skin_from_menu(skin)
            )
            self.skin_submenu.addAction(item)
            self._skin_menu_actions.append(item)

    def _activate_skin_from_menu(self, skin_folder: str) -> None:
        if not self._check_eula_accepted():
            return
        target = (skin_folder or "").strip()
        if not target:
            return
        if target.lower() == self._current_skin_folder():
            return
        if hasattr(self, "_on_direct_oracle_change"):
            self._on_direct_oracle_change(target)
            logger.info("Oracle menu: activated skin %s", target)

    def _skin_previous_from_menu(self) -> None:
        if not self._check_eula_accepted():
            return
        if self._is_oracle_skin_active():
            self.cycle_oracle_previous()
        else:
            self._cycle_avatar_skin(-1)

    def _skin_next_from_menu(self) -> None:
        if not self._check_eula_accepted():
            return
        if self._is_oracle_skin_active():
            self.cycle_oracle()
        else:
            self._cycle_avatar_skin(1)

    def _oracle_background_previous_from_menu(self) -> None:
        if not self._check_eula_accepted():
            return
        self.cycle_oracle_previous()

    def _oracle_background_next_from_menu(self) -> None:
        if not self._check_eula_accepted():
            return
        self.cycle_oracle()

    def _skin_decrease_size_from_menu(self) -> None:
        if not self._check_eula_accepted():
            return
        if hasattr(self, "_adjust_oracle_size_from_hotkey"):
            self._adjust_oracle_size_from_hotkey(-1)

    def _skin_increase_size_from_menu(self) -> None:
        if not self._check_eula_accepted():
            return
        if hasattr(self, "_adjust_oracle_size_from_hotkey"):
            self._adjust_oracle_size_from_hotkey(1)

    def _rebuild_snippet_menu_items(self) -> None:
        if not getattr(self, "snippets_submenu", None):
            return

        for action in self._snippet_menu_actions:
            self.snippets_submenu.removeAction(action)
            action.deleteLater()
        self._snippet_menu_actions.clear()

        try:
            snippets = get_snippets_for_menu()
        except Exception as e:
            logger.error("Failed to load snippets for menu: %s", e)
            snippets = []

        self._snippets_list_separator.setVisible(bool(snippets))
        eula_accepted = bool((getattr(self, "settings", None) or {}).get("accepted_eula", False))

        for snippet_id, title, remote_hotkey in snippets:
            # Show configured hotkeys for reference only. Do not bind QAction shortcuts:
            # ctrl+shift+1/2/3 defaults collide with the global pynput listener when the
            # menu item is clicked, causing a bare Cmd+V (or literal "v") instead of paste.
            menu_label = title
            shortcut_label = format_remote_hotkey_display(remote_hotkey)
            if shortcut_label:
                menu_label = f"{title}\t{shortcut_label}"
            item = QAction(menu_label, self.snippets_submenu)
            item.setEnabled(eula_accepted)
            item.setShortcut(QKeySequence())
            item.setShortcutVisibleInContextMenu(False)
            item.triggered.connect(
                lambda checked=False, sid=snippet_id: self._paste_snippet_from_menu(sid)
            )
            self.snippets_submenu.addAction(item)
            self._snippet_menu_actions.append(item)

    def _paste_snippet_from_menu(self, snippet_id: int) -> None:
        if not self._check_eula_accepted():
            return
        remember_frontmost_app_if_external()
        if hasattr(self, "_paste_snippet_by_hotkey"):
            self._paste_snippet_by_hotkey(int(snippet_id), restore_focus=True)

    def _rebuild_automation_menu_items(self) -> None:
        if not getattr(self, "automations_submenu", None):
            return

        for action in self._automation_menu_actions:
            self.automations_submenu.removeAction(action)
            action.deleteLater()
        self._automation_menu_actions.clear()

        try:
            automations = get_automations_for_menu()
        except Exception as e:
            logger.error("Failed to load automations for menu: %s", e)
            automations = []

        self._automations_list_separator.setVisible(bool(automations))
        eula_accepted = bool((getattr(self, "settings", None) or {}).get("accepted_eula", False))

        for automation_id, title, runnable in automations:
            item = QAction(title, self.automations_submenu)
            item.setEnabled(eula_accepted and runnable)
            if not runnable:
                item.setToolTip("This automation has no instruction to run")
            item.triggered.connect(
                lambda checked=False, aid=automation_id: self._run_automation_from_menu(aid)
            )
            self.automations_submenu.addAction(item)
            self._automation_menu_actions.append(item)

    def _parse_automation_workflow_id(self, automation_id: str) -> Optional[int]:
        raw = str(automation_id or "").strip()
        if raw.startswith("wf_"):
            raw = raw[3:]
        if not raw.isdigit():
            return None
        return int(raw)

    def _automation_dict_for_dispatch(self, workflow: AutoWorkflow) -> dict:
        return {
            "id": f"wf_{int(workflow.id)}",
            "workflow_id": int(workflow.id),
            "name": workflow.name or "Untitled Automation",
            "instruction": _automation_instruction(workflow),
        }

    def _run_automation_from_menu(self, automation_id: str) -> None:
        if not self._check_eula_accepted():
            return
        try:
            from distr.core.automation_orchestrator import dispatch_automation_to_current_chat

            workflow_id = self._parse_automation_workflow_id(automation_id)
            if workflow_id is None:
                logger.warning("Oracle menu: invalid automation id %s", automation_id)
                return
            with get_session() as session:
                workflow = session.query(AutoWorkflow).filter(
                    AutoWorkflow.id == workflow_id
                ).first()
                if not workflow or not _is_automation_workflow(workflow):
                    logger.warning("Oracle menu: automation %s not found", automation_id)
                    return
                automation = self._automation_dict_for_dispatch(workflow)
            dispatch_automation_to_current_chat(automation, manual=True, speak=True)
            logger.info("Oracle menu: ran automation %s", automation_id)
        except Exception as e:
            logger.error(
                "Oracle menu: failed to run automation %s: %s",
                automation_id,
                e,
                exc_info=True,
            )

    def _rebuild_kanban_menu_items(self) -> None:
        if not getattr(self, "kanban_submenu", None):
            return

        whatsapp_enabled = is_whatsapp_enabled_in_settings()
        self._kanban_whatsapp_separator.setVisible(whatsapp_enabled)
        self.kanban_manage_messages_action.setVisible(whatsapp_enabled)
        self.kanban_sync_messages_action.setVisible(whatsapp_enabled)

        for action in self._kanban_board_menu_actions:
            self.kanban_submenu.removeAction(action)
            action.deleteLater()
        self._kanban_board_menu_actions.clear()

        try:
            boards = get_project_linked_boards_for_menu()
        except Exception as e:
            logger.error("Failed to load ticket boards for menu: %s", e)
            boards = []

        self._kanban_list_separator.setVisible(bool(boards))
        eula_accepted = bool((getattr(self, "settings", None) or {}).get("accepted_eula", False))

        sections = organize_board_menu_sections(boards)
        for section_index, (section_header, section_boards) in enumerate(sections):
            if section_header:
                if section_index > 0:
                    separator = self.kanban_submenu.addSeparator()
                    self._kanban_board_menu_actions.append(separator)
                header = QAction(section_header, self.kanban_submenu)
                header.setEnabled(False)
                header_font = header.font()
                header_font.setBold(True)
                header.setFont(header_font)
                self.kanban_submenu.addAction(header)
                self._kanban_board_menu_actions.append(header)

            for source, board_key, title, external_url in section_boards:
                item = QAction(title, self.kanban_submenu)
                item.setEnabled(eula_accepted)
                item.triggered.connect(
                    lambda checked=False, src=source, key=board_key, url=external_url: (
                        self._open_kanban_board_from_menu(src, key, url)
                    )
                )
                self.kanban_submenu.addAction(item)
                self._kanban_board_menu_actions.append(item)

    def _sync_whatsapp_messages_from_menu(self) -> None:
        """Pull WhatsApp relay messages and speak how many new ones arrived."""
        if not self._check_eula_accepted():
            return
        try:
            from distr.core.kanban.whatsapp_relay_sync import sync_whatsapp_from_relay_and_announce

            result = sync_whatsapp_from_relay_and_announce(mark_processed=False)
            logger.info("Oracle menu: WhatsApp sync result %s", result)
        except Exception as e:
            logger.error("Oracle menu: WhatsApp sync failed: %s", e, exc_info=True)
            try:
                from distr.core.signals import speak_text_directly_event_queue

                speak_text_directly_event_queue("WhatsApp sync failed.")
            except Exception:
                pass

    def _open_kanban_board_from_menu(
        self,
        source: str,
        board_key: str,
        external_url: str = "",
    ) -> None:
        if not self._check_eula_accepted():
            return
        src = (source or "database").strip().lower()
        key = (board_key or "").strip()
        if not key:
            return
        if src in ("jira", "trello"):
            url = (
                f"/tickets/?source={quote(src)}"
                f"&board_id={quote(key, safe='')}"
                "&view=list"
            )
            if (external_url or "").strip():
                url += f"&board_url={quote(external_url.strip(), safe='')}"
            self._open_web_url(url)
            return
        self._open_web_url(f"/tickets/?board_id={quote(key, safe='')}&view=list")

    def toggle_visibility(self):
        skin_name = self._get_skin_display_name()
        if self.isVisible():
            self.hide_oracle()
            new_text = f"Show {skin_name}"
        else:
            self.show_oracle()
            new_text = f"Hide {skin_name}"
        if getattr(self, "toggle_visibility_skin_action", None):
            self.toggle_visibility_skin_action.setText(new_text)

    def _change_skin_action(self):
        """Handle 'Change {skin}' menu item.
        
        Oracle skins: cycle through GIF backgrounds.
        Avatar skins: open the Skins tab in Preferences.
        """
        if not self._check_eula_accepted():
            return
        if hasattr(self, '_skin_config') and self._skin_config and self._skin_config.type == "oracle":
            self.cycle_oracle()
        else:
            self._open_web_url("/settings#skins")
