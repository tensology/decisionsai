"""Automation list and in-place editor journeys stay on the Development surface."""
from urllib.parse import urlparse

import pytest
from playwright.sync_api import expect

from tests.ui.test_development_modules_playwright import BASE, install_plan

pytestmark = pytest.mark.e2e_playwright


@pytest.mark.parametrize("width", [390, 768, 1280, 1440])
def test_automations_responsive_journey(width, tmp_path, page):
    install_plan(page)
    page.add_init_script("""sessionStorage.setItem('decisions_automations_view', 'list');
        sessionStorage.setItem('decisions_automations_status_filter', 'all');
        window.WebSocket = class { static OPEN = 1; readyState = 1; };""")
    errors, writes = [], []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.set_viewport_size({"width": width, "height": 844 if width == 390 else 900})
    rows = [{
        "id": "review-project", "name": "Review project evidence and report any outstanding work",
        "instruction": "Review the current project and preserve the existing customer settings.",
        "status": "active", "schedule": {"kind": "daily", "time": "09:00"},
    }]

    def api(route):
        request = route.request
        path = urlparse(request.url).path
        if request.method != "GET":
            writes.append((request.method, path))
            route.fulfill(status=503, json={"detail": "Unexpected write in responsive test"})
        elif path == "/api/automations":
            route.fulfill(json={"automations": rows})
        elif path.endswith("/runs"):
            route.fulfill(json={"runs": [{"id": "run-1", "status": "completed", "started_at": "2026-10-28T09:00:00", "summary": "Reviewed the current project and recorded the outstanding work."}]})
        elif path.endswith("/presets"):
            route.fulfill(json={"presets": []})
        else:
            route.fulfill(json={})

    page.route("**/api/automations**", api)

    def screenshot(name):
        page.wait_for_timeout(180)
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        page.screenshot(path=str(tmp_path / f"{name}.png"))

    page.goto(BASE + "/development/automations/")
    item = page.locator('[data-scheduled-row="review-project"]')
    expect(item).to_be_visible()
    expect(page.locator("#scheduled-workspace")).to_be_visible()
    expect(page.locator("#automation-new")).to_be_visible()
    screenshot("list")
    item.locator(".scheduled-row-title").dblclick()
    expect(page).to_have_url(BASE + "/development/automations/?automation=review-project")
    expect(page.locator("#automation-name")).to_have_value(rows[0]["name"])
    if width < 1024:
        assert page.locator("#automation-instruction").bounding_box()["height"] >= 192
    screenshot("editor")
    page.locator("#automation-name").fill("Preserve the current draft")
    page.locator("#automation-instruction").fill("Do not lose this unsaved instruction.")
    page.locator("#automation-back").click()
    expect(item).to_be_visible()
    expect(page.locator("#scheduled-automation-editor")).to_be_hidden()
    item.locator(".scheduled-row-title").dblclick()
    expect(page.locator("#automation-name")).to_have_value("Preserve the current draft")
    expect(page.locator("#automation-instruction")).to_have_value("Do not lose this unsaved instruction.")
    screenshot("reopen-draft")
    page.get_by_role("tab", name="Run History", exact=True).click()
    expect(page.locator("#automation-runs")).to_contain_text("Reviewed the current project")
    screenshot("history")
    page.get_by_role("tab", name="Automation", exact=True).click()
    for kind in ["once", "interval", "hourly", "weekly", "monthly", "daily"]:
        page.locator("#automation-kind").select_option(kind)
        screenshot(f"schedule-{kind}")
    page.locator("#automation-back").click()
    page.locator("#automation-new").click()
    expect(page.locator("#automation-name")).to_be_focused()
    expect(page.locator("#automation-detail-title")).to_have_text("New Automation")
    expect(page.locator("#automation-name")).to_have_value("")
    page.locator("#automation-name").fill("New draft")
    page.locator("#automation-instruction").fill("Preserve this new draft too.")
    screenshot("create")
    page.locator("#automation-back").click()
    page.locator("#automation-new").click()
    expect(page.locator("#automation-name")).to_have_value("New draft")
    expect(page.locator("#automation-instruction")).to_have_value("Preserve this new draft too.")
    assert writes == []
    assert errors == []
