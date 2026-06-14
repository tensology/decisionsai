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

# Shared typography for About / Changelog / Credits tab bodies
_TAB_FONT_FAMILY = "Arial, sans-serif"
_TAB_FONT_SIZE_PX = 14
_TAB_LINE_HEIGHT = 1.5
_TAB_PADDING_PX = 20
_TAB_TEXT_COLOR = "#ffffff"
_TAB_MUTED_COLOR = "#aab4c4"
_TAB_LINK_COLOR = "#4a9eff"
_TAB_PANEL_BG = "#1a2a3c"


def _tab_body_stylesheet() -> str:
    return f"""
            QTextBrowser {{
                background-color: {_TAB_PANEL_BG};
                color: {_TAB_TEXT_COLOR};
                border: none;
                padding: {_TAB_PADDING_PX}px;
                font-size: {_TAB_FONT_SIZE_PX}px;
                line-height: {_TAB_LINE_HEIGHT};
                font-family: {_TAB_FONT_FAMILY};
            }}
            QTextBrowser a {{
                color: {_TAB_LINK_COLOR};
                text-decoration: underline;
            }}
            QTextBrowser a:visited {{
                color: {_TAB_LINK_COLOR};
            }}
        """


def _html_paragraph(inner_html: str, *, margin: str = "10px 0", muted: bool = False) -> str:
    color = _TAB_MUTED_COLOR if muted else _TAB_TEXT_COLOR
    return (
        f'<p style="margin: {margin}; font-size: {_TAB_FONT_SIZE_PX}px; '
        f'line-height: {_TAB_LINE_HEIGHT}; font-weight: 400; font-family: {_TAB_FONT_FAMILY}; '
        f'color: {color};">{inner_html}</p>'
    )


def _html_section_title(text: str) -> str:
    return (
        f'<p style="margin: 16px 0 8px 0; font-size: {_TAB_FONT_SIZE_PX}px; '
        f'line-height: {_TAB_LINE_HEIGHT}; font-weight: 600; font-family: {_TAB_FONT_FAMILY}; '
        f'color: #cccccc;">{text}</p>'
    )


def _html_link(url: str, label: str) -> str:
    return f'<a href="{url}" style="color:{_TAB_LINK_COLOR}; text-decoration: underline;">{label}</a>'


