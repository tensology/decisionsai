"""
Main Settings Window Implementation

This module provides the SettingsWindow class which implements the main settings window
of the application. It handles the integration of all settings tabs and provides
a unified interface for managing application settings.

Key Features:
- Tab-based settings interface
- Settings persistence
- Window management
- Signal handling
"""

from PyQt6 import QtWidgets, QtGui, QtCore
from PyQt6.QtCore import Qt
from distr.gui.settings.tabs.general import GeneralTab
from distr.gui.settings.tabs.audio import AudioTab
from distr.gui.settings.tabs.ai import AITab
from distr.gui.settings.tabs.advanced import AdvancedTab
from distr.gui.settings.tabs.eula import EulaTab
from distr.gui.settings.utils.settings import load_settings_from_db, save_settings_to_db
from distr.core.signals import signal_manager
import logging
import sys

class SettingsWindow(QtWidgets.QMainWindow):
    """
    Main settings window implementation.
    
    This class provides the main window for managing application settings,
    integrating all settings tabs and handling window management.
    """
    
    def __init__(self, parent=None):
        """
        Initialize the settings window.
        
        Args:
            parent (QWidget, optional): Parent widget
        """
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.setMinimumSize(800, 600)
        
        # Flag to prevent showing EULA message multiple times
        self.showing_eula_message = False
        
        # Set window flags to make it a floating window
        self.setWindowFlags(
            Qt.WindowType.Window |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.CustomizeWindowHint |
            Qt.WindowType.WindowCloseButtonHint
        )
        
        # Set window style
        self.setStyleSheet("""
            QMainWindow {
                background-color: #f8f9fa;
            }
            QTabWidget::pane {
                border: 1px solid #dee2e6;
                border-radius: 4px;
                background-color: white;
            }
            QTabBar::tab {
                background-color: #e9ecef;
                border: 1px solid #dee2e6;
                border-bottom: none;
                border-top-left-radius: 4px;
                border-top-right-radius: 4px;
                padding: 8px 16px;
                margin-right: 2px;
            }
            QTabBar::tab:selected {
                background-color: white;
                border-bottom: 1px solid white;
                margin-bottom: -1px;
            }
            QTabBar::tab:hover {
                background-color: #dee2e6;
            }
            QTabBar::tab:disabled {
                color: #adb5bd;
                background-color: #e9ecef;
            }
        """)
        
        self._setup_ui()
        logging.debug("SettingsWindow initialized")
        
    def closeEvent(self, event):
        """Override close event to prevent closing if EULA is not accepted."""
        # Check if EULA is accepted
        settings = load_settings_from_db()
        if not settings.get('accepted_eula', False):
            # Force the user back to the EULA tab
            self.tab_widget.setCurrentIndex(0)
            
            if not self.showing_eula_message:
                self.showing_eula_message = True
                # Show a simple message about requiring EULA acceptance
                QtWidgets.QMessageBox.information(
                    self,
                    "EULA Required",
                    "You must accept the EULA to use this application. "
                    "Please review and accept the terms to continue.",
                    QtWidgets.QMessageBox.StandardButton.Ok
                )
                self.showing_eula_message = False
            
            # Prevent window from closing
            event.ignore()
        else:
            # If EULA is accepted, just hide the window
            event.ignore()
            self.hide()
            logging.debug("Settings window hidden instead of closed")
        
    def showEvent(self, event):
        """Override show event to log EULA state when window is shown"""
        super().showEvent(event)
        
        # Get EULA status when window is shown
        settings = load_settings_from_db()
        eula_accepted = settings.get('accepted_eula', False)
        
        # Check if EULA tab checkbox matches database
        checkbox_state = self.eula_tab.accept_checkbox.isChecked()
        
        # If they don't match, reconcile the difference
        if checkbox_state != eula_accepted:
            # Force the checkbox to match the database
            self.eula_tab.accept_checkbox.setChecked(eula_accepted)
        
    def _setup_ui(self):
        """Set up the UI components."""
        # Create central widget and main layout
        central_widget = QtWidgets.QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QtWidgets.QVBoxLayout(central_widget)
        
        # Create tab widget
        self.tab_widget = QtWidgets.QTabWidget()
        
        # Add tabs
        self.general_tab = GeneralTab()
        self.audio_tab = AudioTab()
        self.ai_tab = AITab()
        self.advanced_tab = AdvancedTab()
        self.eula_tab = EulaTab()
        
        # Connect EULA acceptance signal to handler
        self.eula_tab.eula_accepted.connect(self._on_eula_acceptance_changed)
        
        # Add tabs to tab widget
        self.tab_widget.addTab(self.eula_tab, "EULA")
        self.tab_widget.addTab(self.general_tab, "General")
        self.tab_widget.addTab(self.audio_tab, "Audio")
        self.tab_widget.addTab(self.ai_tab, "AI")
        self.tab_widget.addTab(self.advanced_tab, "Advanced")
        
        main_layout.addWidget(self.tab_widget)
        
        # Add buttons at the bottom (only visible when not on EULA tab)
        button_layout = QtWidgets.QHBoxLayout()
        
        self.save_button = QtWidgets.QPushButton("Save")
        self.save_button.clicked.connect(self.save_settings)
        self.save_button.setStyleSheet("""
            QPushButton {
                background-color: #28a745;
                color: white;
                border: none;
                padding: 8px 16px;
                border-radius: 4px;
                min-width: 100px;
            }
            QPushButton:hover {
                background-color: #218838;
            }
        """)
        
        self.cancel_button = QtWidgets.QPushButton("Cancel")
        self.cancel_button.clicked.connect(self.cancel_clicked)
        self.cancel_button.setStyleSheet("""
            QPushButton {
                background-color: #6c757d;
                color: white;
                border: none;
                padding: 8px 16px;
                border-radius: 4px;
                min-width: 100px;
            }
            QPushButton:hover {
                background-color: #5a6268;
            }
        """)
        
        # Center the buttons instead of right-aligning them
        button_layout.addStretch()
        button_layout.addWidget(self.save_button)
        button_layout.addSpacing(20)  # Add spacing between buttons
        button_layout.addWidget(self.cancel_button)
        button_layout.addStretch()
        
        main_layout.addLayout(button_layout)
        
        # Connect tab widget's currentChanged signal to update button visibility
        self.tab_widget.currentChanged.connect(self._on_tab_changed)
        
        # Initialize tab states based on EULA acceptance
        settings = load_settings_from_db()
        is_eula_accepted = settings.get('accepted_eula', False)
        self._update_tab_states(is_eula_accepted)
        
        # Force initial tab based on EULA acceptance
        if not is_eula_accepted:
            self.tab_widget.setCurrentIndex(0)
            # Make sure the buttons are visible
            self.save_button.setVisible(True)
            self.cancel_button.setVisible(True)
    
    def _on_eula_acceptance_changed(self, accepted):
        """Handle EULA acceptance state changes."""
        self._update_tab_states(accepted)
    
    def _update_tab_states(self, eula_accepted):
        """Update tab states based on EULA acceptance."""
        # Enable/disable tabs based on EULA acceptance
        for tab_index in range(1, self.tab_widget.count()):
            self.tab_widget.setTabEnabled(tab_index, eula_accepted)
        
        logging.debug(f"Tabs {'enabled' if eula_accepted else 'disabled'} based on EULA acceptance")
    
    def _on_tab_changed(self, index):
        """Handle tab changes and update button visibility."""
        # Always show the buttons (removed hiding logic)
        self.save_button.setVisible(True)
        self.cancel_button.setVisible(True)
        
    def save_settings(self):
        """Save all settings and close the window."""
        try:
            # Get settings from all tabs
            settings = load_settings_from_db()
            
            # EULA tab settings - get the checkbox status but don't save yet
            eula_accepted = self.eula_tab.accept_checkbox.isChecked()
            
            # Ensure the EULA acceptance status is in the settings we're going to save
            settings['accepted_eula'] = eula_accepted
            
            # Check if EULA is accepted
            if not eula_accepted:
                # Show warning dialog
                result = QtWidgets.QMessageBox.warning(
                    self,
                    "EULA Not Accepted",
                    "You have not accepted the End User License Agreement. "
                    "The application will exit if you continue without accepting the EULA.\n\n"
                    "Do you want to continue without accepting?",
                    QtWidgets.QMessageBox.StandardButton.Yes | QtWidgets.QMessageBox.StandardButton.No,
                    QtWidgets.QMessageBox.StandardButton.No
                )
                
                if result == QtWidgets.QMessageBox.StandardButton.Yes:
                    # Save settings with the unchecked EULA status
                    save_settings_to_db(settings)
                    
                    # Notify parent about acceptance change
                    self.eula_tab.eula_accepted.emit(eula_accepted)
                    
                    logging.info("Exiting application - EULA not accepted")
                    
                    # Use the proper exit method from the application
                    self.hide()
                    signal_manager.exit_app.emit()
                    QtWidgets.QApplication.quit()
                    return
                else:
                    # Return to settings without saving
                    return
            
            # If EULA is accepted, save all other settings
            # General tab settings
            settings['load_splash_sound'] = self.general_tab.load_splash_sound.isChecked()
            settings['show_about'] = self.general_tab.show_about.isChecked()
            settings['restore_position'] = self.general_tab.restore_position.isChecked()
            settings['sphere_size'] = self.general_tab.sphere_size_slider.value() * 20
            settings['selected_oracle'] = self.general_tab.switch_oracle.currentData()
            
            # Get listening state
            if self.general_tab.always_stop.isChecked():
                settings['startup_listening_state'] = 'stop'
            elif self.general_tab.always_start.isChecked():
                settings['startup_listening_state'] = 'start'
            else:
                settings['startup_listening_state'] = 'remember'
            
            # Audio tab settings
            settings['output_device'] = self.audio_tab.play_output_combo.currentText()
            settings['translation_device'] = self.audio_tab.play_translation_combo.currentText()
            settings['lock_sound'] = self.audio_tab.lock_sound_checkbox.isChecked()
            settings['speech_volume'] = self.audio_tab.speech_volume_slider.value()
            
            # AI tab settings
            # Save Ollama URL (no checkbox)
            settings['ollama_url'] = self.ai_tab.ollama_input.text()
            
            # Save API keys and enabled states
            for provider in ["assemblyai", "openai", "anthropic", "elevenlabs"]:
                checkbox = getattr(self.ai_tab, f"{provider}_checkbox")
                input_field = getattr(self.ai_tab, f"{provider}_input")
                settings[f"{provider}_enabled"] = checkbox.isChecked()
                settings[f"{provider}_key"] = input_field.text()
            
            # Save model selections
            settings['transcription_model'] = self.ai_tab.input_speech_combo.currentText()
            settings['agent_provider'] = self.ai_tab.agent_provider.currentText()
            settings['agent_model'] = self.ai_tab.agent_model.currentText()
            settings['code_provider'] = self.ai_tab.code_provider.currentText()
            settings['code_model'] = self.ai_tab.code_model.currentText()
            settings['tts_provider'] = self.ai_tab.tts_provider.currentText()
            settings['tts_voice'] = self.ai_tab.tts_voice.currentText()
            settings['playback_speed'] = float(self.ai_tab.speed_label.text().replace('x', ''))
            
            # Save settings
            save_settings_to_db(settings)
            logging.debug("Settings saved successfully")
            
            # Save first-time EULA acceptance specially (this will emit signals if needed)
            was_previously_accepted = settings.get('accepted_eula', False) != eula_accepted and eula_accepted
            if was_previously_accepted:
                self.eula_tab.save_acceptance_state()
            else:
                # Just emit the signal if it's not the first time
                self.eula_tab.eula_accepted.emit(eula_accepted)
            
            # Hide the window instead of closing it
            self.hide()
            
        except Exception as e:
            logging.error(f"Error saving settings: {str(e)}")
            QtWidgets.QMessageBox.critical(
                self,
                "Error",
                f"Failed to save settings: {str(e)}"
            ) 

    def cancel_clicked(self):
        """Custom handler for cancel button."""
        # Check if EULA is accepted
        settings = load_settings_from_db()
        if not settings.get('accepted_eula', False):
            # Force the user back to the EULA tab
            self.tab_widget.setCurrentIndex(0)
            
            if not self.showing_eula_message:
                self.showing_eula_message = True
                # Show a simple message about requiring EULA acceptance
                QtWidgets.QMessageBox.information(
                    self,
                    "EULA Required",
                    "You must accept the EULA to use this application. "
                    "Please review and accept the terms to continue.",
                    QtWidgets.QMessageBox.StandardButton.Ok
                )
                self.showing_eula_message = False
        else:
            # If EULA is accepted, just hide the window
            self.hide()
            logging.debug("Settings window hidden after cancel") 