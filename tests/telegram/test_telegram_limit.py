"""
Manual upload script for Telegram file limits (not run during pytest).

Run: python tests/telegram/test_telegram_limit.py
"""

import os
import sys

from PyQt6.QtCore import QCoreApplication, QTimer
from distr.core.integrations.telegram import TelegramWebSocketManager


def main() -> None:
    app = QCoreApplication(sys.argv)
    manager = TelegramWebSocketManager()

    USER_ID = 984897897
    _downloads = os.path.join(os.path.expanduser("~"), "Downloads")
    _candidate = os.path.join(_downloads, "3 steps.mp4")
    file_path = _candidate if os.path.isfile(_candidate) else None

    def params_loaded():
        print("Connecting...")
        manager.connect(telegram_user_id=USER_ID)

    def on_connected(success, msg):
        if success:
            print("Connected! Sending file...")
            QTimer.singleShot(2000, send_file)
        else:
            print(f"Connection failed: {msg}")
            app.quit()

    def send_file():
        if not file_path:
            print(
                "No test video found. Put '3 steps.mp4' in ~/Downloads or set FILE_PATH."
            )
            app.quit()
            return
        print(f"Sending {file_path}...")
        manager.send_to_telegram(
            text="Test Upload from Fix Script", video_path=file_path
        )
        QTimer.singleShot(30000, lambda: timeout("No confirmation received"))

    def timeout(msg):
        print(msg)
        app.quit()

    manager.connection_status_changed.connect(on_connected)
    QTimer.singleShot(100, params_loaded)

    try:
        print("Starting event loop...")
        sys.exit(app.exec())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