def _wrap_tab_html(inner_html: str) -> str:
    return (
        f'<div style="color: {_TAB_TEXT_COLOR}; font-family: {_TAB_FONT_FAMILY}; '
        f'font-size: {_TAB_FONT_SIZE_PX}px; line-height: {_TAB_LINE_HEIGHT};">{inner_html}</div>'
    )


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
        # Go up 4 levels: about.py -> dialogs -> gui -> distr -> project root
        project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

        def local_doc_url(*parts):
            return QUrl.fromLocalFile(os.path.join(project_root, *parts)).toString()

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
        
        tab_body_style = _tab_body_stylesheet()

        # Create About tab (FIRST) - simple QTextBrowser with its own scrolling
        about_tab = QtWidgets.QWidget()
        about_tab.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding, QtWidgets.QSizePolicy.Policy.Expanding)
        about_layout = QtWidgets.QVBoxLayout(about_tab)
        about_layout.setContentsMargins(0, 0, 0, 0)
        about_layout.setSpacing(0)

        about_content = QtWidgets.QTextBrowser()
        about_content.setOpenExternalLinks(True)
        about_content.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding, QtWidgets.QSizePolicy.Policy.Expanding)
        about_content.setStyleSheet(tab_body_style)

        orchestrator_url = local_doc_url("docs", "orchestrator.md")
        readme_url = local_doc_url("README.md")
        codex_url = local_doc_url("plugins", "codex-ide", "README.md")
        cursor_url = local_doc_url("plugins", "cursor-ide", "README.md")
        claude_url = local_doc_url("plugins", "ecc", "docs", "HERMES-SETUP.md")
        ecc_url = local_doc_url("plugins", "ecc", "README.md")
        sidecar_url = local_doc_url("sidecar", "README.md")

        about_paragraphs = [
            _html_paragraph("<b>Hey.</b> You clicked the llama. Respect."),
            _html_paragraph(
                "DecisionsAI is a local, voice-first workspace — chat, boards, automations, workflows, "
                "loops, IRC, and whatever you dragged in from the rest of your digital life. "
                "One machine, fewer tabs screaming at you."
            ),
            _html_paragraph(
                "Winamp-era energy, modern problems: skimmable when you want speed, deep when you want proof. "
                f"The {_html_link(orchestrator_url, 'orchestrator')} keeps the thread so handoffs do not evaporate. "
                "Version 2.8 tightens the web UI, adds Loops workflows, calendar automations, board-to-orchestrator handoff, "
                "in-thread model and voice changes, chat compaction, WhatsApp-to-board linking, IRC, and a rebuilt remote control."
            ),
            _html_paragraph(
                f"Build work routes through {_html_link(codex_url, 'Codex')}, "
                f"{_html_link(cursor_url, 'Cursor')}, {_html_link(claude_url, 'Claude harnessing')}, "
                f"and {_html_link(ecc_url, 'ECC')} — then back to the orchestrator like a responsible adult. "
                f"Screen and machine control via {_html_link(sidecar_url, 'Sidecar')} when you need hands."
            ),
            _html_paragraph(
                f"<b>Links:</b> {_html_link(readme_url, 'README')} · "
                f"{_html_link('https://www.decisionsai.net/', 'Website')} · "
                f"{_html_link('https://github.com/tensology/decisionsai', 'GitHub')} · "
                f"{_html_link('https://www.decisionsai.net/privacy', 'Privacy')} · "
                f"{_html_link('https://www.decisionsai.net/terms', 'Terms')}"
            ),
            _html_paragraph(
                "<em>It really whips Ollama's ass.</em> — vintage internet compliment. "
                "We are legally required to inform you the llama consented.",
                muted=True,
            ),
        ]
        about_content.setHtml(_wrap_tab_html("".join(about_paragraphs)))
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
        changelog_content.setStyleSheet(tab_body_style)
        
        # Load changelog file (CHANGELOG.md is in the project root)
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
        credits_content.setStyleSheet(tab_body_style)
        
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
            "Coqui TTS: Multi-Speaker & Voice Cloning (VCTK + XTTS v2)": "https://github.com/coqui-ai/TTS",
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
        
        credits_parts = [
            _html_paragraph(_html_link(url, title), margin="8px 0")
            for title, url in credits.items()
        ]
        credits_parts.append(_html_section_title("Oracle Globe Animation Credits"))
        for title, url in animation_credits.items():
            if url:
                credits_parts.append(
                    _html_paragraph(_html_link(url, title), margin="6px 0 6px 16px")
                )
            else:
                credits_parts.append(_html_section_title(title))

        credits_content.setHtml(_wrap_tab_html("".join(credits_parts)))
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

        body = (
            f"color: {_TAB_TEXT_COLOR}; font-family: {_TAB_FONT_FAMILY}; "
            f"font-size: {_TAB_FONT_SIZE_PX}px; line-height: {_TAB_LINE_HEIGHT};"
        )
        link = f"color: {_TAB_LINK_COLOR}; text-decoration: underline;"
        h1 = (
            f"color: {_TAB_LINK_COLOR}; font-size: {_TAB_FONT_SIZE_PX + 4}px; "
            f"margin: 16px 0 8px 0; font-weight: 600; line-height: {_TAB_LINE_HEIGHT};"
        )
        h2 = (
            f"color: {_TAB_LINK_COLOR}; font-size: {_TAB_FONT_SIZE_PX + 2}px; "
            f"margin: 14px 0 6px 0; font-weight: 600; line-height: {_TAB_LINE_HEIGHT};"
        )
        h3 = (
            f"color: #6ab4ff; font-size: {_TAB_FONT_SIZE_PX + 1}px; "
            f"margin: 12px 0 4px 0; font-weight: 600; line-height: {_TAB_LINE_HEIGHT};"
        )
        h4 = (
            f"color: #6ab4ff; font-size: {_TAB_FONT_SIZE_PX}px; "
            f"margin: 10px 0 4px 0; font-weight: 600; line-height: {_TAB_LINE_HEIGHT};"
        )
        li = f"margin: 4px 0; color: {_TAB_TEXT_COLOR}; line-height: {_TAB_LINE_HEIGHT};"
        li_nested = f"margin: 2px 0; margin-left: 16px; color: #e0e0e0; line-height: {_TAB_LINE_HEIGHT};"

        # Replace emojis with text equivalents (do this FIRST, before any other processing)
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
        html = re.sub(rf'^# (.+)$', rf'<h1 style="{h1}">\1</h1>', html, flags=re.MULTILINE)
        html = re.sub(rf'^## (.+)$', rf'<h2 style="{h2}">\1</h2>', html, flags=re.MULTILINE)
        html = re.sub(rf'^### (.+)$', rf'<h3 style="{h3}">\1</h3>', html, flags=re.MULTILINE)
        html = re.sub(rf'^#### (.+)$', rf'<h4 style="{h4}">\1</h4>', html, flags=re.MULTILINE)

        # Convert links first (before bold/italic)
        html = re.sub(rf'\[([^\]]+)\]\(([^\)]+)\)', rf'<a href="\2" style="{link}">\1</a>', html)
        
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
                result.append(f'<li style="{li}">{list_text}</li>')
            else:
                if in_list:
                    result.append('</ul>')
                    in_list = False
                result.append(line)
        if in_list:
            result.append('</ul>')
        html = '\n'.join(result)
        
        # Convert remaining markdown list items
        html = re.sub(rf'^- (.+)$', rf'<li style="{li}">\1</li>', html, flags=re.MULTILINE)
        html = re.sub(rf'^  - (.+)$', rf'<li style="{li_nested}">\1</li>', html, flags=re.MULTILINE)
        
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
        
        html = f'<body style="{body}">{html}</body>'

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
