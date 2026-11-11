"""Exercise Incoming WhatsApp history, linking and replies without sending live messages."""

from datetime import datetime, timezone
from urllib.parse import parse_qs, urlparse

import pytest
from playwright.sync_api import expect

from tests.ui.test_development_harness_playwright import _install_api
from tests.ui.test_incoming_dismiss_undo import fixture_base

pytestmark = pytest.mark.e2e_playwright


@pytest.mark.parametrize("width", [390, 1440])
def test_whatsapp_reader_history_linking_and_text_replies(page, fixture_base, tmp_path, width):
    _install_api(page, [])
    group = "menu@g.us"
    contact = "12025550143@s.whatsapp.net"
    sent = []
    links = []
    history = []
    fail_send = False

    def item(jid, number, text, *, outgoing=False):
        return {
            "key": f"whatsapp:{number}", "database_id": number, "source": "whatsapp",
            "source_thread_id": jid, "source_message_id": f"message-{number}", "text": text,
            "conversation_label": "Menu team" if jid == group else "Andy (test)",
            "chat_type": "group" if jid == group else "private", "sender": "Test sender",
            "created_at": datetime.now(timezone.utc).isoformat() if outgoing else f"2026-11-10T09:00:{number:02d}Z",
            "from_me": outgoing, "processed": outgoing,
        }

    def get_history(route):
        params = parse_qs(urlparse(route.request.url).query)
        jid = params["thread_id"][0]
        before = params["before_id"][0]
        history.append((jid, before))
        if before == "11":
            route.fulfill(json={"items": [item(group, 10, "Older group history")], "has_older": False})
        else:
            route.fulfill(json={"items": [
                item(jid, 30 + i, text, outgoing=True) for i, (destination, text) in enumerate(sent) if destination == jid
            ]})

    def send(route):
        payload = route.request.post_data_json
        if fail_send:
            return route.fulfill(status=503, json={"error": "WhatsApp relay unavailable."})
        sent.append((payload["jid"], payload["text"]))
        route.fulfill(json={"success": True})

    def link(route):
        links.append(route.request.post_data_json["phone_jid"])
        route.fulfill(json={"success": True, "id": 45, "created": True})

    page.route("**/api/workflows/studio/incoming?*", lambda route: route.fulfill(json={
        "items": [
            {**item(group, 11, "Latest group message"), "links": [{"id": 5, "board_id": 70, "board_name": "Decisions delivery"}]},
            item(contact, 12, "Latest private message"),
            {"key": "intake:99", "source": "gmail", "source_thread_id": "gmail-error", "source_message_id": "gmail-error",
             "subject": "Merrypak website error", "conversation_label": "Merrypak website error",
             "sender": "Website alerts", "ticket_id": 306, "board_id": 70, "text": "An error report",
             "created_at": "2026-11-10T09:00:00Z"},
        ],
        "links": [],
    }))
    page.route("**/api/workflows/studio/incoming/whatsapp/history?*", get_history)
    page.route("**/api/tickets/whatsapp/send", send)
    page.route("**/api/tickets/boards/70/whatsapp-links", link)
    page.set_viewport_size({"width": width, "height": 850})
    page.goto(f"{fixture_base}/development/incoming/")
    page.locator("#incoming-folder-whatsapp").click()
    group_row = page.locator(f'[data-incoming-conversation="whatsapp:{group}"]')
    contact_row = page.locator(f'[data-incoming-conversation="whatsapp:{contact}"]')
    reader = page.locator("#incoming-whatsapp-reader")
    composer = reader.get_by_role("textbox", name="WhatsApp message")
    send_button = reader.get_by_role("button", name="Send WhatsApp message")
    back = page.locator("#incoming-panel-whatsapp").get_by_role("button", name="Back to conversations")
    group_row.click()
    expect(send_button).to_be_disabled()
    reader.get_by_role("button", name="Load older messages").click()
    expect(reader.get_by_text("Older group history")).to_be_visible()
    expect(reader.get_by_role("button", name="Load older messages")).to_have_count(0)
    assert history[0] == (group, "11")
    composer.fill("Group reply")
    composer.press("Shift+Enter")
    composer.type("Second line")
    page.screenshot(path=tmp_path / "group-draft-and-history.png")
    composer.press("Enter")
    expect(reader.locator(".incoming-message-bubble.outgoing").get_by_text("Group reply Second line", exact=True)).to_be_visible()
    expect(composer).to_have_value("")
    assert sent == [(group, "Group reply\nSecond line")]
    page.screenshot(path=tmp_path / "group-reply.png")

    if width == 390:
        back.click()
    contact_row.locator("[data-incoming-manage]").click()
    dialog = page.locator("#incoming-link-dialog")
    expect(dialog.get_by_role("heading")).to_have_text("Andy (test)")
    page.screenshot(path=tmp_path / "link-dialog.png")
    dialog.locator("select").select_option("70")
    dialog.get_by_role("button", name="Link board").click()
    expect(dialog).not_to_be_visible()
    expect(contact_row).to_contain_text("Decisions delivery")
    assert links == [contact]
    page.screenshot(path=tmp_path / "linked-contact.png")

    contact_row.click()
    composer.fill("Private reply")
    fail_send = True
    send_button.click()
    expect(reader.get_by_text("WhatsApp relay unavailable.")).to_be_visible()
    expect(composer).to_have_value("Private reply")
    page.screenshot(path=tmp_path / "contact-retry.png")
    fail_send = False
    send_button.click()
    expect(reader.locator(".incoming-message-bubble.outgoing").get_by_text("Private reply", exact=True)).to_be_visible()
    assert sent[-1] == (contact, "Private reply")
    page.screenshot(path=tmp_path / "contact-reply.png")

    page.locator("#incoming-folder-gmail").click()
    mail = page.get_by_role("button", name="Merrypak website error", exact=False)
    expect(mail).to_be_visible()
    page.screenshot(path=tmp_path / "gmail-list.png")
    if width == 390:
        mail.click()
    expect(page.get_by_role("button", name="Open ticket")).to_be_visible()
    page.screenshot(path=tmp_path / "gmail-ticket.png")
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
