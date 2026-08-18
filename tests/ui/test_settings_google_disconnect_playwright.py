"""Browser coverage for the Google disconnect flow on Third Party settings."""

import json

import pytest
from playwright.sync_api import Page, Route, expect


pytestmark = pytest.mark.e2e_playwright
BASE_URL = "http://127.0.0.1:8765"


def test_google_disconnect_confirms_executes_and_refreshes_ui(page: Page):
    state = {"connected": True, "disconnect_calls": 0}

    def route_connection_status(route: Route):
        route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps({"google_connected": state["connected"]}),
        )

    def route_disconnect(route: Route):
        state["disconnect_calls"] += 1
        state["connected"] = False
        route.fulfill(status=200, content_type="application/json", body='{"success": true}')

    page.route("**/api/advanced/connection-status", route_connection_status)
    page.route("**/api/advanced/google/disconnect", route_disconnect)
    page.goto(f"{BASE_URL}/settings#thirdparty", wait_until="domcontentloaded")

    page.locator("[data-thirdparty-subtab=connect]").click()
    page.get_by_role("button", name="Edit Google", exact=True).click()

    expect(page.get_by_text("Google is connected.", exact=False)).to_be_visible()
    page.get_by_role("button", name="Disconnect", exact=True).click()
    expect(page.get_by_role("dialog", name="Disconnect Google")).to_be_visible()
    page.get_by_role("dialog", name="Disconnect Google").get_by_role(
        "button", name="Disconnect", exact=True
    ).click()

    expect(page.get_by_text("Google is not connected.", exact=False)).to_be_visible()
    expect(page.get_by_role("button", name="Connect", exact=True)).to_be_visible()
    expect(page.locator("#thirdparty_connect_google_disconnect")).to_have_count(0)
    assert state["disconnect_calls"] == 1
