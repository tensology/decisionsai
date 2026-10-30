"""A removal confirmation must stay above the native WhatsApp links dialog."""
import pytest
from playwright.sync_api import expect
from tests.ui.test_development_responsive import BASE, install_responsive_api, capture

pytestmark = pytest.mark.e2e_playwright


@pytest.mark.parametrize('width', [390, 768, 1280, 1440])
def test_unlink_confirmation_on_top_cancel_failure_and_selected_link_only(page, width):
    install_responsive_api(page)
    links = [dict(id=5, board_id=70, board_name='Decisions delivery', auto_snapshot=False),
             dict(id=8, board_id=71, board_name='Website refresh', auto_snapshot=False)]
    deletes = []
    fail = False

    def incoming(route):
        route.fulfill(json={'items': [{
            'key': 'whatsapp:test-mariam', 'source': 'whatsapp', 'source_thread_id': 'test-mariam@g.us',
            'source_message_id': 'test-message', 'conversation_label': 'Mariam (test)',
            'text': 'Synthetic project allocation test', 'sender': 'Mariam (test)',
            'created_at': '2026-10-30T10:00:00Z', 'links': links,
        }], 'links': [], 'channels': {}, 'counts': {'whatsapp': 1}})

    def remove(route):
        deletes.append(route.request.url.split('/api')[1])
        if fail:
            route.fulfill(status=503, json={'detail': 'Could not remove this project link'})
        else:
            links[:] = [link for link in links if link['id'] != 5]
            route.fulfill(json={'success': True})

    page.route('**/api/workflows/studio/incoming?*', incoming)
    page.route('**/api/workflows/studio/incoming/proposals*', lambda route: route.fulfill(json={'proposals': []}))
    page.route('**/api/tickets/boards/70/whatsapp-links/5', remove)
    page.set_viewport_size({'width': width, 'height': 900})
    page.goto(BASE+'/development/incoming/')
    page.locator('#incoming-folder-whatsapp').click()
    row = page.locator('[data-incoming-conversation="whatsapp:test-mariam@g.us"]')
    if width <= 980:
        row.locator('[data-incoming-manage]').click()
    else:
        row.click(button='right')
    parent = page.locator('#incoming-link-dialog')
    remove_button = parent.locator('[data-incoming-unlink="5"]')
    expect(parent).to_be_visible()
    remove_button.click()
    confirmation = page.locator('#decisions-confirm-modal')
    expect(confirmation).to_be_visible()
    assert confirmation.evaluate('node=>node.matches(":modal")')
    assert confirmation.locator('.decisions-confirm-ok').evaluate('node=>{const r=node.getBoundingClientRect();return node.contains(document.elementFromPoint(r.x+r.width/2,r.y+r.height/2))}')
    capture(page, 'incoming-unlink-confirmation', width)
    confirmation.get_by_role('button', name='Cancel', exact=True).click()
    expect(parent).to_be_visible()
    expect(remove_button).to_be_focused()
    assert not deletes
    remove_button.click()
    page.keyboard.press('Escape')
    expect(confirmation).to_have_count(0)
    expect(parent).to_be_visible()
    assert not deletes
    remove_button.click()
    confirmation.click(position={'x': 4, 'y': 4})
    expect(parent).to_be_visible()
    assert not deletes
    fail = True
    remove_button.click()
    confirmation.get_by_role('button', name='Unlink', exact=True).click()
    expect(parent.locator('#incoming-link-error')).to_contain_text('Could not remove this project link')
    expect(parent.locator('#incoming-link-error')).to_be_visible()
    expect(parent).to_be_visible()
    expect(remove_button).to_be_visible()
    assert len(links) == 2
    capture(page, 'incoming-unlink-error', width)
    fail = False
    remove_button.click()
    confirmation.get_by_role('button', name='Unlink', exact=True).click()
    expect(parent.locator('[data-incoming-unlink="5"]')).to_have_count(0)
    expect(parent.locator('[data-incoming-unlink="8"]')).to_be_visible()
    expect(parent).to_be_visible()
    assert deletes == ['/tickets/boards/70/whatsapp-links/5']*2
    assert len(links)==1 and links[0]['id']==8
    capture(page, 'incoming-unlink-remaining-project', width)


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
