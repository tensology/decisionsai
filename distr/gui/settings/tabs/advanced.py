"""
Advanced Settings Tab Implementation

This module provides the AdvancedTab class which implements the advanced settings tab
of the settings window. It handles AI safety, data handling, and directory management.

Key Features:
- AI safety and data handling information
- Account connection management
- Directory tree view for data contextualization
- File type exclusion
- Model reindexing
"""

from PyQt6 import QtWidgets, QtGui, QtCore
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QScrollArea, QWidget, QVBoxLayout, QTreeView, QLineEdit
from distr.gui.settings.models.directory import CheckableDirModel
import logging

class AdvancedTab(QtWidgets.QWidget):
    """Advanced settings tab implementation"""
    
    def __init__(self, parent=None):
        """Initialize the advanced settings tab"""
        super().__init__(parent)
        self._setup_ui()
        logging.debug("AdvancedTab initialized")
        
    def _setup_ui(self):
        """Set up the UI components"""
        # Create main layout
        main_layout = QtWidgets.QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)
        
        # Create content widget
        content_widget = QWidget()
        content_widget.setStyleSheet("background-color: #f5f5f5;")
        content_layout = QVBoxLayout(content_widget)
        content_layout.setContentsMargins(20, 20, 20, 20)
        content_layout.setSpacing(15)
        
        # Add safety message
        safety_message = QtWidgets.QLabel("AI Safety & Data Handling")
        safety_message.setAlignment(Qt.AlignmentFlag.AlignCenter)
        safety_message.setStyleSheet("font-size: 14pt; font-weight: bold;")
        content_layout.addWidget(safety_message)
        
        # Add safety content
        safety_content = QtWidgets.QLabel(
            "This software is provided as-is, without warranties or guarantees. All data is stored locally on your device. The AI models and "
            "features are for personal use. You are responsible for reviewing and verifying any information or suggestions provided."
        )
        safety_content.setWordWrap(True)
        safety_content.setStyleSheet("font-size: 12pt;")
        safety_content.setAlignment(Qt.AlignmentFlag.AlignJustify)
        content_layout.addWidget(safety_content)
        
        # Add social media buttons
        button_layout = QtWidgets.QHBoxLayout()
        button_layout.setSpacing(10)
        
        self.facebook_button = self._create_styled_button("Facebook", "#1877F2")
        self.instagram_button = self._create_styled_button("Instagram", "#E1306C")
        self.google_button = self._create_styled_button("Google", "#4285F4")
        self.linkedin_button = self._create_styled_button("LinkedIn", "#0A66C2")
        
        for button in [self.facebook_button, self.instagram_button, self.google_button, self.linkedin_button]:
            button.setFixedHeight(35)
            button_layout.addWidget(button)
        
        content_layout.addLayout(button_layout)
        
        # Add directory tree view
        dir_tree_label = QtWidgets.QLabel("Directory Management")
        dir_tree_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        dir_tree_label.setStyleSheet("font-size: 14pt; font-weight: bold; margin-top: 10px;")
        content_layout.addWidget(dir_tree_label)
        
        self.dir_tree = QTreeView()
        self.dir_model = CheckableDirModel()
        self.dir_tree.setModel(self.dir_model)
        self.dir_tree.setHeaderHidden(True)
        self.dir_tree.setStyleSheet("""
            QTreeView {
                background-color: white;
                border: 1px solid #d0d0d0;
                min-height: 300px;
            }
            QTreeView::item {
                padding: 5px;
            }
        """)
        self.dir_tree.expanded.connect(self._on_item_expanded)
        self.dir_tree.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding, QtWidgets.QSizePolicy.Policy.Expanding)
        content_layout.addWidget(self.dir_tree, 2)  # Increased stretch factor to 2
        
        # Add exclude file types and reindex button
        exclude_layout = QtWidgets.QHBoxLayout()
        exclude_layout.setContentsMargins(0, 10, 0, 0)
        
        exclude_label = QtWidgets.QLabel("Exclude file types:")
        exclude_label.setStyleSheet("font-size: 12pt;")
        exclude_layout.addWidget(exclude_label)
        
        self.exclude_types = QLineEdit()
        self.exclude_types.setPlaceholderText("e.g., .jpg, .pdf, .doc")
        self.exclude_types.setFixedWidth(350)
        self.exclude_types.setStyleSheet("""
            QLineEdit {
                padding: 5px;
                border: 1px solid #d0d0d0;
                background-color: white;
            }
        """)
        exclude_layout.addWidget(self.exclude_types)
        
        exclude_layout.addStretch()
        
        self.reindex_button = QtWidgets.QPushButton("Reindex Models")
        self.reindex_button.setStyleSheet("""
            QPushButton {
                background-color: #4CAF50;
                color: white;
                border: none;
                padding: 8px 16px;
                font-size: 12pt;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #45a049;
            }
        """)
        exclude_layout.addWidget(self.reindex_button)
        
        content_layout.addLayout(exclude_layout)
        
        # Add content widget to main layout
        main_layout.addWidget(content_widget)
        
    def _create_styled_button(self, text, color):
        """Create a styled button with the given text and color"""
        button = QtWidgets.QPushButton(text)
        button.setStyleSheet(f"""
            QPushButton {{
                background-color: {color};
                color: white;
                border: none;
                padding: 8px 16px;
                font-size: 12pt;
                font-weight: bold;
            }}
            QPushButton:hover {{
                background-color: {self._darken_color(color)};
            }}
        """)
        return button
        
    def _darken_color(self, color):
        """Darken a hex color by 20%"""
        factor = 0.8
        r = int(color[1:3], 16)
        g = int(color[3:5], 16)
        b = int(color[5:7], 16)
        r = max(0, int(r * factor))
        g = max(0, int(g * factor))
        b = max(0, int(b * factor))
        return f"#{r:02x}{g:02x}{b:02x}"
        
    def _on_item_expanded(self, index):
        """Handle directory tree item expansion"""
        item = self.dir_model.itemFromIndex(index)
        if item.rowCount() == 1 and item.child(0).text() == "Loading...":
            self.dir_model.populate_directory(item) 