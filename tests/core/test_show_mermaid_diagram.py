"""Tests for show_mermaid_diagram agent tool."""

from unittest.mock import patch

from distr.core.agent.tools.chat.show_mermaid_diagram import ShowMermaidDiagramTool
from distr.gui.web.routes import diagrams as diagrams_mod


def test_show_mermaid_opens_blank_viewer_when_no_code(monkeypatch):
    opened = []

    monkeypatch.setattr(
        "distr.core.agent.tools.chat.show_mermaid_diagram.resolve_local_web_base_url",
        lambda: "http://127.0.0.1:8765",
    )
    monkeypatch.setattr(
        "distr.core.agent.tools.chat.show_mermaid_diagram.webbrowser.open",
        lambda url: opened.append(url),
    )
    diagrams_mod._save_history_rows([])

    result = ShowMermaidDiagramTool()._run(mermaid_code="")

    assert opened == ["http://127.0.0.1:8765/diagram/"]
    assert "Opened the diagram viewer" in result


def test_show_mermaid_opens_latest_history_when_no_code(monkeypatch):
    opened = []

    monkeypatch.setattr(
        "distr.core.agent.tools.chat.show_mermaid_diagram.resolve_local_web_base_url",
        lambda: "http://127.0.0.1:8765",
    )
    monkeypatch.setattr(
        "distr.core.agent.tools.chat.show_mermaid_diagram.webbrowser.open",
        lambda url: opened.append(url),
    )
    diagrams_mod._save_history_rows(
        [{"id": "hist123", "title": "User table", "code": "erDiagram\n  USER ||--o{ ORDER : places", "created_at": 1.0}]
    )

    result = ShowMermaidDiagramTool()._run(mermaid_code="")

    assert opened == ["http://127.0.0.1:8765/diagram/?id=hist123"]
    assert "most recent diagram" in result
    assert "User table" in result


def test_open_page_maps_diagram_viewer(monkeypatch):
    from distr.core.agent.tools.chat.open_page import OpenPageTool

    opened = []

    monkeypatch.setattr(
        "distr.core.agent.tools.chat.open_page.OpenPageTool._resolve_web_base_url",
        lambda self: "http://127.0.0.1:8765",
    )
    monkeypatch.setattr(
        "distr.core.agent.tools.chat.open_page.webbrowser.open",
        lambda url: opened.append(url),
    )

    result = OpenPageTool()._run(page="mermaid viewer")

    assert opened == ["http://127.0.0.1:8765/diagram/"]
    assert "Mermaid diagram viewer" in result


def test_open_page_maps_all_primary_product_surfaces(monkeypatch):
    from distr.core.agent.tools.chat.open_page import OpenPageTool

    opened = []
    monkeypatch.setattr(
        "distr.core.agent.tools.chat.open_page.OpenPageTool._resolve_web_base_url",
        lambda self: "http://127.0.0.1:8765",
    )
    monkeypatch.setattr(
        "distr.core.agent.tools.chat.open_page.webbrowser.open",
        lambda url: opened.append(url) or True,
    )

    tool = OpenPageTool()
    for page in ("chat", "development", "incoming", "automations", "terminals", "reports"):
        result = tool._run(page=page)
        assert "Opened URL: http://127.0.0.1:8765/" in result

    assert opened == [
        "http://127.0.0.1:8765/chat/",
        "http://127.0.0.1:8765/development/",
        "http://127.0.0.1:8765/development/incoming/",
        "http://127.0.0.1:8765/development/automations/",
        "http://127.0.0.1:8765/development/terminals/",
        "http://127.0.0.1:8765/development/reports/",
    ]


def test_open_chat_window_is_deterministically_routed_to_internal_page_tool():
    from distr.core.agent.services.llm.fast_action_detector import detect_fast_action

    for command in (
        "open the chat window",
        "Open Chat in Brave",
        "please launch the DecisionsAI chat web UI in Brave",
    ):
        action = detect_fast_action(command)
        assert action.tool_name == "open_page"
        assert action.tool_args == {"page": "chat"}
