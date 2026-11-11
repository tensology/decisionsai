"""Plan chat build part 1: chat header (switch / new / rename / clear+undo / compact), image drop and paste,
thumbnails labelled "Image <asset id>", geometry at 1440 / 768 / 390. Mocked APIs on a fixture server.

    python -c "import uvicorn; from tests.ui.development_fixture_server import app; uvicorn.run(app, port=<free>)"
    DEVELOPMENT_TEST_URL=http://127.0.0.1:<free> pytest -m e2e_playwright tests/ui/test_plan_chat_header_playwright.py
Screenshots: /tmp/planning-verify/chat1-*.png
"""
import base64
import json
from pathlib import Path

import pytest

from tests.ui.test_development_harness_playwright import _install_api
from tests.ui.test_plan_inline_edit_playwright import Backend, PLAN_URL

pytestmark = pytest.mark.e2e_playwright
OUT = Path('/tmp/planning-verify')
SIZES = [(1440, 900), (768, 1024), (390, 844)]
PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")


class ChatBackend(Backend):
    def __init__(self):
        super().__init__()
        self.chats = [{"id": 3, "title": "Plan: Merrypak", "archived": False, "revision": 0, "messages": 2}]
        self.messages = {3: [{"id": 31, "role": "user", "content": "Here is the checkout screen", "attachment_ids": [77]},
                             {"id": 31, "role": "assistant", "content": "Image 77 shows the checkout form.", "artifacts": []}]}
        self.assets = [{"id": 77, "name": "checkout.png", "mime_type": "image/png", "url": "/api/workflows/studio/plan-workspaces/5/attachments/77"}]
        self.calls = []
        self.next_id = 40

    def conversation(self, chat_id):
        live = [c for c in self.chats if not c["archived"]]
        chat = next((c for c in self.chats if c["id"] == chat_id), None) if chat_id else (live[-1] if live else None)
        return {"chat_id": chat and chat["id"], "chat": chat, "chats": self.chats, "messages": self.messages.get(chat and chat["id"], []),
                "attachments": self.assets, "provider": "ollama", "model_name": "m1", "status": "idle", "provider_routes": {}}

    def route(self, route):
        req = route.request
        url = req.url.split('/api', 1)[1]
        path, _, query = url.partition('?')
        base = '/workflows/studio/plan-workspaces/5'
        if path.startswith('/llms/available-providers'):
            return route.fulfill(json={"providers": [{"id": "ollama", "name": "Ollama"}]})
        if path.startswith('/llms/models'):
            return route.fulfill(json={"models": ["m1"]})
        if path == base + '/attachments/77' or (path.startswith(base + '/attachments/') and req.method == 'GET'):
            return route.fulfill(body=PNG, headers={"content-type": "image/png"})
        if path == base + '/attachments' and req.method == 'POST':
            self.calls.append(('upload', None))
            self.next_id += 1
            asset = {"id": self.next_id, "name": "pasted.png", "mime_type": "image/png", "url": f"/api{base}/attachments/{self.next_id}"}
            self.assets.append(asset)
            return route.fulfill(json=asset)
        if path == base + '/conversation':
            chat_id = int(query.split('chat_id=')[1]) if 'chat_id=' in query else None
            return route.fulfill(json=self.conversation(chat_id))
        if path == base + '/chats' and req.method == 'POST':
            self.next_id += 1
            self.chats.append({"id": self.next_id, "title": f"Chat {len(self.chats) + 1}", "archived": False, "revision": 0, "messages": 0})
            self.calls.append(('new', self.next_id))
            return route.fulfill(json={"chat_id": self.next_id, "chats": self.chats})
        if path.startswith(base + '/chats/'):
            parts = path[len(base + '/chats/'):].split('/')
            chat = next(c for c in self.chats if c["id"] == int(parts[0]))
            body = json.loads(req.post_data or '{}')
            if body.get("expected_revision") not in (None, chat["revision"]):
                return route.fulfill(status=409, json={"detail": "This chat changed in another window."})
            action = parts[1] if len(parts) > 1 else 'rename'
            self.calls.append((action, chat["id"]))
            chat["revision"] += 1
            if action == 'rename':
                chat["title"] = body["title"]
            elif action in ('archive', 'restore'):
                chat["archived"] = action == 'archive'
            elif action == 'clear':
                chat["archived"] = True
                self.next_id += 1
                self.chats.append({"id": self.next_id, "title": f"Chat {len(self.chats) + 1}", "archived": False, "revision": 0, "messages": 0})
                return route.fulfill(json={"archived_chat_id": chat["id"], "chat_id": self.next_id, "chats": self.chats})
            elif action == 'compact':
                for m in self.messages.get(chat["id"], []):
                    m["compacted"] = True
            return route.fulfill(json={"chat_id": chat["id"], "chats": self.chats})
        return super().route(route)


