"""
General Settings Tab Implementation

This module provides the GeneralTab class which implements the general settings tab
of the settings window. It handles startup options, oracle settings, and language preferences.

Key Features:
- Startup options configuration
- Oracle settings management
- Language preferences
"""

from PyQt6 import QtWidgets, QtGui, QtCore
from PyQt6.QtCore import Qt
from distr.gui.settings.utils.settings import load_settings_from_db, save_settings_to_db
from distr.core.constants import ORACLE_DIR
from distr.core.signals import signal_manager
import logging
import os

class GeneralTab(QtWidgets.QWidget):
    """
    General settings tab implementation.
    
    This class provides the UI and functionality for the general settings tab,
    including startup options, oracle settings, and language preferences.
    """
    
    def __init__(self, parent=None):
        """
        Initialize the general settings tab.
        
        Args:
            parent (QWidget, optional): Parent widget
        """
        super().__init__(parent)
        self._setup_ui()
        self._load_settings()
        logging.debug("GeneralTab initialized")

    def _setup_ui(self):
        """Set up the UI components."""
        # Create main layout
        main_layout = QtWidgets.QVBoxLayout(self)
        main_layout.setSpacing(20)

        # Create a font for all group box titles
        title_font = QtGui.QFont()
        title_font.setPointSize(12)

        # Startup Options
        startup_group = QtWidgets.QGroupBox("Startup Options")
        startup_group.setFont(title_font)
        startup_layout = QtWidgets.QVBoxLayout()
        startup_layout.setSpacing(15)
        
        # Regular checkboxes
        checkbox_container = QtWidgets.QWidget()
        checkbox_layout = QtWidgets.QHBoxLayout(checkbox_container)
        checkbox_layout.setContentsMargins(20, 10, 20, 10)
        checkbox_layout.setSpacing(50)
        
        # Create startup option checkboxes
        self.load_splash_sound = QtWidgets.QCheckBox()
        self.show_about = QtWidgets.QCheckBox()
        
        startup_options = [
            (self.load_splash_sound, "Load Splash Sound on Startup"),
            (self.show_about, "Show About on Startup"),
        ]
        
        for checkbox, label_text in startup_options:
            container = QtWidgets.QWidget()
            container_layout = QtWidgets.QHBoxLayout(container)
            container_layout.setContentsMargins(0, 0, 0, 0)
            container_layout.setSpacing(15)
            
            checkbox.setText("")
            checkbox.setStyleSheet("QCheckBox { margin-right: 10px; }")
            
            label = QtWidgets.QLabel(label_text)
            label.setBuddy(checkbox)
            label.mousePressEvent = lambda _, cb=checkbox: cb.setChecked(not cb.isChecked())
            label.setCursor(QtGui.QCursor(Qt.CursorShape.PointingHandCursor))
            label.setStyleSheet("QLabel { padding: 5px; }")
            
            container_layout.addWidget(checkbox)
            container_layout.addWidget(label)
            container_layout.addStretch()
            
            checkbox_layout.addWidget(container)
        
        checkbox_layout.addStretch()
        startup_layout.addWidget(checkbox_container)
        
        # Radio buttons for listening state
        listening_group = QtWidgets.QGroupBox("Listening State on Startup")
        listening_group.setFont(title_font)
        listening_layout = QtWidgets.QVBoxLayout()

        self.listening_state_group = QtWidgets.QButtonGroup(self)

        radio_style = """
            QRadioButton {
                min-height: 30px; 
                font-size: 14px;
            }
            QRadioButton::indicator {
                width: 16px;
                height: 16px;
                margin-right: 15px;
            }
        """

        self.remember_listening = QtWidgets.QRadioButton("Remember Last Listening State")
        self.always_stop = QtWidgets.QRadioButton("Always Stop Listening")
        self.always_start = QtWidgets.QRadioButton("Always Start Listening")

        for radio in [self.remember_listening, self.always_stop, self.always_start]:
            radio.setStyleSheet(radio_style)
            self.listening_state_group.addButton(radio)
            listening_layout.addWidget(radio)

        listening_group.setLayout(listening_layout)
        startup_layout.addWidget(listening_group)

        startup_group.setLayout(startup_layout)
        main_layout.addWidget(startup_group)

        # My Oracle options
        oracle_frame = QtWidgets.QGroupBox("My Oracle")
        oracle_frame.setFont(title_font)
        oracle_layout = QtWidgets.QVBoxLayout()
        
        # Create horizontal layout for position and oracle selection
        controls_layout = QtWidgets.QHBoxLayout()
        
        # Oracle selection controls
        oracle_layout_h = QtWidgets.QHBoxLayout()
        oracle_layout_h.addWidget(QtWidgets.QLabel("Switch Oracle:"))
        self.switch_oracle = QtWidgets.QComboBox()
        self.setup_oracle_options()
        oracle_layout_h.addWidget(self.switch_oracle)
        controls_layout.addLayout(oracle_layout_h)
        
        # Position controls with Custom as default
        position_layout = QtWidgets.QHBoxLayout()
        position_layout.addWidget(QtWidgets.QLabel("Position:"))
        self.position_combo = QtWidgets.QComboBox()
        self.position_combo.addItems([
            "Custom",
            "Top Left", "Top Right",
            "Middle Left", "Middle Right",
            "Bottom Left", "Bottom Right"
        ])
        self.position_combo.currentTextChanged.connect(self.on_position_changed)
        position_layout.addWidget(self.position_combo)
        controls_layout.addLayout(position_layout)
        
        oracle_layout.addLayout(controls_layout)
        
        # Add restore position checkbox
        self.restore_position = QtWidgets.QCheckBox("Restore Position")
        self.restore_position.setChecked(True)
        oracle_layout.addWidget(self.restore_position)
        
        # Add size slider with updated style
        size_layout = QtWidgets.QHBoxLayout()
        size_layout.addWidget(QtWidgets.QLabel("Size:"))
        
        self.sphere_size_slider = QtWidgets.QSlider(Qt.Orientation.Horizontal)
        self.sphere_size_slider.setMinimum(3)  # 60px (3 * 20)
        self.sphere_size_slider.setMaximum(15)  # 300px (15 * 20)
        self.sphere_size_slider.setValue(9)  # 180px default
        self.sphere_size_slider.setTickPosition(QtWidgets.QSlider.TickPosition.TicksBelow)
        
        slider_style = """
            QSlider::groove:horizontal {
                height: 4px;
                background: #d3d3d3;
                margin: 2px 0;
                border-radius: 2px;
            }
            QSlider::handle:horizontal {
                background: #ffffff;
                border: 1px solid #999999;
                width: 16px;
                height: 16px;
                margin: -6px 0;
                border-radius: 8px;
            }
            QSlider::handle:horizontal:hover {
                background: #f0f0f0;
                border: 1px solid #666666;
            }
        """
        self.sphere_size_slider.setStyleSheet(slider_style)
        
        self.sphere_size_label = QtWidgets.QLabel("180px")
        self.sphere_size_label.setMinimumWidth(50)
        self.sphere_size_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        
        self.sphere_size_slider.valueChanged.connect(self.update_sphere_size_label)
        
        size_layout.addWidget(self.sphere_size_slider)
        size_layout.addWidget(self.sphere_size_label)
        oracle_layout.addLayout(size_layout)
        oracle_frame.setLayout(oracle_layout)
        main_layout.addWidget(oracle_frame)

        # Language settings
        language_group = QtWidgets.QGroupBox("Language Settings")
        language_group.setFont(title_font)
        language_layout = QtWidgets.QVBoxLayout()
        self.language_combo = QtWidgets.QComboBox()
        self.language_combo.addItems(["English", "Spanish", "French", "German"])
        self.language_combo.setCurrentText("English")
        self.language_combo.setEnabled(False)
        language_layout.addWidget(QtWidgets.QLabel("Set Language:"))
        language_layout.addWidget(self.language_combo)
        language_group.setLayout(language_layout)
        language_group.setEnabled(False)
        main_layout.addWidget(language_group)

    def _load_settings(self):
        """Load settings from database and update UI."""
        settings = load_settings_from_db()
        
        # Load startup options
        self.load_splash_sound.setChecked(bool(settings.get('load_splash_sound', False)))
        self.show_about.setChecked(bool(settings.get('show_about', False)))
        
        # Load listening state
        listening_state = settings.get('startup_listening_state', 'remember')
        if listening_state == 'remember':
            self.remember_listening.setChecked(True)
        elif listening_state == 'stop':
            self.always_stop.setChecked(True)
        elif listening_state == 'start':
            self.always_start.setChecked(True)
        else:
            self.remember_listening.setChecked(True)
        
        # Load oracle settings
        self.restore_position.setChecked(settings.get('restore_position', True))
        sphere_size = settings.get('sphere_size', 180)
        slider_value = sphere_size // 20
        self.sphere_size_slider.setValue(slider_value)
        self.sphere_size_label.setText(f"{sphere_size}px")
        
        # Always set position to Custom
        self.position_combo.setCurrentText("Custom")
        
        # Load current oracle
        current_oracle = settings.get('selected_oracle')
        if current_oracle:
            index = self.switch_oracle.findData(current_oracle)
            if index >= 0:
                self.switch_oracle.setCurrentIndex(index)

    def setup_oracle_options(self):
        """Populate oracle combo box with available oracles."""
        self.switch_oracle.clear()
        
        # Get all gif files from oracle directory
        oracle_files = [f for f in os.listdir(ORACLE_DIR) if f.endswith('.gif')]
        oracle_files.sort(key=lambda x: int(os.path.splitext(x)[0]) if os.path.splitext(x)[0].isdigit() else float('inf'))
        
        # Add items with display name and keep filename as data
        for filename in oracle_files:
            display_name = self.get_oracle_display_name(filename)
            self.switch_oracle.addItem(display_name, filename)
        
        # Connect the change signal
        self.switch_oracle.currentIndexChanged.connect(self.on_oracle_changed)

    def get_oracle_display_name(self, filename):
        """Get display name for oracle file."""
        name = os.path.splitext(filename)[0]
        if name.isdigit():
            return f"Oracle {name}"
        return name.capitalize()

    def on_oracle_changed(self, index):
        """Handle oracle selection change."""
        selected_file = self.switch_oracle.currentData()
        if selected_file:
            settings = load_settings_from_db()
            settings['selected_oracle'] = selected_file
            save_settings_to_db(settings)
            signal_manager.direct_oracle_change.emit(selected_file)
            logging.debug(f"Emitted direct oracle change for: {selected_file}")

    def on_position_changed(self, position):
        """Handle position combo box changes."""
        settings = load_settings_from_db()
        settings['oracle_position'] = position
        save_settings_to_db(settings)
        signal_manager.oracle_position_changed.emit(position)
        logging.debug(f"Oracle position changed to: {position}")

    def update_sphere_size_label(self, value):
        """Update the size label when slider changes."""
        pixel_size = value * 20
        self.sphere_size_label.setText(f"{pixel_size}px")
        
        settings = load_settings_from_db()
        settings['sphere_size'] = pixel_size
        save_settings_to_db(settings)
        
        signal_manager.oracle_size_changed.emit(pixel_size)
        logging.debug(f"Emitted oracle size change: {pixel_size}px") 