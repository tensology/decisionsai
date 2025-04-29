"""
About.py - About Window Management System

This module provides the About window interface for DecisionsAI with features including:
- Clickable links to external resources
- Scrollable credits section
- Responsive layout with image and text content
- Window positioning utilities
- Custom styling and theming

The system uses PyQt6 for the GUI components with support for:
- Custom widget implementations
- Event handling
- Window management
- Layout management

Key Features:
- Clickable external links
- Scrollable credits section
- Responsive layout system
- Custom styling
- Window positioning
- Event handling

Class Organization:
1. Link Management (ClickableLabel)
2. Credits Display (Credits)
3. Main Window (AboutWindow)
"""

from distr.core.constants import IMAGES_DIR
from PyQt6 import QtWidgets, QtGui, QtCore
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtCore import Qt, QUrl
import os

# ===========================================
# 1. Link Management
# ===========================================
class ClickableLabel(QtWidgets.QLabel):
    """
    Custom QLabel implementation that provides clickable links to external resources.
    
    This class extends QLabel to create clickable text that opens URLs in the default browser.
    It includes custom styling and cursor behavior for better user experience.
    """
    
    def __init__(self, text, url, parent=None):
        """
        Initialize the clickable label with text and target URL.
        
        Args:
            text (str): The text to display
            url (str): The URL to open when clicked
            parent (QWidget, optional): Parent widget
        """
        super().__init__(text, parent)
        self.url = url
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setStyleSheet("color: white; text-decoration: underline;")

    def mousePressEvent(self, event):
        """
        Handle mouse press events by opening the URL.
        
        Args:
            event (QMouseEvent): The mouse event
        """
        QDesktopServices.openUrl(QUrl(self.url))

# ===========================================
# 2. Credits Display
# ===========================================
class Credits(QtWidgets.QScrollArea):
    """
    Scrollable credits display that shows all attribution links.
    
    Provides a scrollable area containing clickable links to all libraries,
    tools, and resources used in the application.
    """
    
    def __init__(self, credits, parent=None):
        """
        Initialize the credits scroll area.
        
        Args:
            credits (dict): Dictionary of credit titles and their URLs
            parent (QWidget, optional): Parent widget
        """
        super().__init__(parent)
        self._setup_scroll_area()
        self._create_content(credits)

    def _setup_scroll_area(self):
        """Configure the scroll area properties and styling."""
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        self.setStyleSheet("background-color: #1a2a3c; border: none;")

    def _create_content(self, credits):
        """
        Create and populate the credits content.
        
        Args:
            credits (dict): Dictionary of credit titles and their URLs
        """
        content = QtWidgets.QWidget()
        self.setWidget(content)
        layout = QtWidgets.QVBoxLayout(content)

        for title, url in credits.items():
            label = ClickableLabel(title, url)
            layout.addWidget(label)

        layout.addStretch()

