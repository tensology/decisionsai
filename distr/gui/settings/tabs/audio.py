"""
Audio Settings Tab Implementation

This module provides the AudioTab class which implements the audio settings tab
of the settings window. It handles audio device configuration, volume settings,
and input/output device management.

Key Features:
- Output device configuration
- Input device configuration
- Volume control
- Device persistence
- Audio device detection
"""

from PyQt6 import QtWidgets, QtGui, QtCore
from PyQt6.QtCore import Qt
from distr.gui.settings.utils.settings import load_settings_from_db, save_settings_to_db
import logging

class AudioTab(QtWidgets.QWidget):
    """
    Audio settings tab implementation.
    
    This class provides the UI and functionality for the audio settings tab,
    including output/input device configuration and volume control.
    """
    
    def __init__(self, parent=None):
        """
        Initialize the audio settings tab.
        
        Args:
            parent (QWidget, optional): Parent widget
        """
        super().__init__(parent)
        self._setup_ui()
        self._load_settings()
        logging.debug("AudioTab initialized")

    def _setup_ui(self):
        """Set up the UI components."""
        layout = QtWidgets.QVBoxLayout(self)
        layout.setSpacing(20)

        # Create a font for all group box titles
        title_font = QtGui.QFont()
        title_font.setPointSize(12)

        # Output settings
        self._setup_output_settings(layout, title_font)
        
        # Output & Input Group
        self._setup_device_settings(layout, title_font)
        
        # Volume controls
        self._setup_volume_controls(layout)

    def _setup_output_settings(self, parent_layout, title_font):
        """
        Set up the output settings section.
        
        Args:
            parent_layout (QLayout): Parent layout
            title_font (QFont): Title font
        """
        output_group = QtWidgets.QGroupBox("Output")
        output_group.setFont(title_font)
        output_layout = QtWidgets.QVBoxLayout()

        # Play output through
        play_output_layout = QtWidgets.QHBoxLayout()
        play_output_layout.addWidget(QtWidgets.QLabel("Play speech through:"))
        self.play_output_combo = QtWidgets.QComboBox()
        self.play_output_combo.addItems([
            "System Default", 
            "JBL TUNE500BT", 
            "MacBook Pro Speakers",
            "3rd Party (Korvo v1)",
            "3rd Party (Rasberry Pi)"
        ])
        play_output_layout.addWidget(self.play_output_combo)
        output_layout.addLayout(play_output_layout)

        # Play translation through
        play_translation_layout = QtWidgets.QHBoxLayout()
        play_translation_layout.addWidget(QtWidgets.QLabel("Play translation through:"))
        self.play_translation_combo = QtWidgets.QComboBox()
        self.play_translation_combo.addItems([
            "System Default", 
            "JBL TUNE500BT", 
            "MacBook Pro Speakers",
            "Grenade",
            "3rd Party (Korvo v1)",
            "3rd Party (Rasberry Pi)"
        ])
        play_translation_layout.addWidget(self.play_translation_combo)
        output_layout.addLayout(play_translation_layout)

        # Lock sound checkbox
        self.lock_sound_checkbox = QtWidgets.QCheckBox("Lock sound to setting")
        output_layout.addWidget(self.lock_sound_checkbox)

        # Explanation label
        explanation_label = QtWidgets.QLabel(
            "When your audio devices reconnect (e.g., Bluetooth, USB), we'll automatically restore your previously saved input and output settings."
        )
        explanation_label.setWordWrap(True)
        explanation_label.setStyleSheet("font-style: italic; color: #666;")
        output_layout.addWidget(explanation_label)

        output_group.setLayout(output_layout)
        parent_layout.addWidget(output_group)

    def _setup_device_settings(self, parent_layout, title_font):
        """
        Set up the device settings section.
        
        Args:
            parent_layout (QLayout): Parent layout
            title_font (QFont): Title font
        """
        output_input_group = QtWidgets.QGroupBox("Output & Input")
        output_input_group.setFont(title_font)
        output_input_layout = QtWidgets.QVBoxLayout()

        # Tabs for Output and Input
        output_input_tabs = QtWidgets.QTabWidget()
        output_tab = QtWidgets.QWidget()
        input_tab = QtWidgets.QWidget()
        output_input_tabs.addTab(output_tab, "Output")
        output_input_tabs.addTab(input_tab, "Input")

        # Output Tab
        output_layout = QtWidgets.QVBoxLayout(output_tab)
        self.output_device_list = QtWidgets.QTableWidget()
        self.output_device_list.setColumnCount(2)
        self.output_device_list.setHorizontalHeaderLabels(["Name", "Type"])
        self.output_device_list.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.ResizeMode.Stretch)
        self.output_device_list.verticalHeader().setVisible(False)
        self.output_device_list.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)
        self.populate_output_devices()
        output_layout.addWidget(self.output_device_list)

        # Input Tab
        input_layout = QtWidgets.QVBoxLayout(input_tab)
        self.input_device_list = QtWidgets.QTableWidget()
        self.input_device_list.setColumnCount(2)
        self.input_device_list.setHorizontalHeaderLabels(["Name", "Type"])
        self.input_device_list.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.ResizeMode.Stretch)
        self.input_device_list.verticalHeader().setVisible(False)
        self.input_device_list.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)
        self.populate_input_devices()
        input_layout.addWidget(self.input_device_list)

        output_input_layout.addWidget(output_input_tabs)
        output_input_group.setLayout(output_input_layout)
        parent_layout.addWidget(output_input_group)

    def _setup_volume_controls(self, parent_layout):
        """
        Set up the volume controls section.
        
        Args:
            parent_layout (QLayout): Parent layout
        """
        # Speech Volume
        speech_volume_layout = QtWidgets.QHBoxLayout()
        speech_volume_layout.addWidget(QtWidgets.QLabel("Speech Volume:"))
        self.speech_volume_slider = QtWidgets.QSlider(QtCore.Qt.Orientation.Horizontal)
        self.speech_volume_slider.setRange(0, 100)
        self.speech_volume_slider.setValue(50)
        speech_volume_layout.addWidget(self.speech_volume_slider)
        parent_layout.addLayout(speech_volume_layout)

        # Input Level
        input_level_layout = QtWidgets.QHBoxLayout()
        input_level_layout.addWidget(QtWidgets.QLabel("Input Level:"))
        self.input_level_indicator = QtWidgets.QProgressBar()
        self.input_level_indicator.setRange(0, 100)
        self.input_level_indicator.setValue(0)
        input_level_layout.addWidget(self.input_level_indicator)
        parent_layout.addLayout(input_level_layout)

    def _load_settings(self):
        """Load settings from database and update UI."""
        settings = load_settings_from_db()
        
        # Load output device
        output_device = settings.get('output_device', 'System Default')
        index = self.play_output_combo.findText(output_device)
        if index >= 0:
            self.play_output_combo.setCurrentIndex(index)
        
        # Load translation device
        translation_device = settings.get('translation_device', 'System Default')
        index = self.play_translation_combo.findText(translation_device)
        if index >= 0:
            self.play_translation_combo.setCurrentIndex(index)
        
        # Load lock sound setting
        self.lock_sound_checkbox.setChecked(bool(settings.get('lock_sound', False)))
        
        # Load speech volume
        volume = settings.get('speech_volume', 50)
        self.speech_volume_slider.setValue(volume)

    def populate_output_devices(self):
        """Populate the output devices list."""
        devices = [
            ("System Default", "Default"),
            ("MacBook Pro Speakers", "Built-in"),
            ("JBL TUNE500BT", "Bluetooth")
        ]  # Replace with actual device detection
        self.output_device_list.setRowCount(len(devices))
        for row, (name, type) in enumerate(devices):
            self.output_device_list.setItem(row, 0, QtWidgets.QTableWidgetItem(name))
            self.output_device_list.setItem(row, 1, QtWidgets.QTableWidgetItem(type))
        logging.debug("Output devices populated")

    def populate_input_devices(self):
        """Populate the input devices list."""
        devices = [
            ("System Default", "Default"),
            ("MacBook Pro Microphone", "Built-in"),
            ("JBL TUNE500BT", "Bluetooth")
        ]  # Replace with actual device detection
        self.input_device_list.setRowCount(len(devices))
        for row, (name, type) in enumerate(devices):
            self.input_device_list.setItem(row, 0, QtWidgets.QTableWidgetItem(name))
            self.input_device_list.setItem(row, 1, QtWidgets.QTableWidgetItem(type))
        logging.debug("Input devices populated") 