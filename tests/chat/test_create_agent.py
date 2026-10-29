"""Real create/read API and database behind the rendered agent dialog."""
import json
import os
from pathlib import Path
from urllib.parse import urlparse

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from jinja2 import ChoiceLoader, DictLoader, Environment, FileSystemLoader
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("width", [390, 768, 1280, 1440])
def test_create_agent_dialog_persistence_and_failure_recovery(width, tmp_path, monkeypatch):
    from distr.gui.web.routes import chat as routes
    from distr.core.db import ChatAgent, get_session
    from distr.core import signals
    from unittest.mock import Mock

    monkeypatch.setattr(signals, "signal_manager", Mock())
    monkeypatch.setattr(routes, "load_settings_from_db", lambda: {"selected_oracle": "oracle"})
    monkeypatch.setattr(routes, "validate_chat_skin", lambda skin: skin)
    app = FastAPI()
    app.include_router(routes.create_routes(ROOT / "distr/gui/web/templates"), prefix="/api")
    client = TestClient(app)
    templates = Environment(loader=ChoiceLoader([
        DictLoader({"base.html": '<html><head><meta name="viewport" content="width=device-width, initial-scale=1"></head><body>{% block content %}{% endblock %}</body></html>'}),
        FileSystemLoader(str(ROOT / "distr/gui/web/templates")),
    ]), autoescape=True)
    html = templates.get_template("chat/chat.html").render(base_path="/chat")
    evidence = Path(os.environ.get("DECISIONS_AGENT_UI_EVIDENCE", str(tmp_path)))
    evidence.mkdir(parents=True, exist_ok=True)
    posted = []
    fail_next = [True]

    def api(route):
        request = route.request
        path = urlparse(request.url).path
        if path == "/api/skins":
            route.fulfill(json={"selected_skin": "oracle", "skins": [{"folder_name": skin, "name": skin.title(), "preview_image": "idle.webp"} for skin in ("oracle", "hayley", "rusty", "clippy", "masko", "nugget", "patate")]})
            return
        if "/preview/" in path:
            route.fulfill(path=str(ROOT / "assets/avatars/hayley/idle.webp"), content_type="image/webp")
            return
        if path == "/api/chats" and request.method == "POST":
            posted.append(request.post_data_json)
            if fail_next[0]:
                fail_next[0] = False
                route.fulfill(status=503, json={"detail": "Storage unavailable. Please try again."})
                return
        if path.startswith("/api/chats"):
            response = client.request(request.method, path, content=request.post_data, headers={"content-type": "application/json"})
            route.fulfill(status=response.status_code, content_type="application/json", body=response.content)
        else:
            route.fulfill(json={})

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": width, "height": 900})
        page.route("http://decisions.test/api/**", api)
        page.set_content('<base href="http://decisions.test/">' + html)
        page.add_style_tag(content="body{margin:0;background:#101735;color:#ececf1;font-family:Arial} .hidden{display:none} .fixed{position:fixed}")
        page.add_style_tag(path=str(ROOT / "distr/gui/web/static/chat/css/chat.css"))
        page.add_script_tag(path=str(ROOT / "distr/gui/web/static/chat/js/chat.js"))
        page.evaluate("""() => {
            setupEventListeners();
            loadDefaultSettings = async () => {
                for (const [id, value] of Object.entries({llmProvider:'ollama',llmModel:'local-test',voiceProvider:'kokoro',voiceModel:'af_sky'})) {
                    document.getElementById(id).innerHTML = `<option value="${value}">${value === 'af_sky' ? '⭐ Sky' : value}</option>`;
                }
            };
            loadAssistantAvatarSkin = async () => {};
            scheduleRefreshChatHeaderStats = () => {};
            startChatWebSocket = () => {};
            saveLastChatId = async () => {};
        }""")
        plus = page.get_by_role("button", name="New agent", exact=True)
        assert plus.inner_text().strip() == ""
        plus.click()
        dialog = page.get_by_role("dialog", name="Create agent", exact=True)
        voice = page.locator('#voiceProvider')
        voice.wait_for(state="visible")
        page.evaluate('''async () => {
            window.globalVoiceWrites = 0;
            persistGlobalVoiceToSettings = async () => { globalVoiceWrites += 1; };
            const originalLoader = loadVoiceModels;
            loadVoiceModels = async () => {};
            document.getElementById('voiceProvider').dispatchEvent(new Event('change'));
            document.getElementById('voiceModel').dispatchEvent(new Event('change'));
            await Promise.resolve();
            loadVoiceModels = originalLoader;
        }''')
        assert page.evaluate('globalVoiceWrites') == 0
        assert voice.evaluate("e => e === document.activeElement")
        assert page.locator('#voiceProvider').bounding_box()['y'] < page.locator('#llmProvider').bounding_box()['y']
        name = page.get_by_label("Custom name (optional)", exact=True)
        assert not name.is_visible()
        rail = page.locator('.agent-avatar-rail')
        grid = page.locator('#modalSkinGrid')
        assert rail.is_visible()
        assert rail.bounding_box()['x'] < voice.bounding_box()['x']
        assert grid.evaluate('e => e.scrollHeight > e.clientHeight')
        form_scroll = page.locator('.agent-setup-form').evaluate('e => e.scrollTop')
        grid.locator('button').last.scroll_into_view_if_needed()
        assert grid.evaluate('e => e.scrollTop > 0')
        assert page.locator('.agent-setup-form').evaluate('e => e.scrollTop') == form_scroll
        create = page.get_by_role("button", name="Create agent", exact=True)
        page.evaluate("document.getElementById('llmModel').value = ''")
        create.click()
        assert page.get_by_role("alert").inner_text() == "Choose a model and voice before creating the agent."
        assert not posted
        page.evaluate("document.getElementById('llmModel').value = 'local-test'")
        page.screenshot(path=str(evidence / f"create-agent-{width}.png"))
        page.get_by_text("Advanced", exact=True).click()
        name.fill(f"Agent {width}")
        page.get_by_label("Agent scope (optional)", exact=True).fill("Review support tickets")
        page.locator('#modalSkinGrid [data-folder="hayley"]').click()
        assert dialog.evaluate("e => {const r=e.getBoundingClientRect();return r.left>=0 && r.top>=0 && r.right<=innerWidth && r.bottom<=innerHeight}")
        page.screenshot(path=str(evidence / f"create-agent-options-{width}.png"))
        create.click()
        page.get_by_text("Storage unavailable. Please try again.", exact=True).wait_for()
        assert name.input_value() == f"Agent {width}"
        assert create.is_enabled()
        page.screenshot(path=str(evidence / f"create-agent-error-{width}.png"))
        page.keyboard.press("Escape")
        assert not dialog.is_visible()
        assert plus.evaluate("e => e === document.activeElement")
        plus.click()
        name.wait_for(state="visible")
        assert name.input_value() == f"Agent {width}"
        page.locator('#modalSkinGrid [data-folder="hayley"]').click()
        page.evaluate("Promise.all([createNewChatWithSettings(), createNewChatWithSettings()])")
        assert len(posted) == 2
        assert "starting_question" not in posted[-1]
        assert not dialog.is_visible()
        row = page.locator(".chat-item").filter(has_text=f"Agent {width}")
        row.wait_for(state="attached")
        assert row.locator(".chat-item-title").inner_text() == f"Agent {width}"
        assert page.locator("#chatTitle").inner_text() == f"Agent {width}"
        page.screenshot(path=str(evidence / f"created-agent-{width}.png"))
        with get_session() as session:
            agent = session.query(ChatAgent).filter(ChatAgent.name == f"Agent {width}").one()
            assert agent.role == "Review support tickets"
            assert agent.skin_name == "hayley"
            main_id = agent.main_chat_id
        # Reload from persisted APIs, with a changed topic, without changing identity.
        page.evaluate("async id => { await loadChats(); await selectChat(id); }", main_id)
        assert row.locator(".chat-item-title").inner_text() == f"Agent {width}"
        # The minimal test shell has no global mobile navigation. Return its
        # sidebar to list mode before exercising another create action.
        page.evaluate("document.body.classList.remove('chat-has-active-thread')")
        plus.click(); voice.wait_for(state="visible")
        assert name.input_value() == ''
        page.evaluate("createNewChatWithSettings()")
        assert posted[-1]['agent_name'] == 'Sky'
        assert page.locator('#chatTitle').inner_text() == 'Sky'
        page.evaluate("document.body.classList.remove('chat-has-active-thread')")
        plus.click(); voice.wait_for(state="visible")
        create.focus(); page.keyboard.press("Tab")
        assert page.get_by_role("button", name="Close create agent").evaluate("e => e === document.activeElement")
        page.get_by_role("button", name="Close create agent").click()
        assert not dialog.is_visible()
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        browser.close()
