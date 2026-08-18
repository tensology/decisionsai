"""Live browser acceptance for the durable multi-stage Chat turn panel.

Run with DecisionsAI listening on 127.0.0.1:8765:
  rtk pytest -m e2e_playwright tests/chat/test_chat_turn_browser_e2e.py -q
"""

from __future__ import annotations

import json
import os
from contextlib import contextmanager
from pathlib import Path

import pytest
from playwright.sync_api import Page, expect
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from distr.core import chat_turns
from distr.core.agent.tools.artifacts import ArtifactTool
from distr.core.db import Chat, ChatTurnEvent


pytestmark = pytest.mark.e2e_playwright
BASE_URL = os.environ.get("CHAT_E2E_BASE_URL", "http://127.0.0.1:8765").rstrip("/")
RUNTIME_DB_PATH = Path(
    os.environ.get(
        "CHAT_E2E_DB_PATH",
        str(Path(__file__).resolve().parents[2] / "db" / "settings.db"),
    )
).expanduser()
_runtime_engine = create_engine(
    f"sqlite:///{RUNTIME_DB_PATH}",
    connect_args={"check_same_thread": False, "timeout": 10},
)
_RuntimeSession = sessionmaker(bind=_runtime_engine, expire_on_commit=False)


@contextmanager
def _runtime_session():
    """Use the database read by the already-running desktop app.

    The root pytest configuration intentionally redirects normal imports to an
    isolated temporary database.  This acceptance test drives the live server,
    so its fixture data must live beside that server instead.
    """
    session = _RuntimeSession()
    try:
        yield session
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


@pytest.fixture(autouse=True)
def _route_turn_lifecycle_to_runtime_db(monkeypatch: pytest.MonkeyPatch):
    if not RUNTIME_DB_PATH.exists():
        pytest.skip(f"Live DecisionsAI database is unavailable: {RUNTIME_DB_PATH}")
    monkeypatch.setattr(chat_turns, "get_session", _runtime_session)


def _seed_turn() -> tuple[int, int, str]:
    now_title = "Chat turn browser acceptance"
    with _runtime_session() as session:
        root = Chat(
            title=now_title,
            provider="Ollama",
            model_name="ornith:9b",
            voice_provider="kokoro",
            voice_model="af_heart",
        )
        session.add(root)
        session.commit()
        session.refresh(root)
        turn = Chat(parent_id=root.id, input="Inspect the project and report back.", response="")
        session.add(turn)
        session.commit()
        session.refresh(turn)
        root.params = json.dumps({"active_turn_chat_row_id": int(turn.id)})
        session.commit()
        root_id, turn_id = int(root.id), int(turn.id)
    chat_turns.ensure_turn_started(root_id, turn_id)
    event_id, _, _ = chat_turns.start_tool(
        root_id,
        "file_operations",
        title="Inspect project files",
        summary="Reading the relevant files.",
    )
    assert event_id
    return root_id, turn_id, event_id


def _seed_plain_turn() -> tuple[int, int]:
    with _runtime_session() as session:
        root = Chat(
            title="Plain chat turn browser acceptance",
            provider="OpenAI",
            model_name="gpt-5.2",
            voice_provider="kokoro",
            voice_model="af_heart",
        )
        session.add(root)
        session.commit()
        turn = Chat(parent_id=root.id, input="Are you ready?", response="")
        session.add(turn)
        session.commit()
        root.params = json.dumps({"active_turn_chat_row_id": int(turn.id)})
        session.commit()
        root_id, turn_id = int(root.id), int(turn.id)
    chat_turns.ensure_turn_started(root_id, turn_id)
    return root_id, turn_id


def _notify(page: Page, payload: dict) -> None:
    token = page.locator('meta[name="decisionsai-internal-api-token"]').get_attribute("content") or ""
    response = page.request.post(
        f"{BASE_URL}/api/internal/notify-chat-event",
        headers={"X-DecisionsAI-Internal-Token": token},
        data={"type": "turn_event", "event": "turn_event", **payload},
    )
    assert response.ok, response.text()


def _cleanup(root_id: int) -> None:
    with _runtime_session() as session:
        session.query(ChatTurnEvent).filter(ChatTurnEvent.chat_id == root_id).delete()
        session.query(Chat).filter(Chat.parent_id == root_id).delete()
        session.query(Chat).filter(Chat.id == root_id).delete()
        session.commit()


