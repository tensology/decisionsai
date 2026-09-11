"""Minimal Playwright smoke test — verifies browser automation deps for chat/workflow flows."""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("playwright.sync_api")

pytestmark = pytest.mark.integration


def test_playwright_chromium_launches_and_evals():
    """Ensure Chromium launches without crashing (skip if browsers not installed)."""
    from playwright.sync_api import sync_playwright

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                page = browser.new_page()
                page.goto("about:blank")
                assert page.evaluate("() => 1 + 1") == 2
            finally:
                browser.close()
    except Exception as exc:
        pytest.skip(f"Playwright/Chromium unavailable: {exc}")


def test_delayed_websocket_user_event_does_not_duplicate_rendered_turn():
    """Reproduce the voice WS plus persisted poll race from the chat screenshots."""
    from playwright.sync_api import sync_playwright

    root = Path(__file__).resolve().parents[2]
    template = (root / "distr/gui/web/templates/chat/chat.html").read_text(encoding="utf-8")
    script_path = root / "distr/gui/web/static/chat/js/chat.js"

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            page.set_content(template)
            page.add_script_tag(path=str(script_path))
            page.evaluate(
                """
                () => {
                    currentChatId = 163;
                    handleChatEventMessageAdded({
                        event: 'message_added', chat_id: 163, chat_row_id: 183,
                        role: 'user', content: 'What is the difference?', timestamp: 1000
                    });
                    handleChatEventMessageAdded({
                        event: 'message_added', chat_id: 163, chat_row_id: 183,
                        role: 'assistant', content: 'Here is the difference.', timestamp: 1001
                    });
                    // Polling rendered the durable turn, then an older WS packet arrived.
                    handleChatEventMessageAdded({
                        event: 'message_added', chat_id: 163, chat_row_id: 183,
                        role: 'user', content: 'What is the difference?', timestamp: 1002
                    });
                }
                """
            )
            assert page.locator("#chatMessages .message.user").count() == 1
            assert page.locator("#chatMessages .message.assistant").count() == 1
        finally:
            browser.close()
