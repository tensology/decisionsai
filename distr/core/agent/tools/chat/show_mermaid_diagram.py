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

logger = logging.getLogger(__name__)



class ShowMermaidDiagramInput(BaseModel):
    mermaid_code: str = Field(..., description="Valid Mermaid diagram source code")
    title: str = Field("Diagram", description="Short title shown in the viewer window")


class ShowMermaidDiagramTool(BaseTool):
    """Render a Mermaid diagram in a freestanding viewer window."""

    name: str = "show_mermaid_diagram"
    description: str = (
        "Open a Mermaid diagram in the DecisionsAI diagram viewer — a freestanding window "
        "where the user can see the rendered chart, edit the source, export PNG/JPEG, or copy "
        "the image to the clipboard. Use whenever a technical explanation benefits from a "
        "visual: architecture (flowchart/graph), sequences, state machines, ER diagrams, "
        "deployment views, or step-by-step flows. Provide valid Mermaid syntax in mermaid_code."
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
        if not code:
            return "Error: mermaid_code is required."
        label = (title or "Diagram").strip()[:200] or "Diagram"

        base_url = self._resolve_web_base_url()
        if not base_url:
            return "Error: Web server is not ready. Try again in a moment."

        try:
            diagram_id = self._store_diagram(base_url, code, label)
        except Exception as exc:
            logger.error("ShowMermaidDiagramTool: store failed: %s", exc, exc_info=True)
            return f"Error storing diagram: {exc}"

        url = f"{base_url}/diagram/?id={diagram_id}"
        try:
            webbrowser.open(url)
        except Exception as exc:
            logger.warning("ShowMermaidDiagramTool: browser open failed: %s", exc)
            return f"Diagram saved. Open this URL in your browser: {url}"

        return (
            f"Opened the diagram viewer for '{label}'. "
            "The user can export PNG/JPEG or copy the image from that window."
        )

    def _store_diagram(self, base_url: str, code: str, title: str) -> str:
        payload = json.dumps({"code": code, "title": title}).encode("utf-8")
        req = urllib.request.Request(
            f"{base_url}/api/diagrams",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=8) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        diagram_id = (body or {}).get("id")
        if not diagram_id:
            raise RuntimeError("Diagram API did not return an id")
        return str(diagram_id)

    def _resolve_web_base_url(self) -> Optional[str]:
        try:
            from distr.gui.web.server import get_unified_server

            server = get_unified_server()
            if server and server.is_ready():
                return server.get_url()
        except Exception:
            pass

        hosts = ("127.0.0.1", "localhost")
        for host in hosts:
            for port in range(8765, 8781):
                base = f"http://{host}:{port}"
                try:
                    with urllib.request.urlopen(f"{base}/health", timeout=0.25) as resp:
                        if resp.status == 200:
                            return base
                except Exception:
                    continue
        return None
