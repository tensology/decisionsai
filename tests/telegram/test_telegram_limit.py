
import sys
import os
import signal
import time
from pathlib import Path

# Add project root to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))

from PyQt6.QtCore import QCoreApplication, QTimer
from distr.core.integrations.telegram import TelegramWebSocketManager

# Setup basic app
app = QCoreApplication(sys.argv)

manager = TelegramWebSocketManager()

# UserId from logs
USER_ID = 984897897
# Use Downloads if available, else temp file (cross-platform)
_downloads = os.path.join(os.path.expanduser("~"), "Downloads")
_candidate = os.path.join(_downloads, "3 steps.mp4")
FILE_PATH = _candidate if os.path.isfile(_candidate) else None

def params_loaded():
    print("Connecting...")
    manager.connect(telegram_user_id=USER_ID)

def on_connected(success, msg):
    if success:
        print("Connected! Sending file...")
        # Wait a bit for subscription
        QTimer.singleShot(2000, send_file)
    else:
        print(f"Connection failed: {msg}")
        app.quit()

def send_file():
    if not FILE_PATH:
        print("No test video found. Put '3 steps.mp4' in ~/Downloads or set FILE_PATH.")
        app.quit()
        return
    print(f"Sending {FILE_PATH}...")
    manager.send_to_telegram(text="Test Upload from Fix Script", video_path=FILE_PATH)
    # Keep running to wait for confirmation
    QTimer.singleShot(30000, lambda: timeout("No confirmation received"))

def timeout(msg):
    print(msg)
    app.quit()

manager.connection_status_changed.connect(on_connected)

# Start
QTimer.singleShot(100, params_loaded)

# Run event loop
try:
    print("Starting event loop...")
    sys.exit(app.exec())
except KeyboardInterrupt:
    pass
