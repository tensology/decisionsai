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

from distr.core.paths import IMAGES_DIR
from PyQt6 import QtWidgets, QtGui, QtCore
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtCore import Qt, QUrl
import os
from datetime import datetime

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
        self.center_on_screen()

    def _setup_window(self):
        """Configure main window properties and styling."""
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_DeleteOnClose, False)
        self.setWindowTitle("About DecisionsAI")
        self.setMinimumSize(1000, 650)
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
        self.content_widget.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding, QtWidgets.QSizePolicy.Policy.Expanding)
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
        text_widget.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding, QtWidgets.QSizePolicy.Policy.Expanding)
        text_layout = QtWidgets.QVBoxLayout(text_widget)
        text_layout.setContentsMargins(50, 50, 20, 20)
        text_layout.setSpacing(20)

        # Add title with orange "AI"
        title_label = QtWidgets.QLabel("Decisions<span style='color: #ff8800;'>AI</span>")
        title_label.setStyleSheet("font-size: 48px; font-weight: 700; letter-spacing: -1px;")
        title_label.setTextFormat(QtCore.Qt.TextFormat.RichText)
        text_layout.addWidget(title_label)

        # Add spacing to move tabbed widget down
        text_layout.addSpacing(30)

        # Add tabbed widget for descriptions and credits
        self._add_tabbed_content(text_layout)

        text_layout.addStretch()
        self.content_layout.addWidget(text_widget, 2)

    def _add_tabbed_content(self, layout):
        """
        Add tabbed widget containing descriptions and credits.
        
        Args:
            layout (QVBoxLayout): Layout to add tabbed widget to
        """
        # Create tabbed widget
        tab_widget = QtWidgets.QTabWidget()
        tab_widget.setStyleSheet(f"""
            QTabWidget::pane {{
                border: 1px solid #2a3a4c;
                background-color: #1a2a3c;
                border-radius: 4px;        
                padding: 0px;
                margin: 0px;
            }}
            QTabBar {{
                margin-left: 0px;
                padding-left: 0px;
                alignment: left;
            }}
            QTabWidget::tab-bar {{
                alignment: left;
            }}
            QTabBar::tab {{
                background-color: #0f1a2c;
                color: #cccccc;
                padding: 8px 20px;
                border-top-left-radius: 4px;
                border-top-right-radius: 4px;
                margin-right: 2px;
                font-size: 14px;
            }}
            QTabBar::tab:selected {{
                background-color: #1a2a3c;
                color: #ffffff;
                border-bottom: 2px solid #4a9eff;
            }}
            QTabBar::tab:hover {{
                background-color: #2a3a4c;
            }}
        """)
        
        # Force tabs to align left by setting tab bar properties
        tab_bar = tab_widget.tabBar()
        tab_bar.setExpanding(False)
        tab_bar.setUsesScrollButtons(False)
        tab_bar.setDrawBase(False)
        
        # Set tab bar to left-align by using document mode and ensuring it doesn't stretch
        tab_widget.setDocumentMode(False)
        
        # Create About tab (FIRST) - simple QTextBrowser with its own scrolling
        about_tab = QtWidgets.QWidget()
        about_tab.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding, QtWidgets.QSizePolicy.Policy.Expanding)
        about_layout = QtWidgets.QVBoxLayout(about_tab)
        about_layout.setContentsMargins(0, 0, 0, 0)
        about_layout.setSpacing(0)
        
        # Use QTextBrowser directly - it handles scrolling itself
        about_content = QtWidgets.QTextBrowser()
        about_content.setOpenExternalLinks(True)
        about_content.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding, QtWidgets.QSizePolicy.Policy.Expanding)
        about_content.setStyleSheet("""
            QTextBrowser {
                background-color: #1a2a3c;
                color: #ffffff;
                border: none;
                padding: 20px;
                font-size: 16px;
                line-height: 1.2;
                font-family: Arial, sans-serif;
            }
            QTextBrowser a {
                color: #4a9eff;
                text-decoration: underline;
            }
            QTextBrowser a:visited {
                color: #4a9eff;
            }
        """)
        
        # Convert descriptions to HTML
        descriptions = [
            ("DecisionsAI is an intelligent digital assistant that understands natural language and executes tasks on your computer. "
             "Simply speak to your computer in plain English, and DecisionsAI will interpret your commands and carry them out. "
             "No complex syntax or technical knowledge required—just communicate naturally and let DecisionsAI handle the rest."),
            ("Built using a plethora of leading-edge libraries and open-source models, DecisionsAI serves as an intelligent "
             "digital assistant capable of understanding and executing various tasks on your computer. "
             "It's designed to be more than just an information retrieval tool, with capabilities that "
             "include automation, voice interaction, and adaptive learning. DecisionsAI aims to streamline "
             "your workflow and enhance productivity through true, local, intuitive AI-driven assistance.")
        ]
        
        # Build HTML content from descriptions
        about_html = "<div style='color: #ffffff;'>"
        for text in descriptions:
            about_html += f'<p style="margin: 15px 0; font-size: 16px; line-height: 1.15; font-weight: 300;">{text}</p>'
        about_html += "</div>"
        
        about_content.setHtml(about_html)
        about_layout.addWidget(about_content)
        tab_widget.addTab(about_tab, "About")
        
        # Create Changelog tab - simple QTextBrowser with its own scrolling
        changelog_tab = QtWidgets.QWidget()
        changelog_tab.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding, QtWidgets.QSizePolicy.Policy.Expanding)
        changelog_layout = QtWidgets.QVBoxLayout(changelog_tab)
        changelog_layout.setContentsMargins(0, 0, 0, 0)
        changelog_layout.setSpacing(0)
        
        # Use QTextBrowser directly - it handles scrolling itself
        changelog_content = QtWidgets.QTextBrowser()
        changelog_content.setOpenExternalLinks(True)
        changelog_content.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding, QtWidgets.QSizePolicy.Policy.Expanding)
        changelog_content.setStyleSheet("""
            QTextBrowser {
                background-color: #1a2a3c;
                color: #ffffff;
                border: none;
                padding: 20px;
                font-size: 13px;
                line-height: 1.6;
                font-family: Arial, sans-serif;
            }
            QTextBrowser a {
                color: #4a9eff;
                text-decoration: underline;
            }
            QTextBrowser a:visited {
                color: #4a9eff;
            }
        """)
        
        # Load changelog file (CHANGELOG.md is in the project root)
        # Go up 4 levels: about.py -> dialogs -> gui -> distr -> project root
        project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
        changelog_path = os.path.join(project_root, "CHANGELOG.md")
        if os.path.exists(changelog_path):
            try:
                with open(changelog_path, 'r', encoding='utf-8') as f:
                    changelog_text = f.read()
                # Convert markdown to HTML for better display
                changelog_html = self._markdown_to_html(changelog_text)
                changelog_content.setHtml(changelog_html)
            except Exception as e:
                changelog_content.setPlainText(f"Error loading changelog: {str(e)}")
        else:
            changelog_content.setPlainText("Changelog file not found.")
        
        changelog_layout.addWidget(changelog_content)
        tab_widget.addTab(changelog_tab, "Changelog")
        
        # Create Credits tab - simple QTextBrowser like changelog
        credits_tab = QtWidgets.QWidget()
        credits_tab.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding, QtWidgets.QSizePolicy.Policy.Expanding)
        credits_layout = QtWidgets.QVBoxLayout(credits_tab)
        credits_layout.setContentsMargins(0, 0, 0, 0)
        credits_layout.setSpacing(0)
        
        # Use QTextBrowser directly - it handles scrolling itself
        credits_content = QtWidgets.QTextBrowser()
        credits_content.setOpenExternalLinks(True)
        credits_content.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding, QtWidgets.QSizePolicy.Policy.Expanding)
        credits_content.setStyleSheet("""
            QTextBrowser {
                background-color: #1a2a3c;
                color: #ffffff;
                border: none;
                padding: 20px;
                font-size: 13px;
                line-height: 1.6;
                font-family: Arial, sans-serif;
            }
            QTextBrowser a {
                color: #4a9eff;
                text-decoration: underline;
            }
            QTextBrowser a:visited {
                color: #4a9eff;
            }
        """)
        
        # Convert credits dictionary to HTML
        credits = {
            "Pipecat: Real-Time AI Voice Framework": "https://pipecat.ai/",
            "Vosk: Low Latency ASR Toolkit": "https://alphacephei.com/vosk/",
            "Whisper.cpp: Open-Source ASR Toolkit": "https://github.com/ggml-org/whisper.cpp",
            "AssemblyAI: Speech-to-Text & Audio Transcription": "https://www.assemblyai.com/",
            "Ollama: AI Model Deployment": "https://ollama.ai/",
            "OpenAI: API LLMs": "https://openai.com/",
            "OpenRouter: Unified LLM API Gateway": "https://openrouter.ai/",
            "Anthropic: Claude API": "https://www.anthropic.com/",
            "Kokoro: Text-to-Speech": "https://github.com/thewh1teagle/kokoro-onnx/",
            "ElevenLabs: Text-to-Speech": "https://elevenlabs.io/",
            "VoxCPM: Tokenizer-Free TTS & Voice Cloning": "https://github.com/OpenBMB/VoxCPM",
            "PyAutoGUI: GUI Automation (Used for Actions)": "https://pyautogui.readthedocs.io/",
            "Pydantic: Data Validation": "https://pydantic-docs.helpmanual.io/",
            "PyQt6: GUI Framework": "https://www.riverbankcomputing.com/software/pyqt/",
            "Masko: AI-Powered Design & Creative Tools": "http://masko.ai/",
        }
        
        # Oracle Globe Animation credits
        animation_credits = {
            "Paarth Desai (iesight)": "https://dribbble.com/iesight/about",
            "KlausHuang": "https://dribbble.com/KlausHuang/about",
            "lavon": "https://dribbble.com/lavon89",
            "DIMUZI": "https://dribbble.com/DIMUZI/about",
            "Hicy Won": "https://www.behance.net/Hicy",
            "Krystalgy": "https://dribbble.com/Krystalgy/about",
        }
        
        # Build HTML content from credits
        credits_html = "<div style='color: #ffffff;'>"
        for title, url in credits.items():
            credits_html += f'<p style="margin: 10px 0;"><a href="{url}" style="color: #4a9eff; text-decoration: underline;">{title}</a></p>'
        
        # Add separator and animation credits section
        credits_html += '<p style="margin: 20px 0 10px 0; color: #cccccc; font-weight: bold;">Oracle Globe Animation Credits:</p>'
        for title, url in animation_credits.items():
            if url:
                credits_html += f'<p style="margin: 5px 0 5px 20px;"><a href="{url}" style="color: #4a9eff; text-decoration: underline;">{title}</a></p>'
            else:
                credits_html += f'<p style="margin: 10px 0 5px 0; color: #cccccc; font-weight: bold;">{title}</p>'
        credits_html += "</div>"
        
        credits_content.setHtml(credits_html)
        credits_layout.addWidget(credits_content)
        tab_widget.addTab(credits_tab, "Credits")
        
        # Force tabs to left-align after all tabs are added
        tab_bar_layout = tab_bar.layout()
        if tab_bar_layout:
            tab_bar_layout.setAlignment(Qt.AlignmentFlag.AlignLeft)
        
        # Set size policy to allow horizontal expansion (100% width)
        size_policy = QtWidgets.QSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding, QtWidgets.QSizePolicy.Policy.Expanding)
        tab_widget.setSizePolicy(size_policy)
        
        # Set minimum height for the tab container (can still grow beyond this)
        # Options: setMinimumHeight(400), setMaximumHeight(800), or setFixedHeight(600)
        tab_widget.setMinimumHeight(450)  # Adjust this value as needed
        
        # Add tab widget directly to layout - it will expand to 100% width
        # The tab bar alignment is already set to left via CSS and tab_bar properties
        layout.addWidget(tab_widget)

    def _markdown_to_html(self, markdown_text):
        """
        Convert markdown text to HTML for display in QTextEdit.
        
        Args:
            markdown_text (str): Markdown formatted text
            
        Returns:
            str: HTML formatted text
        """
        import re
        
        # Replace emojis with text equivalents (do this FIRST, before any other processing)
        # Process the raw markdown text to replace emojis
        html = markdown_text
        
        # Define emoji replacements - order matters for compound emojis
        emoji_replacements = [
            ('🎤', '[Voice]'),
            ('🤖', '[AI]'),
            ('🖱️', '[Mouse]'),
            ('🎨', '[Visual]'),
            ('📁', '[Files]'),
            ('🎬', '[Actions]'),
            ('💬', '[Chat]'),
            ('🔍', '[Search]'),
            ('🌐', '[Web]'),
            ('🎵', '[Media]'),
            ('⚙️', '[Settings]'),
            ('✅', '[OK]'),
            ('⚠️', '[Warning]'),
        ]
        
        # Replace each emoji
        for emoji, replacement in emoji_replacements:
            html = html.replace(emoji, replacement)
        
        # Convert horizontal rules
        html = re.sub(r'^---+$', r'<hr style="border: none; border-top: 1px solid #2a3a4c; margin: 20px 0;">', html, flags=re.MULTILINE)
        
        # Convert headers (must be done before other conversions)
        html = re.sub(r'^# (.+)$', r'<h1 style="color: #4a9eff; font-size: 24px; margin-top: 20px; margin-bottom: 10px; font-weight: bold;">\1</h1>', html, flags=re.MULTILINE)
        html = re.sub(r'^## (.+)$', r'<h2 style="color: #4a9eff; font-size: 20px; margin-top: 18px; margin-bottom: 8px; font-weight: bold;">\1</h2>', html, flags=re.MULTILINE)
        html = re.sub(r'^### (.+)$', r'<h3 style="color: #6ab4ff; font-size: 16px; margin-top: 15px; margin-bottom: 6px; font-weight: bold;">\1</h3>', html, flags=re.MULTILINE)
        html = re.sub(r'^#### (.+)$', r'<h4 style="color: #6ab4ff; font-size: 14px; margin-top: 12px; margin-bottom: 5px; font-weight: bold;">\1</h4>', html, flags=re.MULTILINE)
        
        # Convert links first (before bold/italic)
        html = re.sub(r'\[([^\]]+)\]\(([^\)]+)\)', r'<a href="\2" style="color: #4a9eff; text-decoration: underline;">\1</a>', html)
        
        # Convert bold (after links to avoid conflicts)
        html = re.sub(r'\*\*(.+?)\*\*', r'<strong style="color: #ffffff; font-weight: bold;">\1</strong>', html)
        html = re.sub(r'__(.+?)__', r'<strong style="color: #ffffff; font-weight: bold;">\1</strong>', html)
        
        # Convert italic (after bold)
        html = re.sub(r'(?<!\*)\*([^*]+?)\*(?!\*)', r'<em style="color: #cccccc; font-style: italic;">\1</em>', html)
        html = re.sub(r'(?<!_)_([^_]+?)_(?!_)', r'<em style="color: #cccccc; font-style: italic;">\1</em>', html)
        
        # Convert inline code
        html = re.sub(r'`([^`]+?)`', r'<code style="background-color: #0f1a2c; color: #4a9eff; padding: 2px 4px; border-radius: 3px; font-family: monospace;">\1</code>', html)
        
        # Convert list items
        lines = html.split('\n')
        result = []
        in_list = False
        for line in lines:
            stripped = line.strip()
            if stripped.startswith('<li'):
                if not in_list:
                    result.append('<ul style="margin: 10px 0; padding-left: 25px;">')
                    in_list = True
                result.append(line)
            elif stripped.startswith('- ') and not stripped.startswith('- <'):
                # Handle markdown list items that weren't converted
                if not in_list:
                    result.append('<ul style="margin: 10px 0; padding-left: 25px;">')
                    in_list = True
                list_text = stripped[2:].strip()
                result.append(f'<li style="margin: 5px 0; color: #ffffff;">{list_text}</li>')
            else:
                if in_list:
                    result.append('</ul>')
                    in_list = False
                result.append(line)
        if in_list:
            result.append('</ul>')
        html = '\n'.join(result)
        
        # Convert remaining markdown list items
        html = re.sub(r'^- (.+)$', r'<li style="margin: 5px 0; color: #ffffff;">\1</li>', html, flags=re.MULTILINE)
        html = re.sub(r'^  - (.+)$', r'<li style="margin: 3px 0; margin-left: 20px; color: #e0e0e0;">\1</li>', html, flags=re.MULTILINE)
        
        # Convert line breaks (but preserve HTML tags)
        lines = html.split('\n')
        result = []
        for line in lines:
            stripped = line.strip()
            if not stripped or stripped.startswith('<') and stripped.endswith('>'):
                # HTML tag or empty line
                result.append(line)
            else:
                # Regular text line
                result.append(line + '<br>')
        html = '\n'.join(result)
        
        # Clean up double breaks
        html = re.sub(r'<br>\s*<br>', '<br><br>', html)
        
        # Wrap in body tag with styling
        html = f'<body style="color: #ffffff; font-family: Arial, sans-serif; line-height: 1.6;">{html}</body>'
        
        return html

    def _setup_image_content(self):
        """Configure and add the image content."""
        # Create a container widget for the right side (image + footer)
        right_side_widget = QtWidgets.QWidget()
        right_side_layout = QtWidgets.QVBoxLayout(right_side_widget)
        right_side_layout.setContentsMargins(0, 40, 0, 0)  # Added top margin of 40px
        right_side_layout.setSpacing(20)
        
        # Add image
        self.image_label = QtWidgets.QLabel()
        avatar_path = os.path.join(IMAGES_DIR, "avatar.webp")
        pixmap = QtGui.QPixmap(avatar_path)
        image_height = int(self.height() * 0.7)
        scaled_pixmap = pixmap.scaledToHeight(image_height, QtCore.Qt.TransformationMode.SmoothTransformation)
        self.image_label.setPixmap(scaled_pixmap)
        self.image_label.setAlignment(QtCore.Qt.AlignmentFlag.AlignRight | QtCore.Qt.AlignmentFlag.AlignVCenter)
        right_side_layout.addWidget(self.image_label)
        
        # Add footer under the image
        footer_widget = self._create_footer_widget()
        right_side_layout.addWidget(footer_widget)

        # Add the right side container to the content layout
        self.content_layout.addWidget(right_side_widget, 1)

    def _get_version_from_changelog(self):
        """
        Extract version from CHANGELOG.md.
        
        Returns:
            str: Version string like "2.1.9" or "Unreleased"
        """
        try:
            project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
            changelog_path = os.path.join(project_root, "CHANGELOG.md")
            if os.path.exists(changelog_path):
                with open(changelog_path, 'r', encoding='utf-8') as f:
                    for line in f:
                        # Look for patterns like "## [2.1.9] - 2026-01-29" or "## [Unreleased]"
                        import re
                        match = re.match(r'^##\s+\[([^\]]+)\]', line)
                        if match:
                            version = match.group(1)
                            # Skip "What's to Come" section header
                            if version != "What's to Come":
                                return version
            return "Unknown"
        except Exception:
            return "Unknown"

    def _create_footer_widget(self):
        """Create and return the footer widget with credits."""
        footer_widget = QtWidgets.QWidget()
        footer_layout = QtWidgets.QVBoxLayout(footer_widget)
        footer_layout.setContentsMargins(50, 20, 50, 20)  # Reduced top margin to 20
        footer_layout.setSpacing(8)
        footer_layout.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop)
        
        # Add footer labels - right aligned
        current_year = datetime.now().year
        # Get version dynamically from CHANGELOG.md
        version = self._get_version_from_changelog()
        version_label = QtWidgets.QLabel(f"Version {version} ({current_year})")
        version_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        footer_layout.addWidget(version_label)
        
        # Built by line with multiple links
        built_by_widget = QtWidgets.QWidget()
        built_by_layout = QtWidgets.QHBoxLayout(built_by_widget)
        built_by_layout.setContentsMargins(0, 0, 0, 0)
        built_by_layout.setSpacing(4)
        built_by_layout.addStretch()  # Push content to the right
        
        built_by_text = QtWidgets.QLabel("Built by")
        built_by_text.setStyleSheet("font-size: 12px; color: #cccccc; line-height: 1.2;")
        built_by_text.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        built_by_layout.addWidget(built_by_text)
        
        tensology_label = ClickableLabel("tensology.com", "https://www.tensology.com")
        tensology_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        built_by_layout.addWidget(tensology_label)
        
        separator = QtWidgets.QLabel("|")
        separator.setStyleSheet("font-size: 12px; color: #cccccc; line-height: 1.2;")
        separator.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        built_by_layout.addWidget(separator)
        
        decisionsai_label = ClickableLabel("decisionsai.net", "https://www.decisionsai.net")
        decisionsai_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        built_by_layout.addWidget(decisionsai_label)
        
        footer_layout.addWidget(built_by_widget)
        
        # Lead Developer line
        developer_widget = QtWidgets.QWidget()
        developer_layout = QtWidgets.QHBoxLayout(developer_widget)
        developer_layout.setContentsMargins(0, 0, 0, 0)
        developer_layout.setSpacing(4)
        developer_layout.addStretch()  # Push content to the right
        
        developer_text = QtWidgets.QLabel("Lead Developer:")
        developer_text.setStyleSheet("font-size: 12px; color: #cccccc; line-height: 1.2;")
        developer_text.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        developer_layout.addWidget(developer_text)
        
        paul_label = ClickableLabel("paulhoft.com", "https://www.paulhoft.com")
        paul_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        developer_layout.addWidget(paul_label)
        
        footer_layout.addWidget(developer_widget)

        footer_widget.setStyleSheet("""
            QWidget {
                background-color: transparent;
            }
            QLabel, ClickableLabel {
                font-size: 12px;
                color: #cccccc;
                font-weight: 400;
                line-height: 1.2;
            }
        """)
        
        return footer_widget

    def closeEvent(self, event):
        """
        Handle window close events.
        
        Args:
            event (QCloseEvent): The close event
        """
        event.ignore()
        self.hide()

    def center_on_screen(self, oracle_window=None):
        """Center the window on the primary screen, or position on the same screen as oracle window if provided."""
        if oracle_window and oracle_window.isVisible():
            # Get the screen that contains the oracle window
            oracle_screen = QtWidgets.QApplication.screenAt(oracle_window.geometry().center())
            if oracle_screen:
                # Get the screen geometry
                screen_geometry = oracle_screen.geometry()
                
                # Calculate center position on the oracle's screen
                center_x = screen_geometry.center().x()
                center_y = screen_geometry.center().y()
                
                # Calculate window position (centered on the oracle's screen)
                x = center_x - (self.width() // 2)
                y = center_y - (self.height() // 2)
                
                # Ensure window stays within screen bounds
                x = max(screen_geometry.left(), min(x, screen_geometry.right() - self.width()))
                y = max(screen_geometry.top(), min(y, screen_geometry.bottom() - self.height()))
                
                # Move window
                self.move(x, y)
            else:
                # Fallback: position relative to oracle window
                oracle_pos = oracle_window.pos()
                oracle_size = oracle_window.size()
                
                # Calculate position: center the about window on the oracle ball
                x = oracle_pos.x() + (oracle_size.width() // 2) - (self.width() // 2)
                y = oracle_pos.y() + (oracle_size.height() // 2) - (self.height() // 2)
                
                # Move window
                self.move(x, y)
        else:
            # Default: center on primary screen
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
