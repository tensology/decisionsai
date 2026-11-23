"""Playwright: viewing another thread does not move the star; Load does.

Uses a stubbed fixture page and a temporary Chromium profile. Never talks to
the live Decisions app and never sends a real chat message.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

pytest.importorskip("playwright.sync_api")

pytestmark = pytest.mark.integration

ROOT = Path(__file__).resolve().parents[2]
SHOT_DIR = ROOT / "tmp" / "chat-star-loaded-playwright"


def _template_html() -> str:
    raw = (ROOT / "distr/gui/web/templates/chat/chat.html").read_text(encoding="utf-8")
    # Drop Jinja and any script/link tags so the fixture cannot load the live app.
    raw = re.sub(r"\{%.*?%\}", "", raw, flags=re.S)
    raw = re.sub(r"\{\{.*?\}\}", "", raw)
    raw = re.sub(r"<script\b[^>]*>.*?</script>", "", raw, flags=re.S | re.I)
    raw = re.sub(r"<link\b[^>]*>", "", raw, flags=re.I)
    return raw


def test_star_stays_on_live_agent_until_load(tmp_path):
    from playwright.sync_api import Error as PlaywrightError
    from playwright.sync_api import TimeoutError as PlaywrightTimeout
    from playwright.sync_api import sync_playwright

    SHOT_DIR.mkdir(parents=True, exist_ok=True)
    style_path = ROOT / "distr/gui/web/static/chat/css/chat.css"
    script_path = ROOT / "distr/gui/web/static/chat/js/chat.js"

    try:
        playwright = sync_playwright().start()
        browser = playwright.chromium.launch(headless=True)
    except Exception as exc:
        pytest.skip(f"Playwright/Chromium unavailable: {exc}")

    try:
        context = browser.new_context(
            viewport={"width": 1280, "height": 900},
            ignore_https_errors=True,
        )
        page = context.new_page()
        page.set_default_timeout(8000)
        page.set_default_navigation_timeout(8000)
        page.set_content(_template_html(), wait_until="domcontentloaded")
        page.add_style_tag(content="*,*::before,*::after{box-sizing:border-box}")
        page.add_style_tag(path=str(style_path))
        page.evaluate(
            """() => {
                window.WebSocket = function FakeSocket() {
                    this.readyState = 1;
                    this.send = function() {};
                    this.close = function() {};
                    this.addEventListener = function() {};
                };
                window.WebSocket.OPEN = 1;
                window.EventSource = function FakeES() {
                    this.close = function() {};
                    this.addEventListener = function() {};
                };
                window.fetch = async (url, opts = {}) => {
                    const href = String(url);
                    const method = (opts.method || 'GET').toUpperCase();
                    const json = (body) => new Response(JSON.stringify(body), {
                        status: 200, headers: {'Content-Type': 'application/json'}
                    });
                    if (href.includes('/api/skins')) {
                        return json({ selected_skin: 'oracle', skins: [] });
                    }
                    if (href.includes('/api/agents/primary')) {
                        const live = window.__liveId || 11;
                        return json({ primary_agent: { agent_id: live === 11 ? 1 : 2, name: live === 11 ? 'Ada' : 'Ben', main_conversation_id: live } });
                    }
                    if (href.includes('/api/agent-conversations') && method === 'GET' && href.split('?')[0].replace(/\\/$/, '').endsWith('/api/agent-conversations')) {
                        return json({
                            chats: [
                                { id: 11, title: 'Ada thread', agent_id: 1, agent_name: 'Ada', is_primary: (window.__liveId || 11) === 11, agent_rank: 0, latest_message: 'Hello from Ada', unread: false, provider: 'ollama', model_name: 'kimi' },
                                { id: 22, title: 'Ben thread', agent_id: 2, agent_name: 'Ben', is_primary: (window.__liveId || 11) === 22, agent_rank: 1, latest_message: 'Hello from Ben', unread: false, provider: 'openai', model_name: 'gpt-5.2' }
                            ],
                            last_chat_id: window.__viewId || 11,
                            agent_current_chat_id: window.__liveId || 11
                        });
                    }
                    if (href.includes('/api/agent-conversations/11') && method === 'GET') {
                        return json({ id: 11, title: 'Ada thread', agent_name: 'Ada', provider: 'ollama', model_name: 'kimi', messages: [], voice_provider: 'kokoro', voice_model: 'af_heart' });
                    }
                    if (href.includes('/api/agent-conversations/22') && method === 'GET') {
                        return json({ id: 22, title: 'Ben thread', agent_name: 'Ben', provider: 'openai', model_name: 'gpt-5.2', messages: [], voice_provider: 'kokoro', voice_model: 'af_heart' });
                    }
                    if (href.includes('/agents/2/primary') && method === 'POST') {
                        window.__liveId = 22;
                        return json({ primary_agent: { agent_id: 2, name: 'Ben', main_conversation_id: 22 } });
                    }
                    if (href.includes('/agent-conversations/22/load-in-agent') && method === 'POST') {
                        window.__liveId = 22;
                        return json({ ok: true });
                    }
                    if (href.includes('/last-agent-conversation') && method === 'PUT') {
                        return json({ success: true });
                    }
                    return json({});
                };
                window.__liveId = 11;
                window.__viewId = 11;
            }"""
        )
        page.add_script_tag(path=str(script_path))
        page.evaluate(
            """async () => {
                const data = await loadChats();
                loadedChatId = 11;
                agentCurrentChatId = 11;
                currentChatId = 11;
                updateActiveChat();
                const empty = document.getElementById('emptyState');
                if (empty) empty.style.display = 'none';
                document.body.classList.add('chat-has-active-thread');
            }"""
        )
        page.locator('.chat-item[data-chat-id="11"]').wait_for(state="attached")
        page.locator('.chat-item[data-chat-id="22"]').wait_for(state="attached")

        # View Ben without loading him. Single-click is view-only.
        page.evaluate("() => { window.__viewId = 22; }")
        page.locator('.chat-item[data-chat-id="22"]').click(force=True)
        page.wait_for_timeout(400)
        page.screenshot(path=str(SHOT_DIR / "01-view-ben-star-stays-on-ada.png"))
        (tmp_path / "01-view-ben-star-stays-on-ada.png").write_bytes(
            (SHOT_DIR / "01-view-ben-star-stays-on-ada.png").read_bytes()
        )
        assert page.locator('.chat-item[data-chat-id="22"]').evaluate("el => el.classList.contains('active')")
        star_ada = page.locator('.chat-item[data-chat-id="11"] .agent-star')
        star_ben = page.locator('.chat-item[data-chat-id="22"] .agent-star')
        assert star_ada.inner_text() == "★"
        assert star_ben.inner_text() == "☆"
        loaded = page.evaluate("() => ({ loaded: loadedChatId, live: agentCurrentChatId, viewed: currentChatId })")
        assert loaded == {"loaded": 11, "live": 11, "viewed": 22}

        # Load Ben — star and send target must move together.
        page.evaluate("() => loadAgentChat(22)")
        page.wait_for_timeout(400)
        page.screenshot(path=str(SHOT_DIR / "02-load-ben-star-moves.png"))
        (tmp_path / "02-load-ben-star-moves.png").write_bytes(
            (SHOT_DIR / "02-load-ben-star-moves.png").read_bytes()
        )
        assert star_ben.inner_text() == "★"
        assert star_ada.inner_text() == "☆"
        after = page.evaluate("() => ({ loaded: loadedChatId, live: agentCurrentChatId, viewed: currentChatId })")
        assert after["loaded"] == 22
        assert after["live"] == 22
        assert after["viewed"] == 22
    except (PlaywrightError, PlaywrightTimeout) as exc:
        # Launch already succeeded; a fixture/selector problem is a real failure
        # unless Chromium itself died mid-run.
        msg = str(exc).lower()
        if "browser has been closed" in msg or "chromium" in msg and "launch" in msg:
            pytest.skip(f"Playwright/Chromium unavailable: {exc}")
        raise
    finally:
        try:
            browser.close()
        except Exception:
            pass
        try:
            playwright.stop()
        except Exception:
            pass
