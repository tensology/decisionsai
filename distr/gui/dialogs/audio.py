"""
audio_dialog.py - Audio Device Selection Dialog

This module provides the DeviceSelectionDialog class for selecting
audio input and output devices during initial application setup.
"""

import logging
from PyQt6.QtCore import Qt
from PyQt6 import QtWidgets
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, 
    QTabWidget, QTableWidget, QHeaderView, QAbstractItemView
)
import sounddevice as sd

from distr.core.utils import load_settings_from_db, save_settings_to_db

logger = logging.getLogger(__name__)


class DeviceSelectionDialog(QDialog):
    def __init__(self, oracle_window=None, parent=None):
        super().__init__(parent)
        self.oracle_window = oracle_window
        self.setWindowTitle("Audio Setup")
        self.setModal(True)
        self.setMinimumWidth(500)
        self.setMinimumHeight(400)
        
        # Prevent closing without selecting devices
        self.setWindowFlags(
            Qt.WindowType.Dialog |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.CustomizeWindowHint |
            Qt.WindowType.WindowTitleHint
        )
        
        # Apply dark theme
        self.setStyleSheet("""
            QDialog {
                background-color: #343541;
                color: #ececf1;
            }
            QLabel {
                color: #ececf1;
                font-size: 14px;
                margin-bottom: 5px;
            }
            QTabWidget::pane {
                border: 1px solid #565869;
                border-radius: 4px;
                background-color: #343541;
            }
            QTabBar::tab {
                background-color: #40414f;
                color: #ececf1;
                padding: 8px 20px;
                border-top-left-radius: 4px;
                border-top-right-radius: 4px;
                margin-right: 2px;
            }
            QTabBar::tab:selected {
                background-color: #343541;
                border: 1px solid #565869;
                border-bottom: none;
            }
            QTableWidget {
                background-color: #40414f;
                color: #ececf1;
                border: none;
                gridline-color: #565869;
                selection-background-color: #007bff;
            }
            QHeaderView::section {
                background-color: #343541;
                color: #ececf1;
                padding: 4px;
                border: none;
                border-bottom: 1px solid #565869;
                font-weight: bold;
            }
            QPushButton {
                background-color: #007bff;
                color: white;
                border: none;
                padding: 10px 20px;
                border-radius: 4px;
                font-weight: bold;
                font-size: 14px;
                min-width: 100px;
            }
            QPushButton:hover {
                background-color: #0069d9;
            }
        """)
        
        self.selected_input = None
        self.selected_output = None
        
        layout = QVBoxLayout()
        layout.setSpacing(20)
        layout.setContentsMargins(30, 30, 30, 30)
        
        # Title
        title = QLabel("Configure Audio Devices")
        title.setStyleSheet("font-size: 18px; font-weight: bold; margin-bottom: 10px;")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)
        
        # Tabs for Output and Input
        self.tabs = QTabWidget()
        self.output_tab = QtWidgets.QWidget()
        self.input_tab = QtWidgets.QWidget()
        self.tabs.addTab(self.output_tab, "Output")
        self.tabs.addTab(self.input_tab, "Input")
        
        # Output Tab Setup
        output_layout = QVBoxLayout(self.output_tab)
        output_layout.setContentsMargins(0, 10, 0, 0)
        self.output_device_list = QTableWidget()
        self.output_device_list.setColumnCount(2)
        self.output_device_list.setHorizontalHeaderLabels(["Name", "Type"])
        self.output_device_list.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.output_device_list.verticalHeader().setVisible(False)
        self.output_device_list.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.output_device_list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.output_device_list.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)  # Make cells non-editable
        self.output_device_list.itemSelectionChanged.connect(self._on_output_device_selected)
        self.populate_output_devices()
        output_layout.addWidget(self.output_device_list)
        
        # Input Tab Setup
        input_layout = QVBoxLayout(self.input_tab)
        input_layout.setContentsMargins(0, 10, 0, 0)
        self.input_device_list = QTableWidget()
        self.input_device_list.setColumnCount(2)
        self.input_device_list.setHorizontalHeaderLabels(["Name", "Type"])
        self.input_device_list.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.input_device_list.verticalHeader().setVisible(False)
        self.input_device_list.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.input_device_list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.input_device_list.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)  # Make cells non-editable
        self.input_device_list.itemSelectionChanged.connect(self._on_input_device_selected)
        self.populate_input_devices()
        input_layout.addWidget(self.input_device_list)
        
        layout.addWidget(self.tabs)
        
        # OK Button
        button_layout = QHBoxLayout()
        button_layout.addStretch()
        self.ok_button = QPushButton("Save & Continue")
        self.ok_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.ok_button.clicked.connect(self.accept_selection)
        button_layout.addWidget(self.ok_button)
        button_layout.addStretch()
        layout.addLayout(button_layout)
        
        self.setLayout(layout)
        
        # Load current settings if available
        self._load_current_selection()

    def populate_output_devices(self):
        """Populate the output devices list."""
        try:
            devices = sd.query_devices()
            output_devices = [("System Default", "System")]
            for device in devices:
                if device['max_output_channels'] > 0:
                    device_type = self._get_device_type(device['name'])
                    output_devices.append((device['name'], device_type))
            
            self.output_device_list.setRowCount(len(output_devices))
            for row, (name, device_type) in enumerate(output_devices):
                name_item = QtWidgets.QTableWidgetItem(name)
                name_item.setFlags(name_item.flags() & ~Qt.ItemFlag.ItemIsEditable)  # Make non-editable
                self.output_device_list.setItem(row, 0, name_item)
                type_item = QtWidgets.QTableWidgetItem(device_type)
                type_item.setFlags(type_item.flags() & ~Qt.ItemFlag.ItemIsEditable)  # Make non-editable
                self.output_device_list.setItem(row, 1, type_item)
        except Exception as e:
            logger.error(f"Error populating output devices: {e}")

    def populate_input_devices(self):
        """Populate the input devices list."""
        try:
            devices = sd.query_devices()
            input_devices = [("System Default", "System")]
            for device in devices:
                if device['max_input_channels'] > 0:
                    device_type = self._get_device_type(device['name'])
                    input_devices.append((device['name'], device_type))
            
            self.input_device_list.setRowCount(len(input_devices))
            for row, (name, device_type) in enumerate(input_devices):
                name_item = QtWidgets.QTableWidgetItem(name)
                name_item.setFlags(name_item.flags() & ~Qt.ItemFlag.ItemIsEditable)  # Make non-editable
                self.input_device_list.setItem(row, 0, name_item)
                type_item = QtWidgets.QTableWidgetItem(device_type)
                type_item.setFlags(type_item.flags() & ~Qt.ItemFlag.ItemIsEditable)  # Make non-editable
                self.input_device_list.setItem(row, 1, type_item)
        except Exception as e:
            logger.error(f"Error populating input devices: {e}")

    def _get_device_type(self, device_name: str) -> str:
        """Determine device type from device name."""
        name_lower = device_name.lower()
        if 'bluetooth' in name_lower or 'bt' in name_lower:
            return "Bluetooth"
        elif 'built-in' in name_lower or 'internal' in name_lower or 'macbook' in name_lower:
            return "Built-in"
        elif 'usb' in name_lower:
            return "USB"
        elif 'airpods' in name_lower:
            return "Bluetooth"
        elif 'headphone' in name_lower or 'headset' in name_lower:
            return "Headphones"
        else:
            return "Other"

    def _on_output_device_selected(self):
        """Handle output device selection."""
        selected_items = self.output_device_list.selectedItems()
        selected_rows = self.output_device_list.selectionModel().selectedRows()
        logger.info(f"_on_output_device_selected: selectedItems count={len(selected_items)}, selectedRows count={len(selected_rows)}")
        
        if selected_items:
            self.selected_output = selected_items[0].text()
            logger.info(f"_on_output_device_selected: Output device clicked/selected: '{self.selected_output}'")
        elif selected_rows:
            # Try getting from selected row if items aren't available
            row = selected_rows[0].row()
            item = self.output_device_list.item(row, 0)
            if item:
                self.selected_output = item.text()
                logger.info(f"_on_output_device_selected: Output device selected from row {row}: '{self.selected_output}'")
            else:
                logger.warning(f"_on_output_device_selected: No item found at selected row {row}")
        else:
            logger.warning("_on_output_device_selected: No selection detected (selectedItems and selectedRows both empty)")

    def _on_input_device_selected(self):
        """Handle input device selection."""
        selected_items = self.input_device_list.selectedItems()
        selected_rows = self.input_device_list.selectionModel().selectedRows()
        logger.info(f"_on_input_device_selected: selectedItems count={len(selected_items)}, selectedRows count={len(selected_rows)}")
        
        if selected_items:
            self.selected_input = selected_items[0].text()
            logger.info(f"_on_input_device_selected: Input device clicked/selected: '{self.selected_input}'")
        elif selected_rows:
            # Try getting from selected row if items aren't available
            row = selected_rows[0].row()
            item = self.input_device_list.item(row, 0)
            if item:
                self.selected_input = item.text()
                logger.info(f"_on_input_device_selected: Input device selected from row {row}: '{self.selected_input}'")
            else:
                logger.warning(f"_on_input_device_selected: No item found at selected row {row}")
        else:
            logger.warning("_on_input_device_selected: No selection detected (selectedItems and selectedRows both empty)")

    def _load_current_selection(self):
        """Load currently saved devices and select them in the list."""
        settings = load_settings_from_db()
        
        # Temporarily disconnect handlers to avoid triggering saves during programmatic selection
        try:
            self.output_device_list.itemSelectionChanged.disconnect()
        except Exception:
            pass
        try:
            self.input_device_list.itemSelectionChanged.disconnect()
        except Exception:
            pass
        
        # Select output - use case-insensitive matching
        saved_output = settings.get('output_device')
        found_output = False
        
        # If None or empty, default to first item (System Default)
        if not saved_output or (isinstance(saved_output, str) and not saved_output.strip()):
            if self.output_device_list.rowCount() > 0:
                self.output_device_list.selectRow(0)
                self.selected_output = self.output_device_list.item(0, 0).text()
                found_output = True
                logger.debug(f"No saved output device, defaulting to: {self.selected_output}")
        else:
            saved_output_lower = saved_output.lower().strip()
            
            for row in range(self.output_device_list.rowCount()):
                item = self.output_device_list.item(row, 0)
                if item:
                    device_name = item.text()
                    # Try exact match first
                    if device_name == saved_output:
                        self.output_device_list.selectRow(row)
                        self.selected_output = device_name
                        found_output = True
                        logger.debug(f"Found exact match for output device: {device_name}")
                        break
                    # Try case-insensitive match
                    elif device_name.lower().strip() == saved_output_lower:
                        self.output_device_list.selectRow(row)
                        self.selected_output = device_name  # Use the actual device name from list
                        found_output = True
                        logger.debug(f"Found case-insensitive match for output device: {saved_output} -> {device_name}")
                        break
        
        if not found_output and self.output_device_list.rowCount() > 0:
            # Default to first item (System Default) if not already set
            if not self.selected_output:
                self.output_device_list.selectRow(0)
                self.selected_output = self.output_device_list.item(0, 0).text()
                logger.debug(f"No saved output device found, defaulting to: {self.selected_output}")
        
        # Select input - use case-insensitive matching
        saved_input = settings.get('input_device')
        found_input = False
        
        # If None or empty, default to first item (System Default)
        if not saved_input or (isinstance(saved_input, str) and not saved_input.strip()):
            if self.input_device_list.rowCount() > 0:
                self.input_device_list.selectRow(0)
                self.selected_input = self.input_device_list.item(0, 0).text()
                found_input = True
                logger.debug(f"No saved input device, defaulting to: {self.selected_input}")
        else:
            saved_input_lower = saved_input.lower().strip()
            
            for row in range(self.input_device_list.rowCount()):
                item = self.input_device_list.item(row, 0)
                if item:
                    device_name = item.text()
                    # Try exact match first
                    if device_name == saved_input:
                        self.input_device_list.selectRow(row)
                        self.selected_input = device_name
                        found_input = True
                        logger.debug(f"Found exact match for input device: {device_name}")
                        break
                    # Try case-insensitive match
                    elif device_name.lower().strip() == saved_input_lower:
                        self.input_device_list.selectRow(row)
                        self.selected_input = device_name  # Use the actual device name from list
                        found_input = True
                        logger.debug(f"Found case-insensitive match for input device: {saved_input} -> {device_name}")
                        break
        
        if not found_input and self.input_device_list.rowCount() > 0:
            # Default to first item (System Default) if not already set
            if not self.selected_input:
                self.input_device_list.selectRow(0)
                self.selected_input = self.input_device_list.item(0, 0).text()
                logger.debug(f"No saved input device found, defaulting to: {self.selected_input}")
        
        # Reconnect handlers after programmatic selection
        self.output_device_list.itemSelectionChanged.connect(self._on_output_device_selected)
        self.input_device_list.itemSelectionChanged.connect(self._on_input_device_selected)
        
        # Update settings if we found case-insensitive matches (to normalize case)
        settings_updated = False
        if found_output and self.selected_output != saved_output:
            settings['output_device'] = self.selected_output
            settings_updated = True
        if found_input and self.selected_input != saved_input:
            settings['input_device'] = self.selected_input
            settings_updated = True
        
        if settings_updated:
            save_settings_to_db(settings)
            logger.debug("Updated device names in settings to match actual device names (case normalization)")
        
        # Scroll to selected devices to make them visible
        if found_output:
            selected_rows = self.output_device_list.selectionModel().selectedRows()
            if selected_rows:
                self.output_device_list.scrollTo(selected_rows[0], QAbstractItemView.ScrollHint.EnsureVisible)
        
        if found_input:
            selected_rows = self.input_device_list.selectionModel().selectedRows()
            if selected_rows:
                self.input_device_list.scrollTo(selected_rows[0], QAbstractItemView.ScrollHint.EnsureVisible)
        
        logger.info(f"Loaded device selection - Input: {self.selected_input}, Output: {self.selected_output}")

    def accept_selection(self):
        """Validate selection and close."""
        # Always get the current selection from the tables to ensure we have the latest values
        # This is more reliable than relying on self.selected_input/selected_output which might be stale
        
        # Check input selection
        selected_input_items = self.input_device_list.selectedItems()
        selected_input_rows = self.input_device_list.selectionModel().selectedRows()
        logger.info(f"accept_selection: Input - selectedItems count: {len(selected_input_items)}, selectedRows count: {len(selected_input_rows)}")
        
        if selected_input_items:
            self.selected_input = selected_input_items[0].text()
            logger.info(f"accept_selection: Got input device from table selection: '{self.selected_input}'")
        elif selected_input_rows:
            # Try getting from selected row if items aren't available
            row = selected_input_rows[0].row()
            item = self.input_device_list.item(row, 0)
            if item:
                self.selected_input = item.text()
                logger.info(f"accept_selection: Got input device from selected row {row}: '{self.selected_input}'")
            else:
                # Fallback to first item
                first_item = self.input_device_list.item(0, 0)
                if first_item:
                    self.selected_input = first_item.text()
                    logger.warning(f"accept_selection: No input item at selected row, using first item: '{self.selected_input}'")
                else:
                    self.selected_input = 'System Default'
                    logger.warning("accept_selection: No input devices found, defaulting to System Default")
        else:
            # If nothing selected, use the first row (should be System Default)
            first_item = self.input_device_list.item(0, 0)
            if first_item:
                self.selected_input = first_item.text()
                logger.warning(f"accept_selection: No input selection detected, using first item: '{self.selected_input}'")
            else:
                self.selected_input = 'System Default'
                logger.warning("accept_selection: No input devices found, defaulting to System Default")
        
        # Check output selection
        selected_output_items = self.output_device_list.selectedItems()
        selected_output_rows = self.output_device_list.selectionModel().selectedRows()
        logger.info(f"accept_selection: Output - selectedItems count: {len(selected_output_items)}, selectedRows count: {len(selected_output_rows)}")
        
        if selected_output_items:
            self.selected_output = selected_output_items[0].text()
            logger.info(f"accept_selection: Got output device from table selection: '{self.selected_output}'")
        elif selected_output_rows:
            # Try getting from selected row if items aren't available
            row = selected_output_rows[0].row()
            item = self.output_device_list.item(row, 0)
            if item:
                self.selected_output = item.text()
                logger.info(f"accept_selection: Got output device from selected row {row}: '{self.selected_output}'")
            else:
                # Fallback to first item
                first_item = self.output_device_list.item(0, 0)
                if first_item:
                    self.selected_output = first_item.text()
                    logger.warning(f"accept_selection: No output item at selected row, using first item: '{self.selected_output}'")
                else:
                    self.selected_output = 'System Default'
                    logger.warning("accept_selection: No output devices found, defaulting to System Default")
        else:
            # If nothing selected, use the first row (should be System Default)
            first_item = self.output_device_list.item(0, 0)
            if first_item:
                self.selected_output = first_item.text()
                logger.warning(f"accept_selection: No output selection detected, using first item: '{self.selected_output}'")
            else:
                self.selected_output = 'System Default'
                logger.warning("accept_selection: No output devices found, defaulting to System Default")
            
        # Save to settings
        settings = load_settings_from_db()
        settings['input_device'] = self.selected_input
        settings['output_device'] = self.selected_output
        save_settings_to_db(settings)
        logger.info(f"accept_selection: Device selection saved to database - Input: '{self.selected_input}', Output: '{self.selected_output}'")
        
        # Verify the save worked
        verify_settings = load_settings_from_db()
        logger.info(f"accept_selection: Verification - Database now has input: '{verify_settings.get('input_device')}', output: '{verify_settings.get('output_device')}'")
        
        self.accept()
    
    def closeEvent(self, event):
        """Override close event to prevent closing without selecting devices."""
        # Prevent closing - user must click OK to select devices
        event.ignore()
        QtWidgets.QMessageBox.warning(
            self,
            "Device Selection Required",
            "You must select audio devices to continue.\n\n"
            "Please select your input and output devices and click Save & Continue.",
            QtWidgets.QMessageBox.StandardButton.Ok
        )
    
    def showEvent(self, event):
        """Called when dialog is shown."""
        super().showEvent(event)
        # Positioning is done before show() is called to prevent flicker
    
    def position_at_oracle(self):
        """Position the dialog centered on the screen where the oracle window is located."""
        # First try to use the oracle window if it's visible
        target_window = self.oracle_window
        if target_window and target_window.isVisible():
            # Get the screen that contains the oracle window
            oracle_screen = QtWidgets.QApplication.screenAt(target_window.pos())
            if oracle_screen:
                screen_geometry = oracle_screen.geometry()
                self.adjustSize()
                x = screen_geometry.x() + (screen_geometry.width() // 2) - (self.width() // 2)
                y = screen_geometry.y() + (screen_geometry.height() // 2) - (self.height() // 2)
                self.move(x, y)
                logger.debug(f"Device selection dialog centered on oracle's screen (from visible window): ({x}, {y})")
                return
        
        # If oracle window not visible, try to get saved position from database
        try:
            from distr.core.db import get_session, ScreenPosition
            from distr.core.utils import get_screens_hash
            
            screens_id = get_screens_hash()
            with get_session() as session:
                position = session.query(ScreenPosition).filter_by(screens_id=screens_id).first()
                if position and position.screen_name:
                    # Find the screen by name
                    screens = QtWidgets.QApplication.screens()
                    target_screen = None
                    for screen in screens:
                        if screen.name() == position.screen_name:
                            target_screen = screen
                            break
                    
                    if target_screen:
                        screen_geometry = target_screen.geometry()
                        self.adjustSize()
                        x = screen_geometry.x() + (screen_geometry.width() // 2) - (self.width() // 2)
                        y = screen_geometry.y() + (screen_geometry.height() // 2) - (self.height() // 2)
                        self.move(x, y)
                        logger.debug(f"Device selection dialog centered on oracle's screen (from saved position): ({x}, {y}) on {position.screen_name}")
                        return
        except Exception as e:
            logger.debug(f"Could not get saved oracle position: {e}")
        
        # Fallback to screen center if can't determine screen
        self._center_on_screen()
        logger.debug("Device selection dialog centered on primary screen (fallback)")
    
    def _center_on_screen(self):
        """Center the window on the primary screen."""
        # Ensure window size is calculated first
        self.adjustSize()
        
        screen = QtWidgets.QApplication.primaryScreen()
        screen_geometry = screen.geometry()
        
        # Calculate center position
        x = screen_geometry.x() + (screen_geometry.width() // 2) - (self.width() // 2)
        y = screen_geometry.y() + (screen_geometry.height() // 2) - (self.height() // 2)
        
        # Move window
        self.move(x, y)
    
    def get_selection(self):
        """Return the selected input and output device names."""
        # Return the stored selections from table widget selections
        return self.selected_input or 'System Default', self.selected_output or 'System Default'

