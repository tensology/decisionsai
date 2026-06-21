"""
Tool for opening DecisionsAI web UI pages by name.

Maps natural-language page names ("chat", "preferences", "workflows", etc.)
to the correct web URL and opens it in the default browser — exactly like the
tray context-menu items do.
"""

import logging
import urllib.request
import webbrowser
from typing import Any, Optional

from langchain.tools import BaseTool
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# Canonical page map: alias(es) → path on the unified web server
_PAGE_MAP = {
    # Chat
    "chat":           "/chat/",
    "chats":          "/chat/",
    # Settings / Preferences
    "settings":       "/settings",
    "preferences":    "/settings",
    "preference":     "/settings",
    # Settings sections
    "general":        "/settings#general",
    "general settings": "/settings#general",
    "general and audio": "/settings#general",
    "general & audio": "/settings#general",
    "initiative":     "/settings#initiative",
    "audio":          "/settings#general",
    "audio settings": "/settings#general",
    "third party":    "/settings#thirdparty",
    "third-party":    "/settings#thirdparty",
    "thirdparty":     "/settings#thirdparty",
    "providers":      "/settings#thirdparty",
    "api keys":       "/settings#thirdparty",
    "llms":           "/settings#llms",
    "llm":            "/settings#llms",
    "language models": "/settings#llms",
    "models":         "/settings#llms",
    "skins":          "/settings#skins",
    "skin":           "/settings#skins",
    "skin settings":  "/settings#skins",
    "oracle skin":    "/settings#skins",
    "avatar":         "/settings#skins",
    "avatars":        "/settings#skins",
    "advanced":       "/settings#advanced",
    "advanced settings": "/settings#advanced",
    # Actions
    "actions":        "/actions/",
    # Skills
    "skills":         "/skills/",
    "snippets":       "/skills/",
    # Projects
    "projects":       "/projects/",
    # Workflows
    "workflows":      "/workflows/",
    "workflow":       "/workflows/",
    # Ticket Board / Board
    "kanban":         "/tickets/",
    "board":          "/tickets/",
    "ticket board":   "/tickets/",
    "ticketboard":    "/tickets/",
    # API Docs
    "api docs":       "/docs/",
    "api documentation": "/docs/",
    "docs":           "/docs/",
    "documentation":  "/docs/",
    # Activity Log
    "activity log":   "/settings#logs",
    "logs":           "/settings#logs",
    "activity":       "/settings#logs",
    # About
    "about":          "/settings#about",
    "about us":       "/settings#about",
    # Mermaid / diagram viewer
    "diagram":        "/diagram/",
    "diagrams":       "/diagram/",
    "diagram viewer": "/diagram/",
    "mermaid":        "/diagram/",
    "mermaid viewer": "/diagram/",
    "mermaid editor": "/diagram/",
    "mermaid diagram": "/diagram/",
    # Download manager
    "downloads":      "/settings#downloads",
    "download manager": "/settings#downloads",
    "mermaid history": "/settings#mermaid",
}

# Build a nice list for the tool description
_KNOWN_PAGES = ", ".join(sorted({
    "chat", "settings/preferences", "general & audio", "initiative", "audio",
    "third party/providers/api keys", "llms/models", "skins/avatar",
    "advanced", "actions", "skills", "projects",
    "workflows", "ticket board", "docs/api docs",
    "activity log", "about", "diagram/mermaid viewer", "download manager",
}))


def _confirmation_for_path(path: str) -> str:
    """Short conversational confirmation for chat/TTS (no URLs — TTS strips http links)."""
    if not path:
        return "I've opened that page in your browser."
    if "/chat" in path or path.startswith("/chat"):
        return "I've opened Chat in your browser."
    if "/tickets" in path:
        return "I've opened the Ticket Board in your browser."
    if "/projects" in path:
        return "I've opened Projects in your browser."
    if "/workflows" in path:
        return "I've opened Workflows in your browser."
    if "/actions" in path:
        return "I've opened Actions in your browser."
    if "/skills" in path:
        return "I've opened Skills in your browser."
    if "/docs" in path:
        return "I've opened the API documentation in your browser."
    if "/diagram" in path:
        return "I've opened the Mermaid diagram viewer in your browser."
    if "/downloads" in path:
        return "I've opened the Download Manager in your browser."
    if "/settings" in path:
        frag = ""
        if "#" in path:
            frag = path.split("#", 1)[1].lower()
        section = {
            "general": "General Audio",
            "initiative": "Initiative",
            "thirdparty": "Third Party Vendors",
            "llms": "LLMs",
            "skins": "Skins",
            "advanced": "Advanced",
            "logs": "Activity Logs",
            "downloads": "Downloads",
            "mermaid": "Mermaid History",
            "about": "About",
        }.get(frag, "")
        if section:
            return f"I've opened Settings ({section}) in your browser."
        return "I've opened Settings in your browser."
    return "I've opened that page in your browser."