@pytest.fixture
def backend(page):
    b = ChatBackend()
    _install_api(page, [])
    page.route("**/api/**", b.route)
    OUT.mkdir(parents=True, exist_ok=True)
    return b


def box(page, selector):
    b = page.locator(selector).first.bounding_box()
    assert b is not None, selector
    return b


def _open(page, size):
    page.set_viewport_size({"width": size[0], "height": size[1]})
    page.goto(PLAN_URL)
    page.evaluate("() => { sessionStorage.clear(); localStorage.clear(); }")
    page.goto(PLAN_URL)
    page.locator('#plan-chat-switch').wait_for(state='visible', timeout=10000)
    page.locator('.plan-message-image img').first.wait_for(state='visible', timeout=5000)


@pytest.mark.parametrize("size", SIZES, ids=lambda s: f"{s[0]}")
def test_chat_header_geometry(page, backend, size):
    _open(page, size)
    header, panel = box(page, '#plan-chat-header'), box(page, '.plan-conversation')
    tap = 44 if size[0] < 640 else 30                       # mobile keeps the app's 44px tap targets
    assert header['height'] <= tap + 6 and header['width'] <= panel['width'] + 0.5
    assert abs(header['x'] - panel['x']) <= 1 and header['y'] - panel['y'] <= 1          # top of the chat panel
    for sel in ('#plan-chat-switch', '#plan-chat-new', '#plan-chat-compact', '#plan-chat-clear'):
        b = box(page, sel)
        assert b['x'] >= header['x'] - 0.5 and b['x'] + b['width'] <= header['x'] + header['width'] + 0.5, sel
        assert b['x'] + b['width'] <= size[0] and b['height'] <= tap, sel
        assert b['y'] >= header['y'] - 0.5 and b['y'] + b['height'] <= header['y'] + header['height'] + 0.5, sel
    assert page.evaluate("() => { const h = document.querySelector('#plan-chat-header'); return h.scrollWidth <= h.clientWidth + 1; }")
    assert page.locator('.plan-chat-title').inner_text() == 'Plan: Merrypak'
    thumb = box(page, '.plan-message-image img')
    assert thumb['width'] <= 97 and page.locator('.plan-message-image figcaption').inner_text() == 'Image 77'
    page.screenshot(path=str(OUT / f'chat1-header-{size[0]}.png'))
    page.locator('#plan-chat-switch').click()
    menu = box(page, '#plan-chat-menu')
    assert menu['x'] >= 0 and menu['x'] + menu['width'] <= size[0] and menu['y'] + menu['height'] <= size[1]
    page.screenshot(path=str(OUT / f'chat1-menu-{size[0]}.png'))
    page.keyboard.press('Escape')
    assert page.locator('#plan-chat-menu').is_hidden()


