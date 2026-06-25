"""Application lifecycle mixin for OracleWindow.

Handles restart_app and exit_app.
"""

import logging
import os

from PyQt6 import QtCore, QtWidgets
from PyQt6.QtCore import QTimer
from PyQt6.QtGui import QIcon, QPixmap
from PyQt6.QtWidgets import QApplication

from distr.core.paths import ASSETS_DIR, ICONS_DIR
from distr.core.runtime_lifecycle import append_runtime_event, write_exit_intent
from distr.core.signals import signal_manager

logger = logging.getLogger(__name__)


def _quit_confirmation_icon_path():
    """Path to favicon for quit dialog; falls back to tray icon."""
    for candidate in (
        os.path.join(ASSETS_DIR, "icons", "favicon.png"),
        os.path.join(ASSETS_DIR, "icons", "decisions.ico"),
        os.path.join(ICONS_DIR, "tray.png"),
    ):
        if os.path.isfile(candidate):
            return candidate
    return None


def _center_dialog_position(
    *,
    screen_geometry: tuple[int, int, int, int],
    dialog_size: tuple[int, int],
) -> tuple[int, int]:
    """Return a top-left dialog position centered inside a screen geometry."""
    screen_x, screen_y, screen_w, screen_h = screen_geometry
    dialog_w, dialog_h = dialog_size
    x_offset = max(0, (int(screen_w) - int(dialog_w)) // 2)
    y_offset = max(0, (int(screen_h) - int(dialog_h)) // 2)
    return int(screen_x) + x_offset, int(screen_y) + y_offset


class LifecycleMixin:
    """Restart and exit handling for OracleWindow."""

    def _oracle_target_screen(self):
        """Return the screen containing the oracle's current/last geometry."""
        app = QApplication.instance()
        try:
            center = self.frameGeometry().center()
            target_screen = QApplication.screenAt(center)
            if target_screen is not None:
                return target_screen
        except Exception:
            pass
        try:
            if self.windowHandle() is not None:
                target_screen = self.windowHandle().screen()
                if target_screen is not None:
                    return target_screen
        except Exception:
            pass
        try:
            target_screen = self.screen()
            if target_screen is not None:
                return target_screen
        except Exception:
            pass
        try:
            return app.primaryScreen() if app is not None else None
        except Exception:
            return None

    def _position_dialog_on_oracle_screen(self, dialog: QtWidgets.QDialog) -> None:
        """Center a dialog on the same screen as the oracle window."""
        try:
            target_screen = self._oracle_target_screen()
            if target_screen is None:
                return

            geom = target_screen.availableGeometry()
            # Ensure the dialog has a real size before centering.
            dialog.adjustSize()
            if dialog.windowHandle() is not None:
                dialog.windowHandle().setScreen(target_screen)
            dialog.move(
                *_center_dialog_position(
                    screen_geometry=(geom.x(), geom.y(), geom.width(), geom.height()),
                    dialog_size=(dialog.width(), dialog.height()),
                )
            )
        except Exception as e:
            logger.debug("[EXIT] Failed positioning quit dialog on oracle screen: %s", e)

    def _keep_dialog_on_oracle_screen(self, dialog: QtWidgets.QDialog) -> None:
        """Re-apply placement after native modal show logic has run."""
        self._position_dialog_on_oracle_screen(dialog)
        QTimer.singleShot(0, lambda: self._position_dialog_on_oracle_screen(dialog))
        QTimer.singleShot(75, lambda: self._position_dialog_on_oracle_screen(dialog))

    def _force_hide_oracle_for_exit(self):
        """Hide avatar/oracle immediately (not deferred) before shutdown steps."""
        try:
            self.oracle_visible = False
        except Exception:
            pass
        try:
            self.hide()
        except Exception:
            pass
        try:
            if hasattr(self, "player_window") and self.player_window:
                self.player_window.hide()
        except Exception:
            pass
        QtCore.QCoreApplication.processEvents()

    def _dismiss_blocking_popups_for_exit(self):
        """Close modal dialogs/popups that can deadlock shutdown."""
        app = QApplication.instance()
        if not app:
            return

        # Close the currently active modal first (if any).
        try:
            active_modal = app.activeModalWidget()
            if active_modal is not None and active_modal is not self:
                active_modal.hide()
                active_modal.close()
        except Exception as e:
            logger.debug("[EXIT] Failed closing active modal: %s", e)

        # Then close any remaining visible top-level modal/dialog popups.
        try:
            for widget in app.topLevelWidgets():
                if widget is self:
                    continue
                if not widget.isVisible():
                    continue
                try:
                    is_dialog = isinstance(widget, QtWidgets.QDialog)
                    is_popup = bool(widget.windowModality() != QtCore.Qt.WindowModality.NonModal)
                    if is_dialog or is_popup:
                        widget.hide()
                        widget.close()
                except Exception:
                    continue
        except Exception as e:
            logger.debug("[EXIT] Failed scanning top-level widgets: %s", e)

    def restart_app(self):
        """Restart the application by spawning a new process and quitting."""
        try:
            from distr.core.app_restart import spawn_restart_process

            logger.info("[RESTART] Restart requested")
            self._restart_in_progress = True
            write_exit_intent("restart_app", source="oracle.restart_app", expected_restart=True)
            append_runtime_event("restart_requested", source="oracle.restart_app")

            # Save state
            self.reload_settings()
            if hasattr(self, 'save_listening_state'):
                self.save_listening_state()
            if hasattr(self, 'save_hands_free_state'):
                self.save_hands_free_state()

            spawn_restart_process()

            logger.info("[RESTART] Spawned detached restart process, quitting in 1500ms")
            QTimer.singleShot(1500, lambda: self.exit_app(confirm=False))

        except Exception as e:
            logger.error("[RESTART] Failed: %s", e, exc_info=True)

    def exit_app(self, confirm: bool = True):
        if confirm:
            msg = QtWidgets.QMessageBox(self)
            msg.setWindowTitle("Quit")
            msg.setText("Quit DecisionsAI?")
            msg.setIcon(QtWidgets.QMessageBox.Icon.NoIcon)
            icon_path = _quit_confirmation_icon_path()
            if icon_path:
                pm = QPixmap(icon_path)
                if not pm.isNull():
                    scaled = pm.scaled(
                        48,
                        48,
                        QtCore.Qt.AspectRatioMode.KeepAspectRatio,
                        QtCore.Qt.TransformationMode.SmoothTransformation,
                    )
                    msg.setIconPixmap(scaled)
                    msg.setWindowIcon(QIcon(pm))
            msg.setStandardButtons(
                QtWidgets.QMessageBox.StandardButton.Yes
                | QtWidgets.QMessageBox.StandardButton.No
            )
            msg.setDefaultButton(QtWidgets.QMessageBox.StandardButton.No)
            self._keep_dialog_on_oracle_screen(msg)
            if msg.exec() != QtWidgets.QMessageBox.StandardButton.Yes:
                return

        # Shutdown order matters:
        # 1) Hide oracle/avatar UI immediately
        # 2) Close modal/popups that can hold the event loop
        # 3) Continue with app-wide shutdown
        self._force_hide_oracle_for_exit()
        self._dismiss_blocking_popups_for_exit()
        QtCore.QCoreApplication.processEvents()

        self.reload_settings()

        # Set a flag to prevent any further actions
        self.is_exiting = True
        if not getattr(self, "_restart_in_progress", False):
            write_exit_intent("exit_app", source="oracle.exit_app", expected_restart=False)
        append_runtime_event(
            "exit_requested",
            source="oracle.exit_app",
            confirm=confirm,
            restart_in_progress=bool(getattr(self, "_restart_in_progress", False)),
        )

        # Emit exit signal first to notify other components
        signal_manager.exit_app.emit()

        # Hide all windows first
        if hasattr(self, 'player_window') and self.player_window:
            self.player_window.hide()
        if hasattr(self, 'shutdown_global_ptt_hotkey'):
            self.shutdown_global_ptt_hotkey()
        if hasattr(self, 'about_window') and self.about_window:
            self.about_window.hide()
        # Explicitly clean up any animation resources
        if hasattr(self, 'animation_group') and self.animation_group:
            self.animation_group.stop()
        if hasattr(self, 'movie') and self.movie:
            self.movie.stop()
        # Clean up skin-driven components
        if hasattr(self, '_glow_engine'):
            self._glow_engine.stop()
        if hasattr(self, '_animation_player'):
            self._animation_player.stop()
        if hasattr(self, '_webm_view') and self._webm_view is not None:
            self._webm_view.stop()
        if hasattr(self, '_chat_bubble'):
            self._chat_bubble.hide_bubble()

        # Stop tray icon
        if hasattr(self, 'tray_icon') and self.tray_icon:
            self.tray_icon.hide()

        # Give time for signal processing and cleanup
        QtCore.QCoreApplication.processEvents()

        # Finally quit the application
        logger.info("[ORACLE] Calling QApplication.instance().quit()")
        QApplication.instance().quit()
