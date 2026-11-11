"""Plan chat draft/attachment/route isolation using fully mocked application APIs."""
from pathlib import Path

import pytest
from playwright.sync_api import expect
from tests.ui.test_development_harness_playwright import _install_api
from tests.ui.test_plan_chat_header_playwright import ChatBackend, PNG
from tests.ui.test_plan_inline_edit_playwright import PLAN_URL

pytestmark = pytest.mark.e2e_playwright
OUT = Path('/tmp/decisions-plan-chat-continuity')


class ContinuityBackend(ChatBackend):
    def conversation(self, chat_id):
        value = super().conversation(chat_id)
        if value['chat_id'] != 3:
            value.update(provider='openrouter', model_name='m2')
        return value

    def route(self, route):
        path = route.request.url.split('/api', 1)[1]
        if path.startswith('/llms/available-providers'):
            return route.fulfill(json={'providers': [{'id': 'ollama', 'name': 'Ollama'}, {'id': 'openrouter', 'name': 'OpenRouter'}]})
        if path.startswith('/llms/models'):
            return route.fulfill(json={'models': ['m1', 'm2']})
        return super().route(route)


def shot(page, width, state):
    OUT.mkdir(parents=True, exist_ok=True)
    assert not page.evaluate('document.documentElement.scrollWidth > innerWidth')
    page.screenshot(path=str(OUT / f'{width}-{state}.png'), full_page=True)


def switch(page, chat_id):
    page.locator('#plan-chat-switch').click()
    page.locator(f'[data-chat-switch="{chat_id}"]').click()
    expect(page.locator('#plan-chat-switch')).to_be_enabled()


def attach(page, name):
    page.locator('#plan-attachment').set_input_files({'name': name, 'mimeType': 'image/png', 'buffer': PNG})
    expect(page.locator('#plan-draft-assets .context-chip')).to_have_count(1)


@pytest.mark.parametrize('width', [390, 768, 1280, 1440])
def test_reopened_plan_chat_shows_artifact_issue_and_repair_timeout(page, width):
    page.set_viewport_size({'width': width, 'height': 1000})
    backend = ContinuityBackend()
    backend.messages[3].extend([
        {'id': 32, 'role': 'user', 'content': 'Create the customer entry screen.'},
        {'id': 32, 'role': 'assistant', 'content': (
            'The original proposal failed validation. Customers has unresolved bind=Customer.name.\n\n'
            'The automatic repair could not finish. The selected model timed out. '
            'No artifact changes were applied. Retry or select another model.'
        )},
    ])
    _install_api(page, [])
    page.route('**/api/**', backend.route)
    page.goto(PLAN_URL)
    issue = page.get_by_text('The original proposal failed validation.', exact=False)
    retry = page.get_by_text('The automatic repair could not finish.', exact=False)
    expect(issue).to_be_visible()
    expect(retry).to_be_visible()
    expect(page.locator('#plan-language-input')).to_be_editable()
    if width == 390:
        issue.evaluate("element => element.scrollIntoView({block: 'start'})")
        assert issue.evaluate("element => element.getBoundingClientRect().top >= element.closest('#plan-messages').getBoundingClientRect().top - 1")
        shot(page, width, 'repair-validation')
        retry.evaluate("element => element.scrollIntoView({block: 'end'})")
        assert retry.evaluate("element => element.getBoundingClientRect().bottom <= element.closest('#plan-messages').getBoundingClientRect().bottom + 1")
    else:
        expect(issue).to_be_in_viewport(ratio=1)
    shot(page, width, 'repair-timeout')


@pytest.mark.parametrize('width', [390, 768, 1280, 1440])
def test_drafts_images_routes_survive_switch_new_clear_and_undo_without_leaking(page, width):
    page.set_viewport_size({'width': width, 'height': 1000})
    backend = ContinuityBackend()
    _install_api(page, [])
    page.route('**/api/**', backend.route)
    page.goto(PLAN_URL)
    expect(page.locator('#plan-chat-switch')).to_be_visible()
    message = page.locator('#plan-language-input')
    message.fill('Chat A: keep the payment rules unchanged.')
    attach(page, 'chat-a.png')
    a_asset = page.locator('#plan-draft-assets [data-remove-asset]').get_attribute('data-remove-asset')
    page.locator('#plan-model').select_option('m2')
    page.locator('#plan-chat-new').click()
    expect(page.get_by_text('New chat started.', exact=True)).to_be_visible()
    b_id = backend.next_id
    expect(message).to_have_value('')
    expect(page.locator('#plan-draft-assets .context-chip')).to_have_count(0)
    expect(page.locator('#plan-provider')).to_have_value('openrouter')
    expect(page.locator('#plan-model')).to_have_value('m2')
    assert page.evaluate("() => { const e = new Event('beforeunload', {cancelable:true}); window.dispatchEvent(e); return e.defaultPrevented; }")
    shot(page, width, 'new-chat-isolated')
    message.fill('Chat B: add a customer export preview.')
    attach(page, 'chat-b.png')
    b_asset = page.locator('#plan-draft-assets [data-remove-asset]').get_attribute('data-remove-asset')
    page.locator('#plan-model').select_option('m1')
    switch(page, 3)
    expect(message).to_have_value('Chat A: keep the payment rules unchanged.')
    expect(page.locator('#plan-draft-assets [data-remove-asset]')).to_have_attribute('data-remove-asset', a_asset)
    expect(page.locator('#plan-provider')).to_have_value('ollama')
    expect(page.locator('#plan-model')).to_have_value('m2')
    shot(page, width, 'chat-a-restored')
    switch(page, b_id)
    expect(message).to_have_value('Chat B: add a customer export preview.')
    expect(page.locator('#plan-draft-assets [data-remove-asset]')).to_have_attribute('data-remove-asset', b_asset)
    expect(page.locator('#plan-provider')).to_have_value('openrouter')
    expect(page.locator('#plan-model')).to_have_value('m1')
    shot(page, width, 'chat-b-restored')
    page.locator('#plan-chat-clear').click()
    expect(page.locator('#plan-chat-undo-clear')).to_be_visible()
    expect(message).to_have_value('')
    expect(page.locator('#plan-draft-assets .context-chip')).to_have_count(0)
    shot(page, width, 'clear-fresh-chat')
    page.locator('#plan-chat-undo-clear').click()
    expect(message).to_have_value('Chat B: add a customer export preview.')
    expect(page.locator('#plan-draft-assets [data-remove-asset]')).to_have_attribute('data-remove-asset', b_asset)
    expect(page.locator('#plan-model')).to_have_value('m1')
    shot(page, width, 'undo-restored-chat')
