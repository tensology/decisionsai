"""Plan chat build part 2: tool-call cards (Open / Undo, Apply AI version / Keep mine) and the chat memory
dialog (view, on/off, forget), geometry at 1440 / 768 / 390. Mocked APIs on a fixture server.

    DEVELOPMENT_TEST_URL=http://127.0.0.1:<free> pytest -m e2e_playwright tests/ui/test_plan_chat_tools_playwright.py
Screenshots: /tmp/planning-verify/chat2-*.png
"""
import json

import pytest

from tests.ui.test_plan_chat_header_playwright import ChatBackend, OUT, SIZES, box, _install_api, PLAN_URL

pytestmark = pytest.mark.e2e_playwright


class ToolBackend(ChatBackend):
    def __init__(self):
        super().__init__()
        self.items[0]["revision_count"] = 2
        self.messages[3][1]["tool_calls"] = [
            {"tool": "update_document", "id": 11, "item_type": "prd", "title": "Product requirements", "status": "applied", "from_revision": 1, "to_revision": 2, "warnings": []},
            {"tool": "update_erd", "id": 12, "item_type": "architecture", "title": "Architecture and ERD", "status": "conflict", "proposed_content": "erDiagram\n    A ||--o{ C : has\n"},
        ]
        self.messages[3][1]["memory_used"] = 2
        self.memory = {"enabled": False, "count": 1, "records": [{"text": "We chose Postgres. key [REDACTED:api-key]", "at": "2026-11-03T08:00:00Z"}]}

    def route(self, route):
        req = route.request
        path = req.url.split('/api', 1)[1].partition('?')[0]
        body = json.loads(req.post_data or '{}')
        if path.endswith('/plan-items/11/restore'):
            self.calls.append(('restore', body))
            self.items[0]["revision_count"] += 1
            return route.fulfill(json={"id": 11, "revision_count": self.items[0]["revision_count"]})
        if path == '/workflows/studio/plan-items/12' and req.method == 'PATCH':
            self.calls.append(('patch', body))
            self.items[1]["content"] = body["content"]
            self.items[1]["revision_count"] = self.items[1].get("revision_count", 1) + 1
            return route.fulfill(json=self.items[1])
        if path.endswith('/memory'):
            self.calls.append(('memory', req.method))
            if req.method == 'PUT':
                self.memory["enabled"] = body["enabled"]
                self.chats[0]["memory"] = body["enabled"]
            elif req.method == 'DELETE':
                self.memory = {**self.memory, "count": 0, "records": [], "removed": 1}
            return route.fulfill(json=self.memory)
        return super().route(route)


@pytest.fixture
def backend(page):
    b = ToolBackend()
    _install_api(page, [])
    page.route("**/api/**", b.route)
    OUT.mkdir(parents=True, exist_ok=True)
    return b


def _open(page, size):
    page.set_viewport_size({"width": size[0], "height": size[1]})
    page.goto(PLAN_URL)
    page.evaluate("() => { sessionStorage.clear(); localStorage.clear(); }")
    page.goto(PLAN_URL)
    page.locator('.plan-tool-card').first.wait_for(state='visible', timeout=10000)


@pytest.mark.parametrize("size", SIZES, ids=lambda s: f"{s[0]}")
def test_tool_cards_and_memory_geometry(page, backend, size):
    _open(page, size)
    log = box(page, '.plan-conversation')
    cards = page.locator('.plan-tool-card')
    assert cards.count() == 2
    assert page.locator('.plan-tool-card[data-state="undoable"]').count() == 1
    assert page.locator('.plan-tool-card[data-state="conflict"]').count() == 1
    for i in range(2):
        b = cards.nth(i).bounding_box()
        assert b['x'] >= log['x'] - 0.5 and b['x'] + b['width'] <= log['x'] + log['width'] + 0.5 and b['x'] + b['width'] <= size[0]
    for sel in ('[data-tool-undo]', '[data-tool-apply]', '[data-tool-keep]', '[data-tool-open]'):
        b = box(page, sel)
        assert b['x'] + b['width'] <= size[0] and b['height'] >= 20, sel
    assert 'Used 2 notes' in page.locator('.plan-memory-hint').inner_text()
    page.locator('.plan-tool-card').first.scroll_into_view_if_needed()
    page.screenshot(path=str(OUT / f'chat2-cards-{size[0]}.png'))
    page.locator('#plan-chat-switch').click()
    page.locator('#plan-chat-memory').click()
    dialog = box(page, '.plan-memory-dialog')
    assert dialog['x'] >= 0 and dialog['x'] + dialog['width'] <= size[0] + 0.5 and dialog['y'] + dialog['height'] <= size[1] + 0.5
    assert 'REDACTED' in page.locator('.plan-memory-list').inner_text()
    page.screenshot(path=str(OUT / f'chat2-memory-{size[0]}.png'))
    page.locator('[data-memory-close]').click()
    page.wait_for_function("() => !document.querySelector('.plan-memory-dialog')")


def test_undo_apply_keep(page, backend):
    _open(page, SIZES[0])
    page.locator('[data-tool-undo]').click()
    page.wait_for_function("() => !document.querySelector('[data-tool-undo]')")
    assert ('restore', {"revision": 1, "expected_revision": 2}) in backend.calls
    assert page.locator('.plan-tool-card').first.get_attribute('data-state') == 'changed'
    page.locator('[data-tool-keep]').click()
    assert page.locator('.plan-tool-card').count() == 1
    page.screenshot(path=str(OUT / 'chat2-after-undo-1440.png'))


def test_apply_ai_version(page, backend):
    _open(page, SIZES[1])
    page.locator('[data-tool-apply]').click()
    confirm = page.locator('.studio-confirm-dialog button, dialog button').filter(has_text='Apply AI version')
    if confirm.count():
        confirm.first.click()
    page.wait_for_function("() => document.querySelectorAll('.plan-tool-card').length === 1")
    patch = next(b for k, b in backend.calls if k == 'patch')
    assert patch["content"].endswith("C : has\n") and patch["expected_revision"] is not None


def test_memory_toggle_and_forget(page, backend):
    _open(page, SIZES[2])
    page.locator('#plan-chat-switch').click()
    page.locator('#plan-chat-memory').click()
    page.locator('#plan-memory-enabled').check()
    page.wait_for_function("() => document.querySelector('#plan-memory-enabled')?.checked")
    assert ('memory', 'PUT') in backend.calls
    page.locator('[data-memory-forget="chat"]').click()
    btn = page.locator('dialog button').filter(has_text='Forget').last
    if btn.count() and btn.is_visible():
        btn.click()
    page.wait_for_function("() => document.querySelector('.plan-memory-empty')")
    assert ('memory', 'DELETE') in backend.calls
    page.screenshot(path=str(OUT / 'chat2-memory-forgot-390.png'))
