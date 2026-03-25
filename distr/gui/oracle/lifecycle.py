"""Application lifecycle mixin for OracleWindow.

Handles restart_app, _create_launcher_script, _quit_after_restart, and exit_app.
"""

import logging
import os
import platform
import subprocess

from PyQt6 import QtCore, QtWidgets
from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QApplication

from distr.core.signals import signal_manager

logger = logging.getLogger(__name__)


class LifecycleMixin:
    """Restart and exit handling for OracleWindow."""

    def restart_app(self):
        """Restart the application using a helper script that survives parent process death"""
        import sys
        logging.info("[ORACLE] Restart requested - creating restart launcher")

        # Save current settings before restart
        self.reload_settings()
        if hasattr(self, 'save_listening_state'):
            self.save_listening_state()
        if hasattr(self, 'save_hands_free_state'):
            self.save_hands_free_state()

        # Get the base directory (project root)
        base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '../..'))

        try:
            if getattr(sys, 'frozen', False):
                executable = sys.executable
                if sys.platform == 'darwin' and executable.endswith('.app'):
                    launcher_script = self._create_launcher_script(base_dir, ['open', executable])
                else:
                    launcher_script = self._create_launcher_script(base_dir, [executable])
            else:
                start_script = os.path.join(base_dir, "bin", "start.py")
                if os.path.exists(start_script):
                    python_exe = sys.executable
                    launcher_script = self._create_launcher_script(base_dir, [python_exe, start_script])
                else:
                    if len(sys.argv) > 0:
                        launcher_script = self._create_launcher_script(base_dir, [sys.executable] + sys.argv)
                    else:
                        logging.error("[ORACLE] Cannot restart: no script path available")
                        return

            if platform.system() != 'Windows':
                import signal as _signal

                def preexec_fn():
                    os.setsid()
                    _signal.signal(_signal.SIGHUP, _signal.SIG_IGN)

                process = subprocess.Popen(
                    ['nohup', sys.executable, launcher_script],
                    cwd=base_dir,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    preexec_fn=preexec_fn,
                    start_new_session=True
                )
            else:
                creation_flags = 0
                if hasattr(subprocess, 'DETACHED_PROCESS'):
                    creation_flags = subprocess.DETACHED_PROCESS
                elif hasattr(subprocess, 'CREATE_NEW_PROCESS_GROUP'):
                    creation_flags = subprocess.CREATE_NEW_PROCESS_GROUP

                process = subprocess.Popen(
                    [sys.executable, launcher_script],
                    cwd=base_dir,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    creationflags=creation_flags
                )

            logging.info(f"[ORACLE] Launched restart launcher (PID: {process.pid})")

            if process.poll() is not None:
                logging.error(f"[ORACLE] Launcher process exited immediately with code: {process.returncode}")
                return

            logging.info("[ORACLE] Quitting current instance - launcher will start new instance")
            QTimer.singleShot(500, self._quit_after_restart)

        except Exception as e:
            logging.error(f"[ORACLE] Error creating restart launcher: {e}", exc_info=True)

    def _create_launcher_script(self, base_dir, command):
        """Create a temporary Python script that will launch the app after a delay"""
        import tempfile
        import sys

        fd, script_path = tempfile.mkstemp(suffix='.py', prefix='decisions_restart_', dir=base_dir)

        try:
            script_content = f"""#!/usr/bin/env python3
import subprocess
import sys
import time
import os

# Wait a moment for the old process to fully exit
time.sleep(2)

# Launch the application
command = {repr(command)}
base_dir = {repr(base_dir)}

try:
    import platform
    if sys.platform == 'darwin' and command[0] == 'open':
        subprocess.Popen(command, cwd=base_dir)
    elif platform.system() == 'Windows':
        creation_flags = 0
        if hasattr(subprocess, 'DETACHED_PROCESS'):
            creation_flags = subprocess.DETACHED_PROCESS
        elif hasattr(subprocess, 'CREATE_NEW_PROCESS_GROUP'):
            creation_flags = subprocess.CREATE_NEW_PROCESS_GROUP
        subprocess.Popen(command, cwd=base_dir, creationflags=creation_flags)
    else:
        subprocess.Popen(command, cwd=base_dir, start_new_session=True)
    print(f"Restarted application: {{' '.join(command)}}")
except Exception as e:
    print(f"Error restarting application: {{e}}", file=sys.stderr)
    sys.exit(1)
finally:
    try:
        script_path = os.path.abspath(__file__)
        if os.path.exists(script_path):
            os.unlink(script_path)
    except OSError:
        pass
"""
            with os.fdopen(fd, 'w') as f:
                f.write(script_content)

            if platform.system() != 'Windows':
                os.chmod(script_path, 0o755)

            logging.info(f"[ORACLE] Created restart launcher script: {script_path}")
            return script_path

        except Exception as e:
            try:
                os.close(fd)
            except OSError:
                pass
            try:
                os.unlink(script_path)
            except OSError:
                pass
            raise

    def _quit_after_restart(self):
        """Quit the current instance after restart has been initiated"""
        logging.info("[ORACLE] Quitting current instance after restart")
        self.exit_app()

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
        logging.info("[ORACLE] Calling QApplication.instance().quit()")
        QApplication.instance().quit()
