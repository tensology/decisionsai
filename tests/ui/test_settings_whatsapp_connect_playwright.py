"""Browser coverage for starting and completing WhatsApp pairing."""

import json

import pytest
from playwright.sync_api import Page, Route, expect


pytestmark = pytest.mark.e2e_playwright
BASE_URL = "http://127.0.0.1:8765"


def test_whatsapp_refresh_starts_pairing_then_shows_connected(page: Page):
    state = {"connect_calls": 0, "status_calls": 0, "save_calls": 0}

    def route_connection_status(route: Route):
        route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps({"whatsapp_connected": state["save_calls"] > 0}),
        )

    def route_connect(route: Route):
        state["connect_calls"] += 1
        route.fulfill(
            status=202,
            content_type="application/json",
            body=json.dumps({"status": "connecting", "qr_code": None}),
        )

    def route_status(route: Route):
        state["status_calls"] += 1
        if state["status_calls"] <= 2:
            payload = {"status": "qr_ready", "qr_code": "test-pairing-code"}
        else:
            payload = {
                "status": "connected",
                "phone": {"jid": "27820000000@s.whatsapp.net", "name": "Paul"},
            }
        route.fulfill(status=200, content_type="application/json", body=json.dumps(payload))

    def route_save(route: Route):
        state["save_calls"] += 1
        route.fulfill(status=200, content_type="application/json", body='{"success": true}')

    page.route("**/api/advanced/connection-status", route_connection_status)
    page.route("**/api/advanced/whatsapp/connect", route_connect)
    page.route("**/api/advanced/whatsapp/status", route_status)
    page.route("**/api/advanced/whatsapp/save", route_save)
    page.goto(f"{BASE_URL}/settings#thirdparty", wait_until="domcontentloaded")

    page.locator("[data-thirdparty-subtab=connect]").click()
    page.get_by_role("button", name="Edit WhatsApp", exact=True).click()

    expect(page.get_by_text("test-pairing-code", exact=True)).to_be_visible()
    expect(page.get_by_text("WhatsApp connected successfully.", exact=True)).to_be_visible(timeout=7_000)
    expect(page.get_by_text("Paul", exact=True)).to_be_visible()
    assert state["connect_calls"] == 1
    assert state["status_calls"] >= 2
    assert state["save_calls"] == 1
