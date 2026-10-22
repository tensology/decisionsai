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


def test_conversation_history_uses_selected_oracle_avatar(tmp_path):
    """Persisted and newly streamed assistant rows share the selected skin identity."""
    from playwright.sync_api import sync_playwright

    root = Path(__file__).resolve().parents[2]
    template = (root / "distr/gui/web/templates/chat/chat.html").read_text(encoding="utf-8")
    identity_script_path = root / "distr/gui/web/static/shared/js/assistant_identity.js"
    script_path = root / "distr/gui/web/static/chat/js/chat.js"
    style_path = root / "distr/gui/web/static/chat/css/chat.css"

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            page.set_content(template)
            page.evaluate("document.head.insertAdjacentHTML('afterbegin', '<base href=\"http://decisions.test/\">')")
            page.route(
                "http://decisions.test/api/skins/oracle/preview/idle.png",
                lambda route: route.fulfill(
                    status=200,
                    content_type="image/svg+xml",
                    body='<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"><circle cx="32" cy="32" r="29" fill="#e88932"/><circle cx="32" cy="32" r="18" fill="none" stroke="#fff" stroke-width="4"/><circle cx="32" cy="32" r="5" fill="#fff"/></svg>',
                ),
            )
            page.route(
                "http://decisions.test/api/skins/hayley/preview/idle.webp",
                lambda route: route.fulfill(status=200, content_type="image/webp", path=str(root / "assets/avatars/hayley/idle.webp")),
            )
            page.route(
                "http://decisions.test/assets/img/llama.png",
                lambda route: route.fulfill(status=200, content_type="image/png", path=str(root / "assets/img/llama.png")),
            )
            page.evaluate(
                """() => {
                    window.fetch = async (url) => {
                        if (String(url) === '/api/skins') {
                            return new Response(JSON.stringify({
                                selected_skin: 'oracle',
                                skins: [{
                                    folder_name: 'oracle', name: 'Oracle', type: 'oracle',
                                    idle_animation: 'idle.png', image_scale: 1.55,
                                    image_offset_x: 0, image_offset_y: 0, chroma_key: null
                                }, {
                                    folder_name: 'hayley', name: 'Hayley', type: 'avatar',
                                    idle_animation: 'idle.webm', preview_image: 'idle.webp', chroma_key: [0, 255, 0]
                                }]
                            }), {status: 200, headers: {'Content-Type': 'application/json'}});
                        }
                        return new Response('{}', {status: 200, headers: {'Content-Type': 'application/json'}});
                    };
                }"""
            )
            page.add_style_tag(content="*,*::before,*::after{box-sizing:border-box}")
            page.add_style_tag(path=str(style_path))
            page.add_script_tag(path=str(identity_script_path))
            page.add_script_tag(path=str(script_path))
            page.evaluate(
                """async () => {
                    await loadAssistantAvatarSkin();
                    currentChatId = 163;
                    loadedChatId = 163;
                    renderMessages([
                        {role: 'user', content: 'Earlier question', chat_row_id: 1},
                        {role: 'assistant', content: 'Earlier answer', chat_row_id: 1}
                    ], false);
                    handleChatEventStreamStarted({event: 'stream_started', chat_id: 163});
                    document.body.classList.add('chat-has-active-thread');
                    document.getElementById('emptyState').style.display = 'none';
                    document.getElementById('chatMessages').style.display = 'flex';
                }"""
            )

            history_avatar = page.locator("#chatMessages .message.assistant .assistant-avatar-image").first
            assert history_avatar.get_attribute("data-skin-name") == "Oracle"
            assert history_avatar.get_attribute("src").endswith("/api/skins/oracle/preview/idle.png")
            assert history_avatar.evaluate("node => getComputedStyle(node).transform").startswith("matrix(1.55")
            assert page.locator("#chatMessages .message.user .assistant-avatar-image").count() == 0
            assert page.locator("#streamingAssistantMessage .assistant-avatar-image").count() == 1

            page.evaluate("document.body.insertAdjacentHTML('beforeend', '<div id=emptyStateSkinGrid></div><div id=modalSkinGrid></div>')")
            page.evaluate("Promise.all([loadEmptyStateSkins(), loadModalSkins()])")
            for grid_id in ("emptyStateSkinGrid", "modalSkinGrid"):
                hayley_card = page.locator(f"#{grid_id} [data-folder='hayley']")
                assert hayley_card.locator("img").get_attribute("src").endswith("/api/skins/hayley/preview/idle.webp")
                assert hayley_card.locator("video").count() == 0

            for width, height in ((1440, 900), (1024, 900), (390, 844)):
                page.set_viewport_size({"width": width, "height": height})
                page.locator(".app-container").screenshot(path=str(tmp_path / f"chat-oracle-{width}x{height}.png"))
            page.set_viewport_size({"width": 390, "height": 844})
            assert page.locator("#chatMessages").evaluate("node => getComputedStyle(node).backgroundSize").startswith("112px")
            assert page.locator("#llamaClickTarget").evaluate("node => Math.round(node.getBoundingClientRect().width)") == 88

            page.evaluate(
                """async () => {
                    window.fetch = async () => new Response(JSON.stringify({
                        selected_skin: 'clippy',
                        skins: [{
                            folder_name: 'clippy', name: 'Clippy', type: 'avatar',
                            idle_animation: 'idle.png', chroma_key: null
                        }]
                    }), {status: 200, headers: {'Content-Type': 'application/json'}});
                    await loadAssistantAvatarSkin(true);
                }"""
            )
            switched_avatars = page.locator("#chatMessages .message.assistant .assistant-avatar-image.avatar")
            assert switched_avatars.count() == 2
            assert all(name == "Clippy" for name in switched_avatars.evaluate_all("nodes => nodes.map(node => node.dataset.skinName)"))

            page.route(
                "http://decisions.test/api/skins/oracle/preview/0.webm",
                lambda route: route.fulfill(status=200, content_type="video/webm", path=str(root / "assets/avatars/oracle/0.webm")),
            )
            page.evaluate(
                """async () => {
                    window.fetch = async () => new Response(JSON.stringify({
                        selected_skin: 'oracle',
                        skins: [{
                            folder_name: 'oracle', name: 'Oracle', type: 'oracle',
                            idle_animation: '0.webm', chroma_key: null
                        }]
                    }), {status: 200, headers: {'Content-Type': 'application/json'}});
                    await loadAssistantAvatarSkin(true);
                }"""
            )
            animated_avatars = page.locator("#chatMessages .message.assistant video.assistant-avatar-image.oracle")
            assert animated_avatars.count() == 2
            assert animated_avatars.first.get_attribute("autoplay") is not None
            assert animated_avatars.first.get_attribute("loop") is not None
            animated_avatars.first.wait_for(state="visible")
            page.wait_for_timeout(250)
            assert animated_avatars.first.evaluate("video => !video.paused && video.currentTime > 0")

            terminal_panel = page.evaluate(
                """() => {
                    const panel = createTurnPanel({
                        turn_id: 999, status: 'completed', started_at: '2026-01-01T00:00:00Z',
                        completed_at: '2026-01-01T00:00:05Z',
                        events: [
                            {event_id: 'synthesis', event_type: 'synthesis_started', status: 'running', title: 'Preparing the answer'},
                            {event_id: 'done', event_type: 'turn_completed', status: 'completed', title: 'Answer ready'}
                        ]
                    });
                    document.body.appendChild(panel);
                    return {
                        spinners: panel.querySelectorAll('.turn-step-spinner').length,
                        completedMarks: panel.querySelectorAll('.turn-step-mark--complete').length
                    };
                }"""
            )
            assert terminal_panel == {"spinners": 0, "completedMarks": 1}
        finally:
            browser.close()


def test_chat_http_retry_reuses_one_work_intake_identity():
    """A lost HTTP response must not turn one submission into two tickets."""
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
            result = page.evaluate(
                """async () => {
                    loadedChatId = 163;
                    currentChatSettings = null;
                    ttsEnabled = false;
                    const requests = [];
                    let attempt = 0;
                    window.fetch = async (_url, options) => {
                        requests.push(JSON.parse(options.body));
                        attempt += 1;
                        return new Response(attempt === 1 ? 'temporarily unavailable' : '{}', {
                            status: attempt === 1 ? 503 : 200,
                            headers: {'Content-Type': 'application/json'}
                        });
                    };
                    await sendToAgentWhenReady('Fix the project login', null);
                    return requests;
                }"""
            )
            assert len(result) == 2
            assert result[0]["message"] == result[1]["message"] == "Fix the project login"
            assert result[0]["chat_id"] == result[1]["chat_id"] == 163
            assert result[0]["intake_source_message_id"] == result[1]["intake_source_message_id"]
            assert result[0]["intake_source_message_id"].startswith("web:163:")
        finally:
            browser.close()


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
