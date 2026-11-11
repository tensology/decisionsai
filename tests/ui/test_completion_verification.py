"""Incomplete verification remains visible instead of an unqualified success."""
from pathlib import Path

import pytest
from playwright.sync_api import expect

from tests.ui.test_development_harness_playwright import _install_api, URL

pytestmark = pytest.mark.e2e_playwright


@pytest.mark.parametrize('width', [390, 768, 1280, 1440])
def test_incomplete_verification_is_not_shown_as_completed(page, width):
    page.set_viewport_size({'width': width, 'height': 1000})
    _install_api(page, [], direct_execution=True, run_status='blocked', terminal_session=False)
    execution = {'status': 'blocked', 'backend_id': 'codex', 'direct': True,
        'error': 'Required verification has not passed.',
        'changes': {'files': [{'path': 'app.js', 'line_counts_available': False, 'additions': 0, 'deletions': 0}],
                    'additions': 0, 'deletions': 0, 'reversible': False},
        'outcome': {'verification': {'status': 'failed', 'checks': [{'check': 'npm test', 'exit_code': 1}]}}}
    chat = {'id': 17, 'title': 'Review verification evidence', 'project_id': 7, 'development': {'execution': execution},
        'development_execution': execution, 'development_source_type': 'prompt', 'autonomy_level': 'full',
        'messages': [{'role': 'user', 'content': 'Fix the feature and run tests.', 'chat_row_id': 17},
            {'role': 'assistant', 'chat_row_id': 18, 'content': 'Verification incomplete: Required verification has not passed.\n\nAgent report (not independently verified):\nImplemented and verified. Everything passed.'}]}
    page.route('**/api/chats/17', lambda route: route.fulfill(json=chat))
    page.route('**/api/chats/17?*', lambda route: route.fulfill(json=chat))
    page.route('**/api/workflows/studio/tasks/17/execution', lambda route: route.fulfill(json=execution))
    page.goto(URL)
    expect(page.get_by_text('Needs input', exact=True)).to_be_visible()
    expect(page.get_by_text('Verification incomplete: Required verification has not passed.', exact=True)).to_be_visible()
    expect(page.get_by_text('Agent report (not independently verified):', exact=True)).to_be_visible()
    expect(page.locator('.turn-changes-head strong')).to_have_text('Edited 1 file')
    expect(page.locator('.turn-changes-head small')).to_have_count(0)
    expect(page.locator('.turn-change-row td').filter(has_text='n/a')).to_have_count(2)
    expect(page.locator('[data-turn-undo]')).to_be_disabled()
    assert not page.evaluate('document.documentElement.scrollWidth > innerWidth')
    folder = Path('/tmp/decisions-completion-verification')
    folder.mkdir(exist_ok=True)
    page.screenshot(path=str(folder / f'blocked-{width}.png'), full_page=True)
