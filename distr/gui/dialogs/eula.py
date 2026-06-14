
"""
EULA Window Implementation

This module provides a standalone EULA window that forces the user to accept
the End User License Agreement before using the application.
"""

from PyQt6 import QtWidgets, QtCore
from PyQt6.QtCore import Qt
from distr.core.settings import load_settings_from_db, save_settings_to_db
from distr.core.signals import signal_manager
import logging
import os
import sys
import subprocess

class EulaWindow(QtWidgets.QDialog):
    """
    Standalone EULA window implementation.
    
    This class provides a modal dialog that displays the EULA and requires
    the user to accept it before continuing. The window cannot be closed
    without accepting the EULA.
    """
    
    # Signal emitted when EULA is accepted
    eula_accepted = QtCore.pyqtSignal(bool)
    
    def __init__(self, oracle_window=None, parent=None):
        """
        Initialize the EULA window.
        
        Args:
            oracle_window (OracleWindow, optional): Oracle window to center on
            parent (QWidget, optional): Parent widget
        """
        super().__init__(parent)
        self.oracle_window = oracle_window
        self.setWindowTitle("End User License Agreement")
        self.setMinimumSize(800, 700)
        self.setModal(True)  # Make it modal - blocks interaction with other windows
        
        # Set window flags to make it stay on top and prevent closing
        self.setWindowFlags(
            Qt.WindowType.Dialog |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.CustomizeWindowHint |
            Qt.WindowType.WindowTitleHint
        )
        
        self._setup_ui()
        logging.debug("EulaWindow initialized")
    
    def position_at_oracle(self, oracle_window=None):
        """Position the EULA window centered on the screen where the oracle window is located."""
        target_window = oracle_window or self.oracle_window
        if target_window and target_window.isVisible():
            # Get the screen that contains the oracle window
            oracle_screen = QtWidgets.QApplication.screenAt(target_window.pos())
            if oracle_screen:
                # Get the screen geometry
                screen_geometry = oracle_screen.geometry()
                
                # Ensure window size is calculated
                self.adjustSize()
                
                # Calculate position: center the EULA window on the screen
                x = screen_geometry.x() + (screen_geometry.width() // 2) - (self.width() // 2)
                y = screen_geometry.y() + (screen_geometry.height() // 2) - (self.height() // 2)
                
                # Move window
                self.move(x, y)
                logging.debug(f"EULA window centered on oracle's screen: ({x}, {y})")
            else:
                # Fallback to screen center if can't determine screen
                self._center_on_screen()
                logging.debug("EULA window centered on primary screen (could not determine oracle screen)")
        else:
            # Fallback to screen center if no oracle window or not visible
            self._center_on_screen()
            logging.debug("EULA window centered on screen (oracle not available)")
    
    def _center_on_screen(self):
        """Center the window on the primary screen."""
        screen = QtWidgets.QApplication.primaryScreen()
        screen_geometry = screen.geometry()
        window_geometry = self.frameGeometry()
        center_point = screen_geometry.center()
        window_geometry.moveCenter(center_point)
        self.move(window_geometry.topLeft())
    
    def _setup_ui(self):
        """Set up the UI components."""
        # Apply Dark Theme to the Dialog
        self.setStyleSheet("""
            QDialog {
                background-color: #343541;
                color: #ececf1;
            }
            QScrollBar:vertical {
                width: 10px;
                background: #202123;
                border-radius: 5px;
            }
            QScrollBar::handle:vertical {
                background: #565869;
                border-radius: 5px;
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
                height: 0px;
            }
        """)

        # Create main layout
        main_layout = QtWidgets.QVBoxLayout(self)
        main_layout.setContentsMargins(30, 30, 30, 30)
        main_layout.setSpacing(20)

        # Remove redundant header as per request ("There is redundant give more space to the actual agreement")
        # Instead, we'll have a small informative text or just the license content directly.
        
        # Add EULA content area
        # Using QTextBrowser for rich text and link support
        self.eula_content = QtWidgets.QTextBrowser()
        self.eula_content.setOpenExternalLinks(False) # Handle links manually to open file
        self.eula_content.anchorClicked.connect(self._on_link_clicked)
        self.eula_content.setReadOnly(True)
        self.eula_content.setStyleSheet("""
            QTextBrowser {
                background-color: #202123;
                color: #ececf1;
                border: 1px solid #565869;
                border-radius: 8px;
                padding: 20px;
                font-size: 13px;
                line-height: 1.6;
                font-family: Arial, sans-serif;
            }
        """)
        
        # Load EULA content
        self._load_eula_content()
        
        main_layout.addWidget(self.eula_content, 1)

        # Call out block for "You must accept..."
        # User requested: "The red must be in a call out block"
        callout_frame = QtWidgets.QFrame()
        callout_frame.setStyleSheet("""
            QFrame {
                background-color: #4a1b1b; /* Dark Red background for callout */
                border: 1px solid #e74c3c;
                border-radius: 6px;
                padding: 10px;
            }
            QLabel {
                color: #ff9999;
                font-weight: bold;
                border: none;
                background: transparent;
            }
        """)
        callout_layout = QtWidgets.QVBoxLayout(callout_frame)
        callout_layout.setContentsMargins(15, 10, 15, 10)
        
        self.info_label = QtWidgets.QLabel("You must accept the End User License Agreement to use this application.")
        self.info_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        callout_layout.addWidget(self.info_label)
        
        # Initially visible if not accepted
        settings = load_settings_from_db()
        is_accepted = settings.get('accepted_eula', False)
        callout_frame.setVisible(not is_accepted)
        self.callout_frame = callout_frame
        
        main_layout.addWidget(callout_frame)

        # Checkbox Container
        checkbox_container = QtWidgets.QWidget()
        checkbox_layout = QtWidgets.QHBoxLayout(checkbox_container)
        checkbox_layout.setContentsMargins(0, 10, 0, 10)
        
        # Checkbox
        self.accept_checkbox = QtWidgets.QCheckBox("I accept the terms and conditions")
        self.accept_checkbox.setCursor(Qt.CursorShape.PointingHandCursor)
        # User request: "if I click on the except check box it needs to be green as green as the accept button becomes"
        self.accept_checkbox.setStyleSheet("""
            QCheckBox {
                font-size: 14px;
                color: #ececf1;
                spacing: 10px;
            }
            QCheckBox::indicator {
                width: 22px;
                height: 22px;
                border: 2px solid #565869;
                border-radius: 4px;
                background-color: #343541;
            }
            QCheckBox::indicator:checked {
                background-color: #007bff; /* Blue background when checked */
                border-color: #007bff;
            }
            QCheckBox::indicator:hover {
                border-color: #ececf1;
            }
        """)
        
        checkbox_layout.addStretch()
        checkbox_layout.addWidget(self.accept_checkbox)
        checkbox_layout.addStretch()
        
        main_layout.addWidget(checkbox_container)

        # Load saved state
        self.accept_checkbox.setChecked(is_accepted)
        
        # Connect checkbox
        self.accept_checkbox.stateChanged.connect(self._on_checkbox_changed)

        # Button Layout
        button_layout = QtWidgets.QHBoxLayout()
        button_layout.addStretch()
        
        # Accept Button
        # User request: "accept button needs to be grayed out in a different color grey and then... needs to be green"
        self.accept_button = QtWidgets.QPushButton("ACCEPT LICENSE")
        self.accept_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.accept_button.setEnabled(is_accepted)
        self.accept_button.clicked.connect(self._on_accept_clicked)
        
        # Define button styles for enabled/disabled states
        self.btn_style_enabled = """
            QPushButton {
                background-color: #007bff; /* Blue */
                color: white;
                border: none;
                padding: 12px 40px;
                border-radius: 6px;
                font-size: 14px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #0069d9;
            }
        """
        self.btn_style_disabled = """
            QPushButton {
                background-color: #454655; /* Different Grey */
                color: #8e8ea0;
                border: 1px solid #565869;
                padding: 12px 40px;
                border-radius: 6px;
                font-size: 14px;
                font-weight: bold;
            }
        """
        
        self.accept_button.setStyleSheet(self.btn_style_enabled if is_accepted else self.btn_style_disabled)
        
        button_layout.addWidget(self.accept_button)
        button_layout.addStretch()
        
        main_layout.addLayout(button_layout)
    
    def _load_eula_content(self):
        """Load EULA content from LICENSE.md or fallback."""
        # File is at distr/gui/dialogs/eula.py — need 4 levels up to reach project root
        project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
        license_path = os.path.join(project_root, "LICENSE.md")
        
        eula_text = ""
        
        if os.path.exists(license_path):
            try:
                with open(license_path, 'r', encoding='utf-8') as f:
                    eula_text = f.read()
            except Exception as e:
                logging.error(f"Failed to read LICENSE.md: {e}")
        
        if not eula_text:
            # Fallback to local file if LICENSE.md fails
            local_path = os.path.join(os.path.dirname(__file__), "settings", "tabs", "eula.txt")
            if os.path.exists(local_path):
                try:
                    with open(local_path, 'r') as f:
                        eula_text = f.read()
                except (OSError, IOError):
                    pass
        
        if not eula_text:
            eula_text = "# End User License Agreement\n\nLicense information not available."

        # Convert Markdown to HTML (simple conversion for display)
        # Or just wrap in <pre> if we want raw text, but basic HTML is better.
        # Since we don't have a markdown lib guarantee, we'll do basic formatting.
        
        html_content = self._simple_markdown_to_html(eula_text)
        
        self.eula_content.setHtml(html_content)

    def _simple_markdown_to_html(self, text):
        """Convert basic markdown to HTML for display."""
        html = ""
        lines = text.split('\n')
        in_list = False
        prev_empty = False
        
        for line in lines:
            line = line.strip()
            if not line:
                if in_list:
                    html += "</ul>"
                    in_list = False
                prev_empty = True
                continue
                
            if line.startswith('# '):
                html += f"<h1 style='color: #ffffff; font-size: 16px; margin: 12px 0 8px; line-height: 1.3;'>{line[2:]}</h1>"
            elif line.startswith('## '):
                html += f"<h2 style='color: #ececf1; font-size: 14px; margin: 10px 0 6px; line-height: 1.3;'>{line[3:]}</h2>"
            elif line.startswith('### '):
                html += f"<h3 style='color: #ececf1; font-size: 13px; margin: 8px 0 4px; line-height: 1.3;'>{line[4:]}</h3>"
            elif line.startswith('- ') or line.startswith('* '):
                if not in_list:
                    html += "<ul style='margin: 4px 0;'>"
                    in_list = True
                html += f"<li style='margin-bottom: 3px; line-height: 1.5;'>{line[2:]}</li>"
            else:
                if in_list:
                    html += "</ul>"
                    in_list = False
                spacing = "margin: 6px 0;" if prev_empty else "margin: 2px 0;"
                html += f"<p style='{spacing} line-height: 1.5;'>{line}</p>"
            prev_empty = False
                
        if in_list:
            html += "</ul>"
            
        return html

    def _on_link_clicked(self, url):
        """Handle link clicks in the text browser."""
        path = url.toLocalFile() if url.isLocalFile() else url.toString()
        if path.startswith("file://"):
            path = path[7:]
            
        if os.path.exists(path):
            # Open file with default application
            if sys.platform == 'win32':
                os.startfile(path)
            elif sys.platform == 'darwin':
                subprocess.call(('open', path))
            else:
                subprocess.call(('xdg-open', path))
        else:
            logging.warning(f"Could not open link: {path}")

    def _on_checkbox_changed(self, state):
        """Handle checkbox state change - update button state."""
        is_checked = bool(state)
        self.accept_button.setEnabled(is_checked)
        self.accept_button.setStyleSheet(self.btn_style_enabled if is_checked else self.btn_style_disabled)
        
        # Hide/Show callout based on check state? User didn't explicitly say to hide it,
        # but usually warnings disappear when action is taken.
        # Keeping it simple: if checked, valid.
        self.callout_frame.setVisible(not is_checked)
    
    def _on_accept_clicked(self):
        """Handle accept button click - save acceptance and close window."""
        if not self.accept_checkbox.isChecked():
            return
        
        # Save the acceptance state
        settings = load_settings_from_db()
        was_previously_accepted = settings.get('accepted_eula', False)
        settings['accepted_eula'] = True
        save_settings_to_db(settings)
        
        # Force a small delay to ensure database commit is fully flushed
        import time
        time.sleep(0.05)
        
        # Verify the save worked by reloading with a fresh session
        verify_settings = load_settings_from_db()
        verify_accepted = verify_settings.get('accepted_eula', False)
        if not verify_accepted:
            logging.error("EULA acceptance save failed - value not persisted!")
            QtWidgets.QMessageBox.critical(
                self,
                "Error",
                "Failed to save EULA acceptance. Please try again."
            )
            return
        
        logging.info(f"EULA acceptance saved successfully. Verified: {verify_accepted}")
        
        # Emit signal to notify other components
        self.eula_accepted.emit(True)
        
        # If this is the first time accepting, emit the global signal
        if not was_previously_accepted:
            logging.info("EULA accepted for the first time")
            signal_manager.eula_accepted.emit()
        
        # Force Qt to process events to ensure signals are delivered
        QtWidgets.QApplication.processEvents()
        
        # Close the window
        self.accept()
        logging.debug("EULA window closed after acceptance")
    
    def closeEvent(self, event):
        """Override close event to prevent closing without accepting."""
        # Check if EULA is accepted
        settings = load_settings_from_db()
        if not settings.get('accepted_eula', False):
            # Prevent closing
            event.ignore()
            # Flash the callout or something?
            self.callout_frame.setVisible(True)
            QtWidgets.QMessageBox.warning(
                self,
                "EULA Required",
                "You must accept the End User License Agreement to use this application.\n\n"
                "Please read the terms and check the box to accept.",
                QtWidgets.QMessageBox.StandardButton.Ok
            )
        else:
            # Allow closing if already accepted
            event.accept()
    
    def showEvent(self, event):
        """Called when window is shown - ensure checkbox state is correct."""
        super().showEvent(event)
        # Reload settings to ensure we have the latest state
        settings = load_settings_from_db()
        is_accepted = settings.get('accepted_eula', False)
        self.accept_checkbox.setChecked(is_accepted)
        self.accept_button.setEnabled(is_accepted)
        self.accept_button.setStyleSheet(self.btn_style_enabled if is_accepted else self.btn_style_disabled)
        self.callout_frame.setVisible(not is_accepted)