def test_plain_conversation_does_not_show_work_controls(page: Page) -> None:
    root_id, turn_id = _seed_plain_turn()
    try:
        page.goto(
            f"{BASE_URL}/chat/?id={root_id}&from_create=1",
            wait_until="domcontentloaded",
            timeout=30000,
        )
        expect(page.locator(f"#turnPanel-{turn_id}")).to_have_count(0)
        expect(page.locator("#turnStopButton")).to_be_hidden()
        expect(page.locator("#messageInput")).to_have_attribute("placeholder", "Send message…")

        terminal = chat_turns.complete_turn(
            root_id,
            turn_id=turn_id,
            display_text="Yes, I am ready.",
            speech_text="Yes, I am ready.",
        )
        _notify(page, terminal)
        expect(page.locator(f"#turnPanel-{turn_id}")).to_have_count(0)
        expect(page.locator("#turnStopButton")).to_be_hidden()
    finally:
        _cleanup(root_id)


@pytest.mark.parametrize(
    "viewport",
    [{"width": 1440, "height": 900}, {"width": 390, "height": 844}],
    ids=["desktop", "mobile"],
)
def test_turn_panel_realtime_steer_refresh_and_terminal_recovery(page: Page, viewport: dict) -> None:
    try:
        if not page.request.get(f"{BASE_URL}/api/chats").ok:
            pytest.skip("DecisionsAI server is unavailable")
    except Exception:
        pytest.skip("DecisionsAI server is unavailable")

    root_id, turn_id, first_event_id = _seed_turn()
    page.set_viewport_size(viewport)
    console_errors: list[str] = []
    page.on("console", lambda msg: console_errors.append(msg.text) if msg.type == "error" else None)
    try:
        page.goto(
            f"{BASE_URL}/chat/?id={root_id}&from_create=1",
            wait_until="domcontentloaded",
            timeout=30000,
        )
        panel = page.locator(f"#turnPanel-{turn_id}")
        expect(panel).to_be_visible(timeout=15000)
        expect(panel).to_contain_text("I’m working through that now.")
        expect(panel).to_contain_text("Inspect project files")
        expect(page.locator("#messageInput")).to_be_enabled()
        expect(page.locator("#messageInput")).to_have_attribute("placeholder", "Steer the active work…")
        expect(page.locator("#turnStopButton")).to_be_visible()
        expect(page.locator("#sendButton")).not_to_have_attribute("title", "Stop")
        expect(panel.locator(".turn-panel-phase")).to_have_text("Inspect project files")
        step_toggle = panel.locator(".turn-panel-details > summary")
        hit_target = step_toggle.evaluate(
            """element => {
                const rect = element.getBoundingClientRect();
                const container = document.querySelector('#chatMessages');
                const containerRect = container?.getBoundingClientRect();
                const hit = document.elementFromPoint(rect.left + rect.width / 2, rect.top + rect.height / 2);
                return {rect: {left: rect.left, top: rect.top, width: rect.width, height: rect.height},
                        container: containerRect ? {top: containerRect.top, bottom: containerRect.bottom,
                            scrollTop: container.scrollTop, clientHeight: container.clientHeight,
                            scrollHeight: container.scrollHeight} : null,
                        pointerEvents: getComputedStyle(element).pointerEvents,
                        hit: hit?.tagName || '', hitClass: hit?.className || '',
                        ownsHit: Boolean(hit && (hit === element || element.contains(hit)))};
            }"""
        )
        assert hit_target["ownsHit"], hit_target
        step_toggle.click()
        spinner = panel.locator(f'[data-turn-event-id="{first_event_id}"] .turn-step-spinner')
        expect(spinner).to_be_visible()

        # A stable event is updated in place over WebSocket, not appended twice.
        completed = chat_turns.finish_tool(
            first_event_id,
            success=True,
            summary="Relevant files inspected.",
            detail="The project structure is available for synthesis.",
        )
        _notify(page, completed)
        expect(panel.locator(f'[data-turn-event-id="{first_event_id}"]')).to_have_count(1)
        expect(panel.locator(f'[data-turn-event-id="{first_event_id}"]')).to_contain_text("Relevant files inspected")

        # Composer messages steer the active turn and do not cancel it.
        page.locator("#messageInput").fill("Keep the result concise and focus on regressions.")
        page.locator("#sendButton").click()
        expect(panel).to_contain_text("Guidance added", timeout=10000)
        expect(page.locator("#turnStopButton")).to_be_visible()

        synthesis = chat_turns.begin_synthesis(root_id, turn_id)
        _notify(page, synthesis)
        expect(panel).to_contain_text("Preparing the answer", timeout=10000)
        terminal = chat_turns.complete_turn(
            root_id,
            turn_id=turn_id,
            display_text="The project inspection is complete.",
            speech_text="The project inspection is complete.",
        )
        _notify(page, terminal)
        expect(panel).to_contain_text("Completed", timeout=10000)
        expect(page.locator("#turnStopButton")).to_be_hidden()

        # Refresh recovers ordered durable events and terminal state without a
        # duplicate panel or a resurrected spinner.
        page.reload(wait_until="domcontentloaded")
        panel = page.locator(f"#turnPanel-{turn_id}")
        expect(panel).to_have_count(1)
        expect(panel).to_contain_text("Completed")
        expect(panel.locator(f'[data-turn-event-id="{first_event_id}"]')).to_have_count(1)
        expect(page.locator("#turnStopButton")).to_be_hidden()
        overflow = page.evaluate("() => document.documentElement.scrollWidth > document.documentElement.clientWidth")
        assert overflow is False
        relevant_errors = [e for e in console_errors if "favicon" not in e.lower() and "websocket" not in e.lower()]
        assert not relevant_errors, relevant_errors
    finally:
        _cleanup(root_id)


