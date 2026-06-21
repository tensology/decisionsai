"""
About window for DecisionsAI.

Shows app description, changelog, and credits in a tabbed layout.
"""

from distr.core.paths import IMAGES_DIR
from PyQt6 import QtWidgets, QtGui, QtCore
from PyQt6.QtCore import Qt
import os
from datetime import datetime

# Shared typography for About / Changelog / Credits tab bodies
_TAB_FONT_FAMILY = "Arial, sans-serif"
_TAB_FONT_SIZE_PX = 14
_TAB_LINE_HEIGHT = 1.5
_TAB_LINE_HEIGHT_PX = round(_TAB_FONT_SIZE_PX * _TAB_LINE_HEIGHT)
_TAB_PADDING_PX = 20
_TAB_TEXT_COLOR = "#ffffff"
_TAB_MUTED_COLOR = "#aab4c4"
_TAB_LINK_COLOR = "#4a9eff"
_TAB_PANEL_BG = "#1a2a3c"


def _tab_line_height_css() -> str:
    """Qt rich text applies line-height most reliably as pixels on block elements."""
    return f"{_TAB_LINE_HEIGHT_PX}px"


def _tab_body_stylesheet() -> str:
    return f"""
            QTextBrowser {{
                background-color: {_TAB_PANEL_BG};
                color: {_TAB_TEXT_COLOR};
                border: none;
                padding: {_TAB_PADDING_PX}px;
                font-size: {_TAB_FONT_SIZE_PX}px;
                line-height: {_tab_line_height_css()};
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
        f'line-height: {_tab_line_height_css()}; font-weight: 400; font-family: {_TAB_FONT_FAMILY}; '
        f'color: {color};">{inner_html}</p>'
    )


def _html_section_title(text: str) -> str:
    return (
        f'<p style="margin: 16px 0 8px 0; font-size: {_TAB_FONT_SIZE_PX}px; '
        f'line-height: {_tab_line_height_css()}; font-weight: 600; font-family: {_TAB_FONT_FAMILY}; '
        f'color: #cccccc;">{text}</p>'
    )


def _html_link(url: str, label: str) -> str:
    return f'<a href="{url}" style="color:{_TAB_LINK_COLOR}; text-decoration: underline;">{label}</a>'


def _wrap_tab_html(inner_html: str) -> str:
    return (
        f'<div style="color: {_TAB_TEXT_COLOR}; font-family: {_TAB_FONT_FAMILY}; '
        f'font-size: {_TAB_FONT_SIZE_PX}px; line-height: {_tab_line_height_css()};">{inner_html}</div>'
    )


def _configure_tab_browser(browser: QtWidgets.QTextBrowser) -> None:
    """Apply shared tab typography to a QTextBrowser instance."""
    browser.setStyleSheet(_tab_body_stylesheet())
    browser.document().setDefaultStyleSheet(
        f"body, div, p, li {{ font-family: {_TAB_FONT_FAMILY}; font-size: {_TAB_FONT_SIZE_PX}px; "
        f"line-height: {_tab_line_height_css()}; color: {_TAB_TEXT_COLOR}; }}"
        f"a {{ color: {_TAB_LINK_COLOR}; text-decoration: underline; }}"
    )


def _embed_browser_with_back(tab_layout: QtWidgets.QVBoxLayout, browser: QtWidgets.QTextBrowser) -> None:
    """Place a QTextBrowser in a tab with an in-content Back control when history exists."""
    browser.setOpenExternalLinks(True)
    browser.setOpenLinks(True)

    nav = QtWidgets.QWidget()
    nav_layout = QtWidgets.QHBoxLayout(nav)
    nav_layout.setContentsMargins(12, 8, 12, 0)
    nav_layout.setSpacing(8)

    back_button = QtWidgets.QPushButton("Back")
    back_button.setFixedWidth(72)
    back_button.setVisible(False)
    back_button.setStyleSheet("""
        QPushButton {
            background-color: #2a3a4c;
            color: #ffffff;
            border: 1px solid #3a4a5c;
            border-radius: 4px;
            padding: 4px 12px;
            font-size: 13px;
        }
        QPushButton:disabled {
            color: #667788;
            border-color: #2a3a4c;
        }
        QPushButton:hover:enabled {
            background-color: #3a4a5c;
        }
    """)
    back_button.clicked.connect(browser.backward)
    browser.backwardAvailable.connect(back_button.setVisible)
    browser.backwardAvailable.connect(back_button.setEnabled)

    nav_layout.addWidget(back_button)
    nav_layout.addStretch()
    tab_layout.addWidget(nav)
    tab_layout.addWidget(browser)


# ===========================================
# 1. Main Window
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

        # Create tabbed widget
        tab_widget = QtWidgets.QTabWidget()
        tab_widget.setStyleSheet(f"""
            QTabWidget::pane {{
                border: 1px solid #2a3a4c;
                background-color: #1a2a3c;
                border-radius: 4px;        
                padding: 0px;
                margin: 0px;
                top: -1px;
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
                border-bottom: 1px solid #1a2a3c;
                margin-bottom: -1px;
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

        about_content = QtWidgets.QTextBrowser()
        about_content.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding, QtWidgets.QSizePolicy.Policy.Expanding)
        _configure_tab_browser(about_content)

        about_paragraphs = [
            _html_paragraph(
                "The tech industry has two speeds: announce the revolution on Tuesday, "
                "ship a settings panel on Thursday, and call it a platform by Friday. "
                "We grew up in the Winamp era, when software had a personality, a skin folder, "
                "a hidden installer sample about whipping a llama, and the nerve to be "
                "a little ridiculous on purpose."
            ),
            _html_paragraph(
                "Work ethic, as we understand it: finish the thing, read the error, automate "
                "the part that makes you hate your job, and go home before you start defending "
                "a hallucination like it is company policy. Motion is not progress. A standup is "
                "not a deliverable. Your stack should still make sense when the hype cycle moves on."
            ),
            _html_paragraph(
                "Style is not decoration. Ugly tools train you to accept ugly thinking. "
                "We like software that looks like someone cared, runs where you put it, "
                "and does not need a webinar to explain its existence. "
                "Local models through Ollama when you want them. Cloud GPUs when you do not. "
                "Same standard either way: does it work, and can you live with it tomorrow."
            ),
            _html_paragraph(
                "If you opened this window you already found the app. "
                "We are not going to pitch you from an About box. "
                "The changelog is the other tab. "
                "The credits are the people and libraries we owe."
            ),
            _html_paragraph(
                "<em>It really whips the llama's ass.</em> Winamp buried that line in its "
                "installer twenty-six years ago as a goofy audio Easter egg. Then Ollama "
                "named their local inference stack after LLaMA models and the universe "
                "closed the loop. We did not plan the pun. We are just the ones still laughing.",
                muted=True,
            ),
        ]
        about_content.setHtml(_wrap_tab_html("".join(about_paragraphs)))
        _embed_browser_with_back(about_layout, about_content)
        tab_widget.addTab(about_tab, "About")
        
        # Create Changelog tab - simple QTextBrowser with its own scrolling
        changelog_tab = QtWidgets.QWidget()
        changelog_tab.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding, QtWidgets.QSizePolicy.Policy.Expanding)
        changelog_layout = QtWidgets.QVBoxLayout(changelog_tab)
        changelog_layout.setContentsMargins(0, 0, 0, 0)
        changelog_layout.setSpacing(0)
        
        # Use QTextBrowser directly - it handles scrolling itself
        changelog_content = QtWidgets.QTextBrowser()
        changelog_content.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding, QtWidgets.QSizePolicy.Policy.Expanding)
        _configure_tab_browser(changelog_content)
        
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
        
        _embed_browser_with_back(changelog_layout, changelog_content)
        tab_widget.addTab(changelog_tab, "Changelog")
        
        # Create Credits tab - simple QTextBrowser like changelog
        credits_tab = QtWidgets.QWidget()
        credits_tab.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding, QtWidgets.QSizePolicy.Policy.Expanding)
        credits_layout = QtWidgets.QVBoxLayout(credits_tab)
        credits_layout.setContentsMargins(0, 0, 0, 0)
        credits_layout.setSpacing(0)
        
        # Use QTextBrowser directly - it handles scrolling itself
        credits_content = QtWidgets.QTextBrowser()
        credits_content.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding, QtWidgets.QSizePolicy.Policy.Expanding)
        _configure_tab_browser(credits_content)
        
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
            credits_parts.append(
                _html_paragraph(_html_link(url, title), margin="6px 0 6px 16px")
            )

        credits_content.setHtml(_wrap_tab_html("".join(credits_parts)))
        _embed_browser_with_back(credits_layout, credits_content)
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
            f"font-size: {_TAB_FONT_SIZE_PX}px; line-height: {_tab_line_height_css()};"
        )
        link = f"color: {_TAB_LINK_COLOR}; text-decoration: underline;"
        h1 = (
            f"color: {_TAB_LINK_COLOR}; font-size: {_TAB_FONT_SIZE_PX}px; "
            f"margin: 16px 0 8px 0; font-weight: 600; line-height: {_tab_line_height_css()};"
        )
        h2 = (
            f"color: {_TAB_LINK_COLOR}; font-size: {_TAB_FONT_SIZE_PX}px; "
            f"margin: 14px 0 6px 0; font-weight: 600; line-height: {_tab_line_height_css()};"
        )
        h3 = (
            f"color: #6ab4ff; font-size: {_TAB_FONT_SIZE_PX}px; "
            f"margin: 12px 0 4px 0; font-weight: 600; line-height: {_tab_line_height_css()};"
        )
        h4 = (
            f"color: #6ab4ff; font-size: {_TAB_FONT_SIZE_PX}px; "
            f"margin: 10px 0 4px 0; font-weight: 600; line-height: {_tab_line_height_css()};"
        )
        li = f"margin: 4px 0; color: {_TAB_TEXT_COLOR}; line-height: {_tab_line_height_css()};"
        li_nested = f"margin: 2px 0; margin-left: 16px; color: #e0e0e0; line-height: {_tab_line_height_css()};"
        block_tag = re.compile(r"^</?(?:h[1-4]|ul|li|hr)\b", re.IGNORECASE)

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

        # Wrap prose lines in paragraphs so Qt applies line-height consistently.
        lines = html.split('\n')
        result = []
        for line in lines:
            stripped = line.strip()
            if not stripped:
                continue
            if block_tag.match(stripped):
                result.append(stripped)
            else:
                result.append(_html_paragraph(stripped))
        html = '\n'.join(result)

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
        
        built_by_label = QtWidgets.QLabel("Built by tensology.com · decisionsai.net")
        built_by_label.setStyleSheet("font-size: 12px; color: #cccccc; line-height: 1.2;")
        built_by_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        built_by_layout.addWidget(built_by_label)
        
        footer_layout.addWidget(built_by_widget)
        
        # Lead Developer line
        developer_widget = QtWidgets.QWidget()
        developer_layout = QtWidgets.QHBoxLayout(developer_widget)
        developer_layout.setContentsMargins(0, 0, 0, 0)
        developer_layout.setSpacing(4)
        developer_layout.addStretch()  # Push content to the right
        
        developer_label = QtWidgets.QLabel("Lead Developer: paulhoft.com")
        developer_label.setStyleSheet("font-size: 12px; color: #cccccc; line-height: 1.2;")
        developer_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        developer_layout.addWidget(developer_label)
        
        footer_layout.addWidget(developer_widget)

        footer_widget.setStyleSheet("""
            QWidget {
                background-color: transparent;
            }
            QLabel {
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
