"""Minimal Playwright smoke test — verifies browser automation deps for chat/workflow flows."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytest.importorskip("playwright.sync_api")

pytestmark = pytest.mark.integration


def test_all_sidebar_rows_reorder_and_viewed_chat_controls_corner_avatar(tmp_path):
    from playwright.sync_api import expect, sync_playwright

    root = Path(__file__).resolve().parents[2]
    web = root / "distr/gui/web"
    template = (web / "templates/chat/chat.html").read_text(encoding="utf-8")
    template = re.sub(r"<script\b[^>]*>.*?</script>|<link\b[^>]*>", "", template, flags=re.S)
    template = re.sub(r"\{%.*?%\}|\{\{.*?\}\}", "", template, flags=re.S)
    html = template + """
        <link rel="stylesheet" href="/chat.css">
        <script src="/skin-preview.js"></script><script src="/identity.js"></script><script src="/chat.js"></script>
    """
    rows = [
        {"id": 11, "agent_id": 1, "agent_rank": 0, "agent_name": "First", "skin_name": "oracle"},
        {"id": 22, "agent_id": 2, "agent_rank": 1, "agent_name": "Second", "skin_name": "oracle", "oracle_background": "scene.svg", "is_primary": True},
        {"id": 33, "agent_id": 3, "agent_rank": 2, "agent_name": "Third", "skin_name": "oracle"},
        {"id": 44, "voice_name": "Hayley", "skin_name": "hayley"},
    ]
    saved_order = [11, 22, 33, 44]
    writes = []
    loads = []
    files = {
        "/chat.css": web / "static/chat/css/chat.css",
        "/skin-preview.js": web / "static/shared/js/skin_video_preview.js",
        "/identity.js": web / "static/shared/js/assistant_identity.js",
        "/chat.js": web / "static/chat/js/chat.js",
    }
    # Use independent browser sessions and a deterministic roster. The route
    # tests cover database persistence, while this test drives the real UI.
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            page = browser.new_page(viewport={"width": 1440, "height": 900}, reduced_motion="reduce")
            page.add_init_script("""
                window.WebSocket = class { static OPEN = 1; readyState = 1; addEventListener() {} close() {} send() {} };
            """)

            def routes(route):
                path = route.request.url.split("?", 1)[0].removeprefix("http://decisions.test")
                if path == "/chat/":
                    return route.fulfill(content_type="text/html", body=html)
                if path in files:
                    return route.fulfill(path=str(files[path]))
                if path == "/api/skins/hayley/preview/idle.webm":
                    return route.fulfill(content_type="video/webm", path=str(root / "assets/avatars/hayley/idle.webm"))
                if path == "/api/skins/hayley/preview/idle.webp":
                    return route.fulfill(content_type="image/webp", path=str(root / "assets/avatars/hayley/idle.webp"))
                if path == "/api/skins/oracle/preview/scene.svg":
                    return route.fulfill(content_type="image/svg+xml", body='<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"><circle cx="32" cy="32" r="30" fill="#e88932"/><circle cx="32" cy="32" r="18" fill="none" stroke="white" stroke-width="4"/></svg>')
                if path == "/api/skins":
                    return route.fulfill(json={"selected_skin": "oracle", "skins": [
                        {"folder_name": "oracle", "name": "Oracle", "type": "oracle", "idle_animation": "fallback.svg"},
                        {"folder_name": "hayley", "name": "Hayley", "type": "avatar", "idle_animation": "idle.webm", "preview_image": "idle.webp", "chroma_key": [0, 255, 0]},
                    ]})
                if path == "/api/agent-conversations/sidebar/order":
                    saved_order[:] = route.request.post_data_json["chat_ids"]
                    writes.append(saved_order.copy())
                    return route.fulfill(json={"chat_ids": saved_order})
                if path == "/api/agent-conversations":
                    return route.fulfill(json={"chats": [
                        dict(row, sidebar_rank=saved_order.index(row["id"])) for row in rows
                    ], "last_chat_id": 44, "agent_current_chat_id": 22})
                if path.endswith("/load-in-agent"):
                    loads.append(path)
                if re.fullmatch(r"/api/agent-conversations/\d+", path):
                    record = next(row for row in rows if row["id"] == int(path.rsplit("/", 1)[1]))
                    return route.fulfill(json=dict(record, messages=[], title="Test conversation"))
                return route.fulfill(json={})

            page.route("http://decisions.test/**", routes)
            page.goto("http://decisions.test/chat/")
            page.evaluate("async () => { await loadChats(); await selectChat(44); }")
            corner = page.locator("#chatBackgroundAvatar .assistant-avatar-image")
            expect(corner).to_have_attribute("data-skin-name", "Hayley")
            background = page.locator("#chatBackgroundAvatar")
            avatar_width = background.bounding_box()["width"]
            expect(corner.locator("video")).to_have_attribute("src", "/api/skins/hayley/preview/idle.webm")
            page.wait_for_function("""() => {
                const video = document.querySelector('#chatBackgroundAvatar video');
                const canvas = document.querySelector('#chatBackgroundAvatar canvas');
                if (!video || video.currentTime <= 0 || !canvas) return false;
                const context = canvas.getContext('2d');
                return context.getImageData(0, 0, 1, 1).data[3] === 0
                    && context.getImageData(canvas.width >> 1, canvas.height >> 1, 1, 1).data[3] > 0;
            }""")
            row = lambda chat_id: page.locator(f'.chat-item[data-chat-id="{chat_id}"]')
            order = lambda: page.locator(".chat-item").evaluate_all("rows => rows.map(row => Number(row.dataset.chatId))")
            expect(row(44)).to_have_attribute("aria-selected", "true")
            with page.expect_response("**/api/agent-conversations/sidebar/order"):
                row(44).locator(".chat-thread-avatar").drag_to(row(11), target_position={"x": 90, "y": 5})
            assert writes[-1] == [44, 11, 22, 33]
            expect(row(44)).to_have_attribute("aria-selected", "true")
            expect(row(22)).to_have_class(re.compile(r"\bloaded\b"))
            page.wait_for_function("!sidebarOrderSaving")
            with page.expect_response("**/api/agent-conversations/sidebar/order"):
                row(33).drag_to(row(11), target_position={"x": 90, "y": 5})
            assert writes[-1] == [44, 33, 11, 22]
            page.reload()
            page.evaluate("async () => { await loadChats(); await selectChat(44); }")
            expect(corner).to_have_attribute("data-skin-name", "Hayley")
            assert order() == [44, 33, 11, 22]
            page.screenshot(path=str(tmp_path / "chat-hayley-reordered.png"))
            row(22).click()
            expect(corner).to_have_attribute("data-skin-name", "Oracle")
            expect(corner).to_have_attribute("src", "/api/skins/oracle/preview/scene.svg")
            oracle_box = background.bounding_box()
            oracle_width = oracle_box["width"]
            assert 44 < oracle_width <= 136
            assert oracle_width < avatar_width
            composer = page.locator("#inputContainer").bounding_box()
            assert composer["y"] - oracle_box["y"] - oracle_box["height"] == pytest.approx(24, abs=1)
            assert composer["x"] + composer["width"] - oracle_box["x"] - oracle_width == pytest.approx(32, abs=1)
            assert order() == [44, 33, 11, 22]
            assert not loads
            page.screenshot(path=str(tmp_path / "chat-oracle-reordered.png"))
        finally:
            browser.close()


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
                "http://decisions.test/api/skins/hayley/preview/idle.webm",
                lambda route: route.fulfill(status=200, content_type="video/webm", path=str(root / "assets/avatars/hayley/idle.webm")),
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
            page.add_script_tag(path=str(identity_script_path.with_name("skin_video_preview.js")))
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
            assert page.locator("#chatMessages").evaluate("node => getComputedStyle(node).backgroundImage") == "none"
            assert 0 < page.locator("#chatBackgroundAvatar").bounding_box()["width"] <= 84
            corner_avatar = page.locator("#chatBackgroundAvatar .assistant-avatar-image")
            assert corner_avatar.get_attribute("data-skin-name") == "Oracle"

            for width in (390, 1440):
                page.set_viewport_size({"width": width, "height": 900})
                page.evaluate("viewedChatSkin = 'oracle'; loadAssistantAvatarSkin(true)")
                layout_nodes = [page.locator("#chatMessages"), page.locator("#inputContainer"),
                                page.locator("#chatMessages .message.assistant").first]
                layout = [node.bounding_box() for node in layout_nodes]
                page.evaluate("viewedChatSkin = 'hayley'; loadAssistantAvatarSkin(true)")
                assert [node.bounding_box() for node in layout_nodes] == layout
                assert page.locator("#chatBackgroundAvatar").evaluate(
                    "node => getComputedStyle(node).pointerEvents"
                ) == "none"
            assert history_avatar.get_attribute('data-skin-name') == 'Hayley'
            assert corner_avatar.locator("video").get_attribute("src").endswith("/api/skins/hayley/preview/idle.webm")
            assert page.locator('#chatMessages .message.assistant .assistant-avatar-image.avatar').count() == 2
            page.evaluate('viewedChatSkin = null')

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