def test_built_tool_shows_ordered_execution_steps(page: Page, monkeypatch: pytest.MonkeyPatch) -> None:
    """A frozen runtime tool exposes each deterministic step in the live Chat panel."""
    try:
        if not page.request.get(f"{BASE_URL}/api/chats").ok:
            pytest.skip("DecisionsAI server is unavailable")
    except Exception:
        pytest.skip("DecisionsAI server is unavailable")

    root_id, turn_id, initial_event_id = _seed_turn()

    class _ChatManager:
        @staticmethod
        def get_current_chat() -> int:
            return root_id

    class _EchoTool:
        @staticmethod
        def invoke(arguments: dict) -> str:
            return f"echo:{arguments.get('value', '')}"

    from distr.core.agent.tools import loader
    from distr.core.agent.tool_audit import record_tool_start

    original_get_cached_tool = loader.get_cached_tool
    monkeypatch.setattr(
        loader,
        "get_cached_tool",
        lambda name: _EchoTool() if name == "test_echo" else original_get_cached_tool(name),
    )
    artifact = {
        "format": 1,
        "name": "built__demo_pipeline",
        "description": "Run two deterministic test steps.",
        "input_schema": {
            "type": "object",
            "properties": {"value": {"type": "string"}},
            "required": ["value"],
        },
        "steps": [
            {
                "id": "normalize_input",
                "name": "Normalize input",
                "kind": "tool",
                "tool": "test_echo",
                "arguments": {"value": "${inputs.value}"},
            },
            {
                "id": "format_output",
                "name": "Format output",
                "kind": "tool",
                "tool": "test_echo",
                "arguments": {"value": "${steps.normalize_input.output}"},
            },
        ],
    }

    try:
        chat_turns.finish_tool(
            initial_event_id,
            success=True,
            summary="Project context ready.",
            detail="Project context ready.",
        )
        outer_event_id = record_tool_start(
            root_id,
            "built__demo_pipeline",
            instruction_hint="Run the generated deterministic capability",
        )
        assert outer_event_id
        result = ArtifactTool(artifact, chat_manager=_ChatManager()).invoke({"value": "alpha"})
        chat_turns.finish_tool(
            outer_event_id,
            success=True,
            summary=result,
            detail=result,
        )
        chat_turns.complete_turn(
            root_id,
            turn_id=turn_id,
            display_text="The generated capability completed both steps.",
            speech_text="The generated capability completed both steps.",
        )

        page.goto(
            f"{BASE_URL}/chat/?id={root_id}&from_create=1",
            wait_until="domcontentloaded",
            timeout=30000,
        )
        panel = page.locator(f"#turnPanel-{turn_id}")
        expect(panel).to_be_visible(timeout=15000)
        expect(panel).to_contain_text("Completed")
        panel.locator(".turn-panel-details > summary").click()
        expect(panel).to_contain_text("Run the generated deterministic capability")
        expect(panel).to_contain_text("Step 1: Normalize input")
        expect(panel).to_contain_text("Step 2: Format output")
        expect(panel).to_contain_text("echo:echo:alpha")
        screenshot_path = os.environ.get("CHAT_E2E_SCREENSHOT_PATH", "").strip()
        if screenshot_path:
            page.screenshot(path=screenshot_path, full_page=True)
    finally:
        _cleanup(root_id)