def test_chat_actions_new_switch_rename_clear_undo_compact(page, backend):
    _open(page, (1440, 900))
    page.locator('#plan-chat-new').click()
    page.wait_for_function("() => document.querySelector('.plan-chat-title')?.textContent === 'Chat 2'")
    assert page.evaluate("() => Object.keys(localStorage).some((k) => k.startsWith('decisions.plan.chat.'))")
    page.locator('#plan-chat-switch').click()
    page.locator('[data-chat-switch="3"]').click()
    page.wait_for_function("() => document.querySelector('.plan-chat-title')?.textContent === 'Plan: Merrypak'")
    page.locator('#plan-chat-switch').click()
    page.locator('#plan-chat-rename').click()
    page.locator('.plan-chat-rename-input').fill('Checkout flow')
    page.keyboard.press('Enter')
    page.wait_for_function("() => document.querySelector('.plan-chat-title')?.textContent === 'Checkout flow'")
    page.locator('#plan-chat-compact').click()
    page.locator('.plan-compacted-note').wait_for()
    assert page.locator('.plan-message-compacted').count() == 2
    page.locator('#plan-chat-clear').click()
    page.locator('#plan-chat-undo-clear').wait_for()
    assert page.locator('.plan-chat-title').inner_text().startswith('Chat ')
    page.screenshot(path=str(OUT / 'chat1-cleared-1440.png'))
    page.locator('#plan-chat-undo-clear').click()
    page.wait_for_function("() => document.querySelector('.plan-chat-title')?.textContent === 'Checkout flow'")
    actions = [a for a, _ in backend.calls]
    assert actions[:1] == ['new'] and 'rename' in actions and 'compact' in actions and 'clear' in actions and 'restore' in actions
    assert ('archive', backend.chats[-1]["id"]) in backend.calls                        # empty fresh chat folded away


@pytest.mark.parametrize("size", [*SIZES, (1280, 900)], ids=lambda s: f"{s[0]}")
def test_plan_composer_keeps_prompt_and_wrapped_draft_readable(page, backend, size):
    _open(page, size)
    composer = page.locator('#plan-language')
    message = composer.get_by_role('textbox', name='Message')
    send = composer.locator('#plan-send')
    composer.scroll_into_view_if_needed()
    idle_height = message.evaluate('field => field.clientHeight')
    fits = 'field => field.scrollHeight <= field.clientHeight + 1'
    assert message.evaluate(fits), 'The empty prompt should fit within the message field'
    page.screenshot(path=str(OUT / f'plan-composer-empty-{size[0]}.png'))

    draft = '\n'.join([
        'Review the checkout page and its order summary.',
        'Keep the delivery address visible.',
        'Show the next step after checkout.',
    ])
    message.fill(draft)
    assert message.evaluate(fits), 'A short multiline draft should grow the field'
    assert message.evaluate('field => field.clientHeight') > idle_height
    assert message.input_value() == draft
    send.click(trial=True)
    page.screenshot(path=str(OUT / f'plan-composer-draft-{size[0]}.png'))

    long_draft = '\n'.join([draft] * 8 + ['Final acceptance criterion.'])
    message.fill(long_draft)
    assert message.evaluate('field => field.scrollHeight > field.clientHeight')
    message.press('ControlOrMeta+End')
    assert message.input_value() == long_draft
    assert message.evaluate('field => field.scrollTop > 0')
    composer.scroll_into_view_if_needed()
    send.click(trial=True)
    page.screenshot(path=str(OUT / f'plan-composer-long-{size[0]}.png'))

    message.fill('')
    assert message.evaluate('field => field.clientHeight') == idle_height
    assert send.is_disabled()


@pytest.mark.parametrize("size", [*SIZES, (1280, 900)], ids=lambda s: f"{s[0]}")
def test_plan_send_recovers_the_saved_reply_after_response_is_lost(page, backend, size):
    requests = []

    def lost_response(route):
        body = route.request.post_data_json
        requests.append(body)
        backend.messages[3].extend([
            {"id": 41, "role": "user", "content": body["message"],
             "request_id": body.get("request_id"), "status": "complete"},
            {"id": 41, "role": "assistant", "content": "The delivery address stays visible."},
        ])
        route.abort()

    page.route("**/plan-workspaces/5/messages", lost_response)
    _open(page, size)
    message = page.get_by_role("textbox", name="Message")
    message.fill("Review the delivery address.")
    page.locator("#plan-send").click()
    reply = page.get_by_text("The delivery address stays visible.", exact=True)
    reply.wait_for()
    assert message.input_value() == ""
    assert page.locator("#plan-send").is_disabled()
    assert page.locator("#plan-operation-status .plan-error").count() == 0
    assert len(requests) == 1 and requests[0].get("request_id")
    page.locator("#plan-language").scroll_into_view_if_needed()
    page.screenshot(path=str(OUT / f"plan-send-recovered-{size[0]}.png"))


