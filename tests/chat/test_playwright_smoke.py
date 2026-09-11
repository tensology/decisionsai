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


def test_full_refresh_dedupe_does_not_replay_tail_on_next_incremental_refresh():
    """A deduped full render must keep its cursor aligned to source messages."""
    from playwright.sync_api import sync_playwright

    root = Path(__file__).resolve().parents[2]
    template = (root / "distr/gui/web/templates/chat/chat.html").read_text(encoding="utf-8")
    script_path = root / "distr/gui/web/static/chat/js/chat.js"
    messages = [
        {"role": "user", "content": "question 1", "chat_row_id": 1},
        {"role": "assistant", "content": "reply 1", "chat_row_id": 1},
        {"role": "assistant", "content": "reply 1", "chat_row_id": 1},
        *[
            {"role": "user" if i % 2 == 0 else "assistant", "content": f"message {i}", "chat_row_id": i}
            for i in range(3, 11)
        ],
    ]

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            page.set_content(template)
            page.add_script_tag(path=str(script_path))
            page.evaluate(
                """(initial) => {
                    currentChatId = 163;
                    renderMessages(initial, false);
                    renderMessages([...initial, {
                        role: 'assistant', content: 'tail reply', chat_row_id: 11
                    }], false);
                }""",
                messages,
            )
            assert page.locator("#chatMessages .message").count() == len(messages) - 1 + 1
            assert page.locator("#chatMessages .message-text", has_text="tail reply").count() == 1
        finally:
            browser.close()


def test_stream_finish_and_persisted_assistant_event_render_once():
    """The normal WS ordering must not create a second assistant bubble."""
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
                """() => {
                    currentChatId = 163;
                    loadedChatId = 163;
                    handleChatEventMessageAdded({
                        event: 'message_added', chat_id: 163, chat_row_id: 183,
                        role: 'user', content: 'Find local options'
                    });
                    handleChatEventStreamStarted({event: 'stream_started', chat_id: 163});
                    handleChatEventStreamToken({event: 'stream_token', chat_id: 163, token: 'Here are '});
                    handleChatEventStreamToken({event: 'stream_token', chat_id: 163, token: 'the options.'});
                }"""
            )
            page.wait_for_timeout(40)
            page.evaluate(
                """() => {
                    handleChatEventStreamFinished({
                        event: 'stream_finished', chat_id: 163,
                        chat_row_id: 184, response_text: 'Here are the options.'
                    });
                    handleChatEventMessageAdded({
                        event: 'message_added', chat_id: 163, chat_row_id: 184,
                        role: 'assistant', content: 'Here are the options.'
                    });
                }"""
            )
            assert page.locator("#chatMessages .message.user").count() == 1
            assert page.locator("#chatMessages .message.assistant").count() == 1
            assert page.locator("#chatMessages .message-text", has_text="Here are the options.").count() == 1
        finally:
            browser.close()


def test_transcription_preview_promotion_and_persisted_event_render_once():
    """PTT/STT preview promotion plus its durable event must stay one user row."""
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
                """() => {
                    currentChatId = 163;
                    loadedChatId = 163;
                    showTranscriptionStatus('Search nearby', false, false, false);
                    showTranscriptionStatus('', true, true, false);
                    handleChatEventMessageAdded({
                        event: 'message_added', chat_id: 163, chat_row_id: 185,
                        role: 'user', content: 'Search nearby'
                    });
                }"""
            )
            assert page.locator("#chatMessages .message.user").count() == 1
            assert page.locator("#chatMessages .message-text", has_text="Search nearby").count() == 1
            assert page.locator("#transcriptionStatus").count() == 0
        finally:
            browser.close()