# ===========================================
# 3. Main Window
# ===========================================
class AboutWindow(QtWidgets.QMainWindow):
    """
    Main about window implementation displaying application information.
    
    Provides a comprehensive view of the application's description,
    credits, and additional information in a styled window.
    """

    def __init__(self, parent=None):
        """
        Initialize the about window with all components.
        
        Args:
            parent (QWidget, optional): Parent widget
        """
        super().__init__(parent)
        self._setup_window()
        self._create_layout()
        self._setup_content()
        self._setup_footer()
        self.center_on_screen()

    def _setup_window(self):
        """Configure main window properties and styling."""
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_DeleteOnClose, False)
        self.setWindowTitle("About Decisions")
        self.setFixedSize(1000, 600)
        self.setStyleSheet("""
            QMainWindow, QWidget {
                background-color: #0f1a2c;
                color: #ffffff;
                font-family: Arial, sans-serif;
            }
        """)

    def _create_layout(self):
        """Create and configure the main layout structure."""
        central_widget = QtWidgets.QWidget()
        self.setCentralWidget(central_widget)

        self.main_layout = QtWidgets.QVBoxLayout(central_widget)
        self.main_layout.setContentsMargins(0, 0, 0, 0)
        self.main_layout.setSpacing(0)

        self.content_widget = QtWidgets.QWidget()
        self.content_layout = QtWidgets.QHBoxLayout(self.content_widget)
        self.content_layout.setContentsMargins(0, 0, 0, 0)

    def _setup_content(self):
        """Set up the main content area with text and image."""
        self._setup_text_content()
        self._setup_image_content()
        self.main_layout.addWidget(self.content_widget)

    def _setup_text_content(self):
        """Configure and populate the text content area."""
        text_widget = QtWidgets.QWidget()
        text_layout = QtWidgets.QVBoxLayout(text_widget)
        text_layout.setContentsMargins(50, 50, 20, 20)
        text_layout.setSpacing(20)

        # Add title
        title_label = QtWidgets.QLabel("DecisionsAI")
        title_label.setStyleSheet("font-size: 48px; font-weight: 700; letter-spacing: -1px;")
        text_layout.addWidget(title_label)

        # Add descriptions
        self._add_descriptions(text_layout)
        
        # Add credits
        self._add_credits(text_layout)

        text_layout.addStretch()
        self.content_layout.addWidget(text_widget, 2)

    def _add_descriptions(self, layout):
        """
        Add description text to the layout.
        
        Args:
            layout (QVBoxLayout): Layout to add descriptions to
        """
        descriptions = [
            ("Since the dawn of civilization, humanity has sought to harness the power of the subservient (aka; Slave). "
             "Speak to your computer as you would a South African car-gaurd and let DecisionsAI figure it out."),
            ("Built using a plethora of leading-edge libraries and open-source models, DecisionsAI serves as an intelligent "
             "digital assistant capable of understanding and executing various tasks on your computer. "
             "It's designed to be more than just an information retrieval tool, with capabilities that "
             "include automation, voice interaction, and adaptive learning. DecisionsAI aims to streamline "
             "your workflow and enhance productivity through true, local, intuitive AI-driven assistance.")
        ]

        for text in descriptions:
            description = QtWidgets.QLabel(text)
            description.setWordWrap(True)
            description.setStyleSheet("font-size: 14px; line-height: 1.6; font-weight: 300;")
            layout.addWidget(description)

    def _add_credits(self, layout):
        """
        Add credits section to the layout.
        
        Args:
            layout (QVBoxLayout): Layout to add credits to
        """
        credits = {
            "Vosk: Low Latency ASR Toolkit": "https://alphacephei.com/vosk/",
            "Whisper.cpp: Open-Source ASR Toolkit": "https://github.com/ggml-org/whisper.cpp",
            "Ollama: AI Model Deployment": "https://ollama.ai/",
            "OpenAI: API LLMs": "https://openai.com/",
            "Kokoro: Text-to-Speech": "https://github.com/thewh1teagle/kokoro-onnx/",
            "ElevenLabs: Text-to-Speech": "https://elevenlabs.io/",
            "OpenInterpreter: Python Interpreter": "https://github.com/open-interpreter/open-interpreter",
            "PyAutoGUI: GUI Automation (Used for Actions)": "https://pyautogui.readthedocs.io/",
            "Pydantic: Data Validation": "https://pydantic-docs.helpmanual.io/",
            "PyQt6: GUI Framework": "https://www.riverbankcomputing.com/software/pyqt/",
        }
        credits_scroll = Credits(credits)
        layout.addWidget(credits_scroll)

    def _setup_image_content(self):
        """Configure and add the image content."""
        self.image_label = QtWidgets.QLabel()
        avatar_path = os.path.join(IMAGES_DIR, "avatar.jpg")
        pixmap = QtGui.QPixmap(avatar_path)
        image_height = int(self.height() * 0.7)
        scaled_pixmap = pixmap.scaledToHeight(image_height, QtCore.Qt.TransformationMode.SmoothTransformation)
        self.image_label.setPixmap(scaled_pixmap)
        self.image_label.setAlignment(QtCore.Qt.AlignmentFlag.AlignRight | QtCore.Qt.AlignmentFlag.AlignVCenter)
        self.content_layout.addWidget(self.image_label, 1)

    def _setup_footer(self):
        """Configure and add the footer content."""
        footer_widget = QtWidgets.QWidget()
        footer_layout = QtWidgets.QHBoxLayout(footer_widget)
        footer_layout.setContentsMargins(50, 10, 50, 10)

        right_footer = QtWidgets.QWidget()
        right_layout = QtWidgets.QVBoxLayout(right_footer)
        
        # Add footer labels
        version_label = QtWidgets.QLabel("Version 0.1.2 (2025)")
        right_layout.addWidget(version_label)
        company_label = ClickableLabel("Built by the tensology.com", "https://www.tensology.com")
        right_layout.addWidget(company_label)
        empty_label = QtWidgets.QLabel("")
        right_layout.addWidget(empty_label)
        
        right_layout.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        footer_layout.addWidget(right_footer)

        footer_widget.setStyleSheet("""
            QWidget {
                background-color: rgba(15, 26, 44, 0.8);
            }
            QLabel, ClickableLabel {
                font-size: 12px;
                color: #cccccc;
                font-weight: 400;
            }
        """)
        self.main_layout.addWidget(footer_widget)

    def closeEvent(self, event):
        """
        Handle window close events.
        
        Args:
            event (QCloseEvent): The close event
        """
        event.ignore()
        self.hide()

    def center_on_screen(self):
        """Center the window on the primary screen."""
        primary_screen = QtWidgets.QApplication.primaryScreen()
        screen_geometry = primary_screen.geometry()
        
        # Calculate center position
        center_x = screen_geometry.center().x()
        center_y = screen_geometry.center().y()
        
        # Calculate window position
        x = center_x - (self.width() // 2)
        y = center_y - (self.height() // 2)
        
        # Move window
        self.move(x, y)
