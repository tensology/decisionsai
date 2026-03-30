"""Application lifecycle mixin for OracleWindow.

Handles restart_app and exit_app.
"""

import logging
import os
import platform
import subprocess
import sys

from PyQt6 import QtCore, QtWidgets
from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QApplication

from distr.core.signals import signal_manager

logger = logging.getLogger(__name__)

# Project root: lifecycle.py is at distr/gui/oracle/lifecycle.py → 3 levels up
_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '../../..'))


class LifecycleMixin:
    """Restart and exit handling for OracleWindow."""

    def restart_app(self):
        """Restart the application by spawning a new process and quitting."""
        try:
            logger.info("[RESTART] Restart requested")

            # Save state
            self.reload_settings()
            if hasattr(self, 'save_listening_state'):
                self.save_listening_state()
            if hasattr(self, 'save_hands_free_state'):
                self.save_hands_free_state()

            # Build the command to restart
            if getattr(sys, 'frozen', False):
                # Packaged app
                if sys.platform == 'darwin':
                    cmd = ['open', '-n', sys.executable]
                else:
                    cmd = [sys.executable]
            else:
                # Running from source — use bin/start.py
                start_script = os.path.join(_PROJECT_ROOT, 'bin', 'start.py')
                cmd = [sys.executable, start_script]
                logger.info("[RESTART] cmd=%s  cwd=%s  start.py exists=%s",
                            cmd, _PROJECT_ROOT, os.path.exists(start_script))

            # Write a tiny shell/bat script that waits then launches.
            # This survives the parent process dying.
            if platform.system() == 'Windows':
                script = os.path.join(_PROJECT_ROOT, '_restart.bat')
                with open(script, 'w') as f:
                    f.write('@echo off\n')
                    f.write('timeout /t 2 /nobreak >nul\n')
                    f.write(' '.join(f'"{c}"' for c in cmd) + '\n')
                    f.write(f'del "{script}"\n')
                subprocess.Popen(
                    ['cmd', '/c', script],
                    cwd=_PROJECT_ROOT,
                    creationflags=getattr(subprocess, 'DETACHED_PROCESS', 0) | getattr(subprocess, 'CREATE_NEW_PROCESS_GROUP', 0),
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
            else:
                # Unix/macOS: use bash -c with sleep
                shell_cmd = f'sleep 2 && {" ".join(cmd)}'
                subprocess.Popen(
                    ['bash', '-c', shell_cmd],
                    cwd=_PROJECT_ROOT,
                    start_new_session=True,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )

            logger.info("[RESTART] Spawned restart process, quitting in 500ms")
            QTimer.singleShot(500, self.exit_app)

        except Exception as e:
            logger.error("[RESTART] Failed: %s", e, exc_info=True)

    def exit_app(self):
        self.reload_settings()

        # Hide the oracle window itself
        self.hide_oracle()

        # Set a flag to prevent any further actions
        self.is_exiting = True

        # Emit exit signal first to notify other components
        signal_manager.exit_app.emit()

        # Hide all windows first
        if hasattr(self, 'player_window') and self.player_window:
            self.player_window.hide()
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
