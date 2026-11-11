"""WhatsApp removes links directly and the shared confirmation retains focus."""
import pytest
from playwright.sync_api import expect
from tests.ui.test_development_responsive import BASE, install_responsive_api, capture

pytestmark = pytest.mark.e2e_playwright


@pytest.mark.parametrize('width', [390, 768, 1280, 1440])
def test_unlink_updates_without_refresh_and_closes_after_last_link(page, width):
    install_responsive_api(page)
    thread = '12025550143@s.whatsapp.net'
    links = [dict(id=5, board_id=70, board_name='Decisions delivery', auto_snapshot=False),
             dict(id=8, board_id=71, board_name='Website refresh', auto_snapshot=False)]
    deletes = []
    refreshes = []
    fail = False

    def incoming(route):
        refreshes.append(route.request.url)
        route.fulfill(json={'items': [{
            'key': 'whatsapp:test-contact', 'source': 'whatsapp', 'source_thread_id': thread,
            'source_message_id': 'test-message', 'conversation_label': 'Example contact', 'sender': 'Example contact',
            'text': 'Synthetic project allocation test',
            'created_at': '2026-10-30T10:00:00Z', 'links': links,
        }], 'links': [{**link, 'source': 'whatsapp', 'source_thread_id': thread} for link in links],
            'channels': {}, 'counts': {'whatsapp': 1}})

    def remove(route):
        assert route.request.method == 'DELETE'
        path = route.request.url.split('/api')[1]
        deletes.append(path)
        if fail:
            route.fulfill(status=503, json={'detail': 'Could not remove this project link'})
        else:
            link_id = int(path.rsplit('/', 1)[1])
            links[:] = [link for link in links if link['id'] != link_id]
            route.fulfill(json={'success': True})

    page.route('**/api/workflows/studio/incoming?*', incoming)
    page.route('**/api/workflows/studio/incoming/proposals*', lambda route: route.fulfill(json={'proposals': []}))
    page.route('**/api/tickets/boards/70/whatsapp-links/5', remove)
    page.route('**/api/tickets/boards/71/whatsapp-links/8', remove)
    page.set_viewport_size({'width': width, 'height': 900})
    page.goto(BASE+'/development/incoming/')
    page.locator('#incoming-folder-whatsapp').click()
    row = page.locator(f'[data-incoming-conversation="whatsapp:{thread}"]')
    if width <= 980:
        row.locator('[data-incoming-manage]').click()
    else:
        row.click(button='right')
    parent = page.locator('#incoming-link-dialog')
    remove_button = parent.locator('[data-incoming-unlink="5"]')
    expect(parent).to_be_visible()
    fail = True
    remove_button.click()
    expect(parent.locator('#incoming-link-error')).to_contain_text('Could not remove this project link')
    expect(parent).to_be_visible()
    expect(remove_button).to_be_focused()
    assert len(links) == 2
    capture(page, 'incoming-unlink-error', width)
    fail = False
    refresh_count = len(refreshes)
    remove_button.click()
    expect(remove_button).to_have_count(0)
    remaining = parent.locator('[data-incoming-unlink="8"]')
    expect(remaining).to_be_focused()
    expect(parent).to_be_visible()
    expect(row).not_to_contain_text('Decisions delivery')
    assert deletes == ['/tickets/boards/70/whatsapp-links/5'] * 2
    capture(page, 'incoming-unlink-remaining-project', width)
    remaining.click()
    expect(parent).not_to_be_visible()
    expect(page.locator('#decisions-confirm-modal')).to_have_count(0)
    expect(row).to_be_focused()
    expect(row.locator('.incoming-board-pill')).to_have_count(0)
    expect(row).to_contain_text('Synthetic project allocation test')
    expect(page.locator('.incoming-channel-section.other')).to_contain_text('Example contact')
    assert deletes[-1] == '/tickets/boards/71/whatsapp-links/8' and not links
    assert len(refreshes) == refresh_count
    capture(page, 'incoming-unlink-last-project', width)


def test_shared_confirmation_plain_page_checkbox_keyboard_and_focus(page):
    install_responsive_api(page)
    page.goto(BASE+'/development/incoming/')
    trigger=page.locator('#incoming-folder-whatsapp')
    trigger.focus()
    page.evaluate("""() => {
        window.confirmResult=null;
        window.DecisionsAPI.confirm({title:'Shared confirmation check',message:'Synthetic confirmation only.',
            checkbox:{label:'Include related item',checked:false}}).then(result=>window.confirmResult=result);
    }""")
    modal=page.locator('#decisions-confirm-modal')
    expect(modal.locator('.decisions-confirm-ok')).to_be_focused()
    page.keyboard.press('Tab')
    assert page.evaluate('document.activeElement.closest("#decisions-confirm-modal")!==null')
    modal.locator('.decisions-confirm-checkbox').check()
    page.keyboard.press('Enter')
    expect(modal).to_have_count(0)
    assert page.evaluate('window.confirmResult')=={'confirmed':True,'checked':True}
    expect(trigger).to_be_focused()
    page.evaluate("""() => {
        window.confirmResult=null;
        window.DecisionsAPI.confirm({title:'Cancel check',message:'Synthetic confirmation only.'})
          .then(result=>window.confirmResult=result);
    }""")
    page.keyboard.press('Escape')
    assert page.evaluate('window.confirmResult') is False
    expect(trigger).to_be_focused()
