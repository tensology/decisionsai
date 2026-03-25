#!/usr/bin/env python3

"""
Simple test to see if pynput listeners work in a separate thread with Qt.
"""

import sys
import os
import time
import threading
from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import QTimer
from pynput import keyboard, mouse

def test_pynput_in_thread_with_qt():
    """Test if pynput listeners work in a separate thread with Qt"""
    print("Starting pynput listeners in separate thread with Qt...")

    app = QApplication.instance()
    if not app:
        app = QApplication([])

    def listener_thread():
        try:
            print("Thread: Starting keyboard listener...")
            keyboard_listener = keyboard.Listener(
                on_press=lambda key: print(f"Key pressed: {key}"),
                on_release=lambda key: print(f"Key released: {key}")
            )
            keyboard_listener.start()

            print("Thread: Starting mouse listener...")
            mouse_listener = mouse.Listener(
                on_move=lambda x, y: None,
                on_click=lambda x, y, button, pressed: None
            )
            mouse_listener.start()

            print("Thread: Listeners started successfully")
            time.sleep(5)  # Keep running for 5 seconds

            print("Thread: Stopping listeners...")
            keyboard_listener.stop()
            mouse_listener.stop()
            print("Thread: Listeners stopped")

        except Exception as e:
            print(f"Thread: Error: {e}")
            import traceback
            traceback.print_exc()

    # Start the thread
    thread = threading.Thread(target=listener_thread, daemon=True)
    thread.start()

    # Process Qt events
    QTimer.singleShot(100, app.quit)
    app.exec()

    # Wait for the thread to complete
    thread.join()
    print("Main: Thread completed")

if __name__ == '__main__':
    test_pynput_in_thread_with_qt()