import sys
import os
import logging
from PyQt6 import QtWidgets, QtCore

# Add project root to path
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '../..'))
sys.path.insert(0, project_root)

# Configure logging
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)

# Mock settings
import distr.gui.settings.tabs.thirdparty as thirdparty_module

def mock_load_settings():
    return {
        'transcription_model': 'Whisper.cpp (Local & Offline)',
        'ollama_url': 'http://localhost:11434/'
    }

def mock_save_settings(settings):
    logging.info(f"Settings saved: {settings}")

thirdparty_module.load_settings_from_db = mock_load_settings
thirdparty_module.save_settings_to_db = mock_save_settings

from distr.gui.settings.tabs.thirdparty import ThirdPartyTab

def run_test():
    app = QtWidgets.QApplication(sys.argv)
    
    window = QtWidgets.QMainWindow()
    window.setWindowTitle("Vosk Download Real Logic Test")
    window.resize(800, 600)
    
    tab = ThirdPartyTab()
    window.setCentralWidget(tab)
    window.show()
    
    print("\n" + "="*50)
    print("TEST STARTED: Triggering Vosk download with Direct Import")
    print("="*50 + "\n")
    
    def trigger_download():
        print(">>> Triggering 'Vosk (Local & Offline)' selection...")
        
        # Mock question to return Yes (for the initial "download?" prompt)
        original_question = QtWidgets.QMessageBox.question
        QtWidgets.QMessageBox.question = lambda parent, title, text, buttons, default: QtWidgets.QMessageBox.StandardButton.Yes
        
        # Force download condition (model not found)
        tab._check_vosk_model = lambda: False
        
        try:
            tab._on_transcription_model_changed("Vosk (Local & Offline)")
            print("\n>>> Manual download dialog should have appeared.")
            print(">>> Check if browser opened and dialog is showing instructions.")
        except Exception as e:
            print(f"\n!!! EXCEPTION CAUGHT: {e}")
            import traceback
            traceback.print_exc()
        finally:
            QtWidgets.QMessageBox.question = original_question

    # Trigger after 1s
    QtCore.QTimer.singleShot(1000, trigger_download)
    
    # Let it run to completion (user can manually close or it will finish when download/extraction completes)
    print("\n>>> Test running - close window or wait for completion...")
    print(">>> NOTE: This is a MANUAL download flow - it won't download automatically.")
    print(">>> The dialog should appear and browser should open for manual download.")
    
    sys.exit(app.exec())

if __name__ == "__main__":
    run_test()

