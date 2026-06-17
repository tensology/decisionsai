"""
Open a Mermaid diagram in the DecisionsAI freestanding viewer window.

Used when explaining architecture, flows, sequences, or other technical
structures where a visual diagram helps more than prose alone.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
import webbrowser
from typing import Any, Optional

from langchain.tools import BaseTool
from pydantic import BaseModel, Field

from distr.core.web_runtime import internal_api_headers, resolve_local_web_base_url
from distr.gui.web.routes.diagrams import list_diagram_history
from distr.gui.web.security import INTERNAL_AUTH_HEADER

logger = logging.getLogger(__name__)


class ShowMermaidDiagramInput(BaseModel):
    mermaid_code: str = Field(
        "",
        description=(
            "Valid Mermaid diagram source code. "
            "Leave empty to open the diagram viewer (shows your most recent diagram, or a blank editor)."
        ),
    )
    title: str = Field("Diagram", description="Short title shown in the viewer window")


class ShowMermaidDiagramTool(BaseTool):
    """Render a Mermaid diagram in a freestanding viewer window."""

    name: str = "show_mermaid_diagram"
    description: str = (
        "Open the DecisionsAI Mermaid diagram viewer — a freestanding window where the user can "
        "see rendered charts, edit source, export PNG/JPEG, or copy the image. "
        "When the user only asks to open the viewer (no diagram yet), call this with empty "
        "mermaid_code or use open_page with page='diagram viewer'. "
        "When explaining architecture, flows, sequences, state machines, ER models, or similar, "
        "provide valid Mermaid syntax in mermaid_code."
    )
    args_schema: type[BaseModel] = ShowMermaidDiagramInput
    chat_manager: Optional[Any] = Field(default=None, exclude=True)

    def __init__(self, chat_manager=None, **kwargs):
        super().__init__(**kwargs)
        self._chat_manager = chat_manager

    def _run(self, mermaid_code: str = "", title: str = "Diagram", **kwargs) -> str:
        return self._show(mermaid_code, title)

    async def _arun(self, mermaid_code: str = "", title: str = "Diagram", **kwargs) -> str:
        return self._show(mermaid_code, title)

    def _show(self, mermaid_code: str, title: str) -> str:
        code = (mermaid_code or "").strip()
        base_url = resolve_local_web_base_url()
        if not base_url:
            return "Error: Web server is not ready. Try again in a moment."

        if not code:
            return self._open_viewer_only(base_url)

        label = (title or "Diagram").strip()[:200] or "Diagram"
        headers = internal_api_headers()
        if not headers.get(INTERNAL_AUTH_HEADER):
            return "Error: Could not authenticate with the local web server."

        try:
            diagram_id = self._store_diagram(base_url, code, label, headers)
        except Exception as exc:
            logger.error("ShowMermaidDiagramTool: store failed: %s", exc, exc_info=True)
            return f"Error storing diagram: {exc}"

        url = f"{base_url}/diagram/?id={diagram_id}"
        return self._open_url(
            url,
            f"Opened the diagram viewer for '{label}'. "
            "The user can export PNG/JPEG or copy the image from that window.",
        )

    def _open_viewer_only(self, base_url: str) -> str:
        history = list_diagram_history()
        if history:
            latest = history[0] or {}
            diagram_id = (latest.get("id") or "").strip()
            label = (latest.get("title") or "Diagram").strip() or "Diagram"
            if diagram_id:
                url = f"{base_url}/diagram/?id={diagram_id}"
                return self._open_url(
                    url,
                    f"Opened the diagram viewer with your most recent diagram, '{label}'.",
                )

        url = f"{base_url}/diagram/"
        return self._open_url(
            url,
            "Opened the diagram viewer. You can write or paste Mermaid code there.",
        )

    def _open_url(self, url: str, success_message: str) -> str:
        try:
            webbrowser.open(url)
        except Exception as exc:
            logger.warning("ShowMermaidDiagramTool: browser open failed: %s", exc)
            return f"{success_message} Open this URL in your browser: {url}"
        return success_message

    def _store_diagram(self, base_url: str, code: str, title: str, headers: dict) -> str:
        payload = json.dumps({"code": code, "title": title}).encode("utf-8")
        req = urllib.request.Request(
            f"{base_url}/api/diagrams",
            data=payload,
            headers=headers,
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=8) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        diagram_id = (body or {}).get("id")
        if not diagram_id:
            raise RuntimeError("Diagram API did not return an id")
        return str(diagram_id)