def test_plan_send_retries_confirmed_failure_with_a_new_request(page, backend):
    requests = []

    def respond(route):
        body = route.request.post_data_json
        requests.append(body)
        attempt = len(requests)
        status, reply = ("error", "Model unavailable.") if attempt == 1 else ("complete", "Plan reviewed.")
        backend.messages[3].extend([
            {"id": 40 + attempt, "role": "user", "content": body["message"],
             "request_id": body["request_id"], "status": status},
            {"id": 40 + attempt, "role": "assistant", "content": reply},
        ])
        route.fulfill(json={**backend.conversation(3), "status": status})

    page.route("**/plan-workspaces/5/messages", respond)
    _open(page, (1280, 900))
    field = page.get_by_role("textbox", name="Message")
    send = page.locator("#plan-send")
    field.fill("Review the plan.")
    send.click()
    page.get_by_text("Your draft is preserved.", exact=False).wait_for()
    assert field.input_value() == "Review the plan."
    page.screenshot(path=str(OUT / "plan-send-failure-1280.png"))
    send.click()
    page.get_by_text("Plan reviewed.", exact=True).wait_for()
    assert field.input_value() == ""
    assert requests[0]["request_id"] != requests[1]["request_id"]


def test_plan_polling_keeps_a_followup_draft_when_the_lost_request_completes(page, backend):
    requests = []
    complete = False
    read = backend.conversation

    def conversation(chat_id):
        result = read(chat_id)
        if requests:
            result["status"] = "complete" if complete else "running"
        return result

    def lose_response(route):
        body = route.request.post_data_json
        requests.append(body)
        backend.messages[3].append({"id": 41, "role": "user", "content": body["message"],
                                    "request_id": body["request_id"], "status": "running"})
        route.abort()

    backend.conversation = conversation
    page.route("**/plan-workspaces/5/messages", lose_response)
    _open(page, (1280, 900))
    field = page.get_by_role("textbox", name="Message")
    field.fill("Review the checkout.")
    page.locator("#plan-send").click()
    page.get_by_text("Review the checkout.", exact=True).wait_for()
    field.fill("Also review the order summary.")
    assert page.locator("#plan-send").is_disabled()
    complete = True
    backend.messages[3][-1]["status"] = "complete"
    backend.messages[3].append({"id": 41, "role": "assistant", "content": "Checkout reviewed."})
    page.get_by_text("Checkout reviewed.", exact=True).wait_for()
    assert field.input_value() == "Also review the order summary."
    assert page.locator("#plan-send").is_enabled()
    assert len(requests) == 1


@pytest.mark.parametrize("size", [*SIZES, (1280, 900)], ids=lambda s: f"{s[0]}")
def test_plan_reload_restores_only_the_selected_chats_draft(page, backend, size):
    requests = []

    def unexpected_send(route):
        requests.append(route.request.post_data_json)
        route.abort()

    page.route("**/plan-workspaces/5/messages", unexpected_send)
    _open(page, size)
    field = page.get_by_role("textbox", name="Message")
    field.fill("Checkout draft for Merrypak")
    page.locator("#plan-chat-new").click()
    page.wait_for_function("() => document.querySelector('.plan-chat-title')?.textContent === 'Chat 2'")
    assert field.input_value() == ""
    field.fill("A separate draft for Chat 2")
    page.reload()
    page.wait_for_function("() => document.querySelector('.plan-chat-title')?.textContent === 'Chat 2'")
    assert field.input_value() == "A separate draft for Chat 2"
    page.locator("#plan-language").scroll_into_view_if_needed()
    page.screenshot(path=str(OUT / f"plan-draft-reloaded-{size[0]}.png"))
    page.locator("#plan-chat-switch").click()
    page.locator('[data-chat-switch="3"]').click()
    page.wait_for_function("() => document.querySelector('.plan-chat-title')?.textContent === 'Plan: Merrypak'")
    assert field.input_value() == "Checkout draft for Merrypak"
    assert not requests
    assert page.evaluate("!Object.keys(localStorage).some(key => key.startsWith('decisions.plan.draft.'))")


