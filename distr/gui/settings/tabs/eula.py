"""
EULA Tab Implementation

This module provides the EulaTab class which implements the EULA tab
of the settings window. It handles the display and acceptance of the
End User License Agreement.
"""

from PyQt6 import QtWidgets, QtCore
from PyQt6.QtCore import Qt
from distr.gui.settings.utils.settings import load_settings_from_db, save_settings_to_db
from distr.core.signals import signal_manager
import logging
import os

class EulaTab(QtWidgets.QWidget):
    """
    EULA tab implementation.
    
    This class provides the UI and functionality for the EULA tab,
    including displaying the EULA text and handling acceptance.
    """
    
    # Add a signal to emit when EULA acceptance changes
    eula_accepted = QtCore.pyqtSignal(bool)
    
    def __init__(self, parent=None):
        """
        Initialize the EULA tab.
        
        Args:
            parent (QWidget, optional): Parent widget
        """
        super().__init__(parent)
        self._setup_ui()
        logging.debug("EulaTab initialized")
        
        # Register this tab as needing to be notified when it becomes active
        if parent and hasattr(parent, 'currentChanged'):
            parent.currentChanged.connect(self._on_tab_selected)
        
    def _setup_ui(self):
        """Set up the UI components."""
        # Create main layout
        main_layout = QtWidgets.QVBoxLayout(self)
        main_layout.setContentsMargins(20, 20, 20, 20)
        main_layout.setSpacing(15)

        # Add tab header
        tab_header = QtWidgets.QLabel("End User License Agreement")
        tab_header.setAlignment(Qt.AlignmentFlag.AlignCenter)
        tab_header.setStyleSheet("font-size: 18pt; font-weight: bold; margin-bottom: 15px;")
        main_layout.addWidget(tab_header)

        # Add EULA content
        eula_content = QtWidgets.QTextEdit()
        eula_content.setReadOnly(True)
        eula_content.setMinimumHeight(450)  # Significantly increase the height of the EULA content
        eula_content.setStyleSheet("""
            QTextEdit {
                background-color: white;
                border: 1px solid #d0d0d0;
                border-radius: 5px;
                padding: 10px;
                font-size: 12pt;
            }
        """)
        
        # Load EULA content from file
        eula_path = os.path.join(os.path.dirname(__file__), "eula.txt")
        try:
            with open(eula_path, 'r') as f:
                eula_content.setText(f.read())
        except FileNotFoundError:
            logging.error(f"EULA file not found at {eula_path}")
            eula_content.setText("EULA content not available.")
        
        main_layout.addWidget(eula_content, 1)  # Add stretch factor to make the text box take more space

        # Add some spacing, but less than before
        main_layout.addSpacing(10)

        # Add accept checkbox with a more prominent style and centered
        checkbox_container = QtWidgets.QWidget()
        checkbox_layout = QtWidgets.QVBoxLayout(checkbox_container)
        checkbox_layout.setSpacing(10)  # Reduced spacing between elements
        checkbox_layout.setContentsMargins(0, 0, 0, 0)  # Remove margins
        
        # Add an informative message
        self.info_label = QtWidgets.QLabel("You must accept the EULA to use this application")
        self.info_label.setStyleSheet("""
            QLabel {
                color: #e74c3c;
                font-size: 12pt;
                font-style: italic;
                margin-bottom: 5px;
            }
        """)
        self.info_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        checkbox_layout.addWidget(self.info_label)
        
        self.accept_checkbox = QtWidgets.QCheckBox("I accept the terms and conditions")
        self.accept_checkbox.setStyleSheet("""
            QCheckBox::indicator {
                width: 28px;
                height: 28px;
                border: 2px solid #3498db;
                border-radius: 4px;
            }
            QCheckBox::indicator:checked {
                background-color: #000;
            }
        """)
        
        # Center the checkbox with more space
        checkbox_center = QtWidgets.QHBoxLayout()
        checkbox_center.addStretch()
        checkbox_center.addWidget(self.accept_checkbox)
        checkbox_center.addStretch()
        
        checkbox_layout.addLayout(checkbox_center)
        
        main_layout.addWidget(checkbox_container, 0)  # No stretch factor

        # Load saved acceptance state
        settings = load_settings_from_db()
        is_accepted = settings.get('accepted_eula', False)
        self.accept_checkbox.setChecked(is_accepted)
        
        # Update info label visibility based on acceptance state
        self.info_label.setVisible(not is_accepted)
        
        # Emit initial state
        self.eula_accepted.emit(is_accepted)

        # Connect checkbox to update UI only, not save settings
        self.accept_checkbox.stateChanged.connect(self._on_checkbox_changed)

    def _on_checkbox_changed(self, state):
        """Handle checkbox state change - only updates UI, doesn't save settings"""
        is_checked = bool(state)
        
        # Update info label visibility only
        self.info_label.setVisible(not is_checked)
        
    def save_acceptance_state(self):
        """Save the EULA acceptance state when the Save button is clicked"""
        is_accepted = self.accept_checkbox.isChecked()
        
        # Get previous acceptance state
        settings = load_settings_from_db()
        was_previously_accepted = settings.get('accepted_eula', False)
        
        # Save the new state
        settings['accepted_eula'] = is_accepted
        save_settings_to_db(settings)
        
        # Emit signal to notify parent about acceptance change
        self.eula_accepted.emit(is_accepted)
        
        # If this is the first time accepting the EULA, emit the global signal
        if is_accepted and not was_previously_accepted:
            logging.info("EULA accepted for the first time")
            signal_manager.eula_accepted.emit()
            
        return is_accepted 

    def _on_tab_selected(self, index):
        """Called when a tab is selected in the parent widget"""
        # Check if this tab is the one being selected
        if hasattr(self.parent(), 'widget') and self.parent().widget(index) == self:
            self._sync_checkbox_with_db()
    
    def _sync_checkbox_with_db(self):
        """Sync the checkbox state with the database value to ensure consistency"""
        # Reload settings to verify EULA status
        settings = load_settings_from_db()
        db_state = settings.get('accepted_eula', False)
        current_state = self.accept_checkbox.isChecked()
        
        # If they don't match, update the checkbox
        if current_state != db_state:
            self.accept_checkbox.setChecked(db_state)
            
            # Update UI based on new state
            self.info_label.setVisible(not db_state)
        
    def showEvent(self, event):
        """Called when the tab becomes visible"""
        super().showEvent(event)
        
        # Make sure checkbox is in sync with database
        self._sync_checkbox_with_db() 