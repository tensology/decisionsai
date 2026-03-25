#!/usr/bin/env python3
"""
Simple test script to open the About window.
Closes the app when the window is closed or Ctrl+C is pressed.
"""

import sys
import signal
import os

# Try to prevent pipecat initialization - set before any imports
os.environ['SKIP_PIPECAT_INIT'] = '1'
os.environ['DISABLE_PIPECAT'] = '1'

from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import Qt

def main():
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(True)  # CRITICAL: Quit when last window closes
    
    # Handle Ctrl+C gracefully - quit the app
    def signal_handler(sig, frame):
        print("\nCtrl+C pressed, exiting...")
        app.quit()
        sys.exit(0)
    
    signal.signal(signal.SIGINT, signal_handler)
    
    # Create and show the about window
    try:
        # Import only what we need, avoid importing the whole distr package
        from distr.gui.about import AboutWindow
        about_window = AboutWindow()
        
        # Override closeEvent to ensure app quits
        original_close = about_window.closeEvent
        def closeEvent(event):
            app.quit()
            if original_close:
                original_close(event)
        
        about_window.closeEvent = closeEvent
        about_window.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        
        # Make sure window is visible and on top
        about_window.show()
        about_window.raise_()
        about_window.activateWindow()
        
        # Force window to be visible
        if hasattr(about_window, 'setWindowState'):
            about_window.setWindowState(Qt.WindowState.WindowActive)
        
        print("About window opened. Close it or press Ctrl+C to exit.")
        print(f"Window visible: {about_window.isVisible()}")
        print(f"Window size: {about_window.width()}x{about_window.height()}")
        
    except Exception as e:
        print(f"Error creating window: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
    
    # Exit when window is closed
    result = app.exec()
    print("App exiting...")
    sys.exit(result)

if __name__ == "__main__":
    main()
