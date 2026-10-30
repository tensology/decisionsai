import json
from pathlib import Path
from urllib.parse import urlparse
import pytest
from playwright.sync_api import expect
from tests.ui.test_development_harness_playwright import _install_api, URL

pytestmark = pytest.mark.e2e_playwright


@pytest.mark.parametrize("width", [390, 768, 1280, 1440])
def test_telegram_decision_updates_project_sidebar_without_navigation(page, width):
    _install_api(page, [])
    page.add_init_script("""(() => {
        window.proposalSockets = [];
        class TestSocket {
            static OPEN = 1;
            constructor(url) {this.url=url;this.readyState=0;this.sent=[];window.proposalSockets.push(this);setTimeout(()=>{this.readyState=1;this.onopen?.()},0)}
            send(value){this.sent.push(JSON.parse(value))}
            close(){this.readyState=3}
        }
        window.WebSocket=TestSocket;
    })()""")
    created = False
    def chats(route):
        if urlparse(route.request.url).path != '/api/chats':
            route.fallback(); return
        rows = [{"id":17,"title":"Implement ticket DEV-42","project_id":7,"development":True,"development_board_key":"decisions:70","modified_date":"2026-10-30T12:00:00Z","archived":False}]
        if created:
            rows.append({"id":88,"title":"Approved from Telegram","project_id":7,"development":True,"development_board_key":"decisions:70","development_source_type":"incoming_proposal","modified_date":"2026-10-30T13:00:00Z","archived":False})
        route.fulfill(json={"last_chat_id":17,"chats":rows})
    page.route('**/api/chats*', chats)
    page.set_viewport_size({"width":width,"height":900})
    page.goto(URL)
    expect(page.locator('[data-board-group="decisions:70"] [data-chat-id="88"]')).to_have_count(0)
    page.wait_for_function("window.proposalSockets.some(s=>s.sent.some(m=>m.subscribe_topic==='incoming'))")
    created = True
    page.evaluate("""() => {
        const socket=window.proposalSockets.find(s=>s.sent.some(m=>m.subscribe_topic==='incoming'));
        socket.onmessage({data:JSON.stringify({type:'development_changed',chat_id:88})});
    }""")
    expect(page.locator('[data-board-group="decisions:70"] [data-chat-id="88"]')).to_have_count(1)
    assert '/threads/17/' in page.url
    row=page.locator('[data-board-group="decisions:70"] [data-chat-id="88"]')
    assert row.evaluate("n=>n.closest('[data-board-group]').dataset.boardGroup") == 'decisions:70'
    if width == 390: page.locator('#sidebar-open').click()
    expect(row).to_be_visible()
    folder=Path('/tmp/decisions-proposal-realtime');folder.mkdir(exist_ok=True)
    if width == 390:
        page.wait_for_function("() => getComputedStyle(document.querySelector('#studio-sidebar')).transform === 'matrix(1, 0, 0, 1, 0, 0)'")
    page.screenshot(path=str(folder/f'sidebar-{width}.png'))
    pending = True
    def proposals(route):
        route.fulfill(json={"proposals": [{"id":"tensology:7","title":"Telegram dismissal check","project_id":7,"project_name":"Decisions delivery","source":"gmail","origin":"tensology","status":"pending"}] if pending else []})
    page.route('**/api/workflows/studio/incoming/proposals*', proposals)
    page.goto('http://127.0.0.1:8765/development/incoming/')
    expect(page.get_by_text('Telegram dismissal check', exact=True)).to_be_visible()
    pending = False
    page.wait_for_function("window.proposalSockets.some(s=>s.sent.some(m=>m.subscribe_topic==='incoming'))")
    page.evaluate("""() => {
        const socket=window.proposalSockets.find(s=>s.sent.some(m=>m.subscribe_topic==='incoming'));
        socket.onmessage({data:JSON.stringify({type:'proposal_changed',request_id:7,status:'dismissed'})});
    }""")
    expect(page.get_by_text('Telegram dismissal check', exact=True)).to_have_count(0)
    page.screenshot(path=str(folder/f'dismissed-{width}.png'))
