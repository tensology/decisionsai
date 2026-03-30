"""
Tool for opening DecisionsAI web UI pages by name.

Maps natural-language page names ("chat", "preferences", "step runner", etc.)
to the correct web URL and opens it in the default browser — exactly like the
tray context-menu items do.
"""

import json
import logging
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
    # Actions
    "actions":        "/actions/",
    # Snippets
    "snippets":       "/snippets/",
    # Projects
    "projects":       "/projects/",
    # Workflows / Step Runner
    "workflows":      "/workflows/",
    "step runner":    "/workflows/",
    "step-runner":    "/workflows/",
    "steprunner":     "/workflows/",
    # Kanban / Board
    "kanban":         "/kanban/",
    "board":          "/kanban/",
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
}

# Build a nice list for the tool description
_KNOWN_PAGES = ", ".join(sorted({
    "chat", "settings/preferences", "actions", "snippets", "projects",
    "workflows/step runner", "kanban/board", "docs/api docs",
    "activity log", "about",
}))


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

            # Build URL — try server object first, fall back to default port
            url = None
            try:
                from distr.gui.web.server import get_unified_server
                server = get_unified_server()
                if server and server.is_ready():
                    url = f"{server.get_url()}{path}"
            except Exception:
                pass
            if not url:
                url = f"http://localhost:8765{path}"

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

            return json.dumps({"status": "success", "page": label, "url": url, "silent": True})
        except Exception as e:
            logger.error(f"OpenPageTool: Error opening page: {e}", exc_info=True)
            return f"Error opening page: {e}"

    def _handle_new_chat(self) -> str:
        """Create a new chat and open the chat page."""
        try:
            chat_id = None
            if self._chat_manager:
                chat_id = self._chat_manager.create_chat("New Conversation", is_new=True)
                logger.info(f"OpenPageTool: Created new chat {chat_id}")

            # Open the chat page
            result = self._open_url("/chat/", "new chat")

            # Merge chat_id into the response
            try:
                data = json.loads(result)
                if chat_id:
                    data["chat_id"] = chat_id
                return json.dumps(data)
            except (json.JSONDecodeError, TypeError):
                return result
        except Exception as e:
            logger.error(f"OpenPageTool: Error creating new chat: {e}", exc_info=True)
            return f"Error creating new chat: {e}"
