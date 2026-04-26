"""Menu and system-tray mixin for OracleWindow.

Handles context-menu creation, tray-icon management, visibility toggling,
EULA gating of menu items, and recording/dictation menu state.
"""

import logging
import os
from typing import Optional

from PyQt6 import QtWidgets, QtGui, QtCore
from PyQt6.QtGui import QAction
from PyQt6.QtWidgets import QApplication

from distr.core.paths import ICONS_DIR
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

logger = logging.getLogger(__name__)


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

        # Create actions for functionality that should be disabled when EULA not accepted
        self.record_action_action = QAction("Start Recording", self.menu)
        self.record_action_action.triggered.connect(self.start_recording_action)
        self.menu.addAction(self.record_action_action)

        # Stop recording action (initially hidden)
        self.stop_recording_action = QAction("Stop Recording", self.menu)
        self.stop_recording_action.triggered.connect(self.stop_recording_action_handler)
        self.stop_recording_action.setVisible(False)
        self.menu.addAction(self.stop_recording_action)

        self.new_chat_action = QAction("New Chat", self.menu)
        self.new_chat_action.triggered.connect(self.handle_new_chat)
        self.menu.addAction(self.new_chat_action)

        self.menu.addSeparator()

        # Create a single chat ID menu item that will be shared between both menus
        self.chat_id_menu_item = QAction("No active chat", self.menu)
        self.chat_id_menu_item.setEnabled(False)
        self.menu.addAction(self.chat_id_menu_item)

        self.menu.addSeparator()

        self.chats_action = QAction("Chat", self.menu)
        self.chats_action.triggered.connect(lambda: self._open_web_url("/chat/"))
        self.menu.addAction(self.chats_action)

        self.projects_action = QAction("Projects", self.menu)
        self.projects_action.triggered.connect(lambda: self._open_web_url("/projects/"))
        self.menu.addAction(self.projects_action)

        self.kanban_action = QAction("Ticket Boards", self.menu)
        self.kanban_action.triggered.connect(lambda: self._open_web_url("/kanban/"))
        self.menu.addAction(self.kanban_action)

        self.actions_action = QAction("Actions", self.menu)
        self.actions_action.triggered.connect(lambda: self._open_web_url("/actions/"))
        self.menu.addAction(self.actions_action)

        self.step_runner_action = QAction("Workflows", self.menu)
        self.step_runner_action.triggered.connect(lambda: self._open_web_url("/workflows/"))
        self.menu.addAction(self.step_runner_action)

        self.skills_action = QAction("Skills", self.menu)
        self.skills_action.triggered.connect(lambda: self._open_web_url("/skills/"))
        self.menu.addAction(self.skills_action)

        self.menu.addSeparator()

        self.toggle_visibility_action = QAction("Hide Oracle", self.menu)
        self.toggle_visibility_action.triggered.connect(self.toggle_visibility)
        self.menu.addAction(self.toggle_visibility_action)

        self.change_oracle_action = QAction("Change Oracle", self.menu)
        self.change_oracle_action.triggered.connect(self._change_skin_action)
        self.menu.addAction(self.change_oracle_action)

        self.menu.addSeparator()

        self.about_action = QAction("About DecisionsAI", self.menu)
        self.about_action.triggered.connect(self.show_about_window)
        self.menu.addAction(self.about_action)

        self.api_docs_action = QAction("API Docs", self.menu)
        self.api_docs_action.triggered.connect(lambda: self._open_web_url("/docs/"))
        self.menu.addAction(self.api_docs_action)

        self.menu.addSeparator()

        self.preferences_action = QAction("Preferences", self.menu)
        self.preferences_action.triggered.connect(self.show_settings_web)
        self.menu.addAction(self.preferences_action)

        self.menu.addSeparator()

        self.log_activity_action = QAction("Activity Log", self.menu)
        self.log_activity_action.triggered.connect(lambda: self._open_web_url("/settings#logs"))
        self.menu.addAction(self.log_activity_action)

        self.restart_action = QAction("Restart", self.menu)
        self.restart_action.triggered.connect(self.restart_app)
        self.menu.addAction(self.restart_action)

        self.exit_action = QAction("Quit", self.menu)
        # QAction.triggered emits (checked: bool); do not pass it to exit_app(confirm=...)
        self.exit_action.triggered.connect(lambda: self.exit_app())
        self.menu.addAction(self.exit_action)

        # Connect the aboutToShow signal to update the menu
        self.menu.aboutToShow.connect(self.update_menu)

        return self.menu

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

        # Always load fresh settings from DB to ensure we have latest EULA status
        fresh_settings = load_settings_from_db()
        eula_accepted = fresh_settings.get('accepted_eula', False)
        logging.debug(f"update_menu: eula_accepted={eula_accepted} (from fresh DB load)")

        # Update cached settings
        self.settings = fresh_settings

        # Update context menu text based on active skin name
        skin_name = self._get_skin_display_name()
        if self.isVisible():
            self.toggle_visibility_action.setText(f"Hide {skin_name}")
        else:
            self.toggle_visibility_action.setText(f"Show {skin_name}")
        self.change_oracle_action.setText(f"Change {skin_name}")

        self.change_oracle_action.setVisible(self.oracle_visible)

        # Enable/disable features based on EULA acceptance
        features_requiring_eula = [
            self.record_action_action,
            self.new_chat_action,
            self.chats_action,
            self.kanban_action,
            self.actions_action,
            self.step_runner_action,
            self.skills_action,
            self.projects_action,
            self.change_oracle_action,
            self.about_action,
            self.hands_free_action
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
        self.tray_icon.activated.connect(self._on_tray_icon_activated)
        if self.menu:
            self.tray_icon.setContextMenu(self.menu)
        self.tray_icon.show()

    def toggle_visibility(self):
        skin_name = self._get_skin_display_name()
        if self.isVisible():
            self.hide_oracle()
            new_text = f"Show {skin_name}"
        else:
            self.show_oracle()
            new_text = f"Hide {skin_name}"
        self.toggle_visibility_action.setText(new_text)

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