class OpenPageInput(BaseModel):
    page: str = Field(description="Name of the page to open")


class OpenPageTool(BaseTool):
    """Opens a DecisionsAI web UI page in the default browser."""

    name: str = "open_page"
    description: str = (
        "Open a DecisionsAI web UI page in the default browser. "
        "Use when the user says 'open chat', 'open preferences', 'open projects', etc. "
        f"Known pages: {_KNOWN_PAGES}. "
        "Also handles 'new chat' by creating a fresh chat and opening the chat page."
    )
    args_schema: type[BaseModel] = OpenPageInput
    chat_manager: Optional[Any] = Field(default=None, exclude=True)

    def __init__(self, chat_manager=None, **kwargs):
        super().__init__(**kwargs)
        self._chat_manager = chat_manager

    def _run(self, page: str = "", **kwargs) -> str:
        return self._open(page)

    async def _arun(self, page: str = "", **kwargs) -> str:
        return self._open(page)

    def _open(self, page: str) -> str:
        page_lower = (page or "").strip().lower()
        if not page_lower:
            return "Error: No page name provided."

        # Special case: "new chat" — create a chat then open the chat page
        if page_lower in ("new chat", "new conversation"):
            return self._handle_new_chat()

        # Look up the path
        path = _PAGE_MAP.get(page_lower)
        if not path:
            # Fuzzy: check if any key starts with or contains the input
            for key, val in _PAGE_MAP.items():
                if page_lower in key or key in page_lower:
                    path = val
                    break

        if not path:
            return (
                f"Unknown page '{page}'. "
                f"Known pages: {_KNOWN_PAGES}."
            )

        return self._open_url(path, page_lower)

    def _open_url(self, path: str, label: str) -> str:
        """Open a path on the web server. Takes a screenshot for Telegram."""
        try:
            import time

            # Build URL from the live GUI server. This tool runs in the agent
            # subprocess, so get_unified_server() may be unavailable there.
            # Probe local health endpoints to discover the current runtime port.
            base_url = self._resolve_web_base_url()
            if not base_url:
                return (
                    "Error: Web server is not ready right now. "
                    "Please try again in a moment."
                )
            url = f"{base_url}{path}"

            webbrowser.open(url)
            logger.info(f"OpenPageTool: Opened {url}")

            # If this is a Telegram request, take a screenshot after a short delay
            import threading
            if hasattr(threading.current_thread(), 'telegram_request'):
                time.sleep(2)
                try:
                    import tempfile
                    from distr.core.agent.tools.vision.screen_capture import capture_screenshot
                    screenshot_path = tempfile.mktemp(suffix='.png', prefix='open_page_')
                    if capture_screenshot(screenshot_path):
                        threading.current_thread().telegram_send_raw_screenshot = screenshot_path
                        logger.info(f"OpenPageTool: Screenshot captured for Telegram: {screenshot_path}")
                except Exception as e:
                    logger.warning(f"OpenPageTool: Could not capture screenshot: {e}")

            # Return plain language for chat + TTS. JSON with URLs was shown verbatim and
            # clean_text_for_tts replaces https://... with "a web link", corrupting JSON.
            return _confirmation_for_path(path)
        except Exception as e:
            logger.error(f"OpenPageTool: Error opening page: {e}", exc_info=True)
            return f"Error opening page: {e}"

    def _resolve_web_base_url(self) -> Optional[str]:
        """Resolve the active DecisionsAI web server base URL.

        Priority:
        1) In-process unified server singleton (main process path)
        2) Probe localhost ports (agent subprocess path)
        """
        try:
            from distr.gui.web.server import get_unified_server
            server = get_unified_server()
            if server and server.is_ready():
                return server.get_url()
        except Exception:
            pass

        hosts = ("127.0.0.1", "localhost")
        # Unified server starts at 8765 and may increment when occupied.
        ports = range(8765, 8781)
        for host in hosts:
            for port in ports:
                base = f"http://{host}:{port}"
                health_url = f"{base}/health"
                try:
                    with urllib.request.urlopen(health_url, timeout=0.25) as resp:
                        if resp.status == 200:
                            return base
                except Exception:
                    continue
        return None

    def _handle_new_chat(self) -> str:
        """Create a new chat and open the chat page."""
        try:
            chat_id = None
            if self._chat_manager:
                chat_id = self._chat_manager.create_chat("New Conversation", is_new=True)
                logger.info(f"OpenPageTool: Created new chat {chat_id}")

            result = self._open_url("/chat/", "new chat")
            if result.startswith("Error"):
                return result
            return "I've started a new conversation and opened Chat in your browser."
        except Exception as e:
            logger.error(f"OpenPageTool: Error creating new chat: {e}", exc_info=True)
            return f"Error creating new chat: {e}"