def test_plan_tab_keeps_its_chat_selection_when_another_tab_changes_it(page, backend):
    _open(page, (1280, 900))
    field = page.get_by_role("textbox", name="Message")
    page.locator("#plan-chat-new").click()
    page.wait_for_function("() => document.querySelector('.plan-chat-title')?.textContent === 'Chat 2'")
    field.fill("Draft belongs to the first tab")

    other = page.context.new_page()
    try:
        _install_api(other, [])
        other.route("**/api/**", backend.route)
        other.goto(PLAN_URL)
        other.wait_for_function("() => document.querySelector('.plan-chat-title')?.textContent === 'Chat 2'")
        assert other.get_by_role("textbox", name="Message").input_value() == ""
        other.locator("#plan-chat-switch").click()
        other.locator('[data-chat-switch="3"]').click()
        other.wait_for_function("() => document.querySelector('.plan-chat-title')?.textContent === 'Plan: Merrypak'")
        page.reload()
        page.wait_for_function("() => document.querySelector('.plan-chat-title')?.textContent === 'Chat 2'")
        assert field.input_value() == "Draft belongs to the first tab"
    finally:
        other.close()


def _drop(page, mime, name):
    page.evaluate("""([mime, name, b64]) => {
        const bytes = Uint8Array.from(atob(b64), (c) => c.charCodeAt(0));
        const dt = new DataTransfer(); dt.items.add(new File([bytes], name, { type: mime }));
        const zone = document.querySelector('.plan-conversation');
        for (const type of ['dragenter', 'dragover', 'drop']) zone.dispatchEvent(new DragEvent(type, { dataTransfer: dt, bubbles: true, cancelable: true }));
    }""", [mime, name, base64.b64encode(PNG).decode()])


def test_image_drop_paste_and_rejection(page, backend):
    _open(page, (768, 1024))
    _drop(page, 'application/pdf', 'spec.pdf')
    page.locator('.plan-error').wait_for()
    assert 'PNG, JPEG or WebP' in page.locator('.plan-error').inner_text()
    assert not [c for c in backend.calls if c[0] == 'upload']
    _drop(page, 'image/png', 'drop.png')
    page.locator('#plan-draft-assets .context-chip-preview').first.wait_for()
    assert page.locator('#plan-draft-assets .context-chip').first.inner_text().startswith('Image 41')
    page.evaluate("""(b64) => {
        const bytes = Uint8Array.from(atob(b64), (c) => c.charCodeAt(0));
        const dt = new DataTransfer(); dt.items.add(new File([bytes], 'paste.png', { type: 'image/png' }));
        document.querySelector('#plan-language-input').dispatchEvent(new ClipboardEvent('paste', { clipboardData: dt, bubbles: true, cancelable: true }));
    }""", base64.b64encode(PNG).decode())
    page.wait_for_function("() => document.querySelectorAll('#plan-draft-assets .context-chip').length === 2")
    assert page.locator('#plan-drop-overlay').is_hidden()
    chips, composer = box(page, '#plan-draft-assets'), box(page, '#plan-language')
    assert chips['x'] >= composer['x'] - 0.5 and chips['x'] + chips['width'] <= composer['x'] + composer['width'] + 0.5
    page.screenshot(path=str(OUT / 'chat1-images-768.png'))
    assert len([c for c in backend.calls if c[0] == 'upload']) == 2


@pytest.mark.parametrize('width', [320, 390, 768, 1440])
def test_plan_section_tabs_fit_and_switch(page, backend, width):
    _open(page, (width, 900))
    nav = page.get_by_role('navigation', name='Plan sections')
    assert nav.evaluate('node => node.scrollWidth <= node.clientWidth + 1')
    for label in ('Data', 'Documents', 'Interfaces'):
        tab = nav.get_by_role('button', name=label, exact=True)
        tab.click()
        assert tab.get_attribute('aria-pressed') == 'true'
        if width < 620 and label != 'Interfaces':
            page.screenshot(path=str(OUT / f'plan-section-{label.lower()}-{width}.png'), animations='disabled')
    assert page.get_by_role('button', name='Build tickets', exact=True).is_visible()
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    page.screenshot(path=str(OUT / f'plan-section-tabs-{width}.png'), animations='disabled')
