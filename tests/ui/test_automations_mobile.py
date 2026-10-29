"""Automation list, draft and calendar journeys with all API calls intercepted."""

from urllib.parse import urlparse

import pytest
from playwright.sync_api import expect, sync_playwright


@pytest.mark.parametrize("width", [390, 768, 1280, 1440])
def test_automations_responsive_journey(width, tmp_path):
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport={"width": width, "height": 844 if width == 390 else 900})
        errors, writes = [], []
        page.on("pageerror", lambda error: errors.append(str(error)))
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
            elif path == "/api/schedule-blocks":
                route.fulfill(json={"blocks": []})
            elif path.endswith("/export-boards"):
                route.fulfill(json={"boards": []})
            elif path == "/api/tickets/boards":
                route.fulfill(json=[])
            else:
                route.fulfill(json={})

        page.route("**/api/**", api)

        def screenshot(name):
            page.wait_for_timeout(180)
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            assert page.evaluate("document.documentElement.scrollHeight <= innerHeight + 1")
            page.screenshot(path=str(tmp_path / f"{name}.png"))
            detail = page.locator("#automation-detail")
            if detail.is_visible() and detail.evaluate("node => node.scrollHeight > node.clientHeight + 1"):
                detail.evaluate("node => { node.scrollTop = node.scrollHeight; }")
                page.screenshot(path=str(tmp_path / f"{name}-bottom.png"))
                detail.evaluate("node => { node.scrollTop = 0; }")

        page.goto("http://127.0.0.1:8765/automations/")
        item = page.locator(".automation-item").first
        expect(item).to_be_visible()
        if width < 1024:
            expect(page.locator("#automations-editor")).to_be_hidden()
        screenshot("list")
        item.click()
        expect(page.locator("#automation-name")).to_have_value(rows[0]["name"])
        if width < 1024:
            expect(page.locator("#automation-back")).to_be_focused()
            assert page.locator("#automation-instruction").bounding_box()["height"] >= 192
        screenshot("editor")
        page.locator("#automation-name").fill("Preserve the current draft")
        page.locator("#automation-instruction").fill("Do not lose this unsaved instruction.")
        if width < 1024:
            page.locator("#automation-back").click()
            expect(item).to_be_focused()
            expect(page.locator("#automations-editor")).to_be_hidden()
            item.click()
            expect(page.locator("#automation-name")).to_have_value("Preserve the current draft")
            expect(page.locator("#automation-instruction")).to_have_value("Do not lose this unsaved instruction.")
        page.get_by_role("button", name="Run History", exact=True).click()
        expect(page.locator("#automation-runs")).to_contain_text("Reviewed the current project")
        screenshot("history")
        page.get_by_role("button", name="Automation", exact=True).click()
        page.locator("#automation-delete").click()
        expect(page.get_by_role("dialog")).to_be_visible()
        screenshot("remove-confirm")
        page.get_by_role("button", name="Cancel", exact=True).click()
        for kind in ["once", "interval", "hourly", "weekly", "monthly", "daily"]:
            page.locator("#automation-kind").select_option(kind)
            if kind == "interval":
                assert page.locator("#automation-kind").bounding_box()["width"] >= 192
            screenshot(f"schedule-{kind}")
        if width < 1024:
            page.locator("#automation-back").click()
        page.locator("#automation-view-calendar").click()
        expect(page.locator("#automation-calendar-panel")).to_be_visible()
        for mode in ["month", "week", "day"]:
            page.locator(f"#automation-cal-mode-{mode}").click()
            screenshot(f"calendar-{mode}")
        page.locator("#automation-cal-export").click()
        expect(page.locator("#sched-export-modal")).to_be_visible()
        screenshot("export")
        page.locator("#sched-export-cancel").click()
        if width < 1024:
            page.locator("#automation-calendar-back").click()
            expect(page.locator("#automation-view-calendar")).to_be_visible()
            item.click()
            expect(page.locator("#automation-name")).to_have_value("Preserve the current draft")
            page.locator("#automation-back").click()
        else:
            page.locator("#automation-view-list").click()
        page.locator("#automation-new").click()
        expect(page.locator("#automation-name")).to_be_focused()
        expect(page.locator("#automation-name")).to_have_value("")
        page.locator("#automation-name").fill("New draft")
        page.locator("#automation-instruction").fill("Preserve this new draft too.")
        screenshot("create")
        if width < 1024:
            page.locator("#automation-back").click()
            page.locator("#automation-new").click()
            expect(page.locator("#automation-name")).to_have_value("New draft")
        assert writes == []
        assert errors == []
        browser.close()
