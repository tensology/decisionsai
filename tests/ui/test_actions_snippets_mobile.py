"""Responsive journeys against the live templates, with all app API writes mocked."""

import json
from urllib.parse import urlparse

import pytest
from playwright.sync_api import expect, sync_playwright


@pytest.fixture
def mobile_page():
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        state = {
            "actions": [{"id": 101, "title": "Prepare the weekly report with a long action title", "description": "Review the current report.", "additional_trigger_words": '["weekly report"]', "is_instruction": True, "instruction_text": "Summarize the report.", "recording_filename": None}],
            "snippets": [{"id": 201, "text": "A useful snippet that remains readable on a narrow phone screen.", "remote_hotkey": "ctrl+shift+1"}],
            "fail_save": False, "fail_load": False, "fail_record": False, "writes": [],
        }

        def api(route):
            request = route.request
            path = urlparse(request.url).path
            method = request.method
            if method != "GET":
                state["writes"].append((method, path))
            kind = "actions" if path.startswith("/api/actions") else "snippets" if path.startswith("/api/snippets") else None
            if kind is None:
                route.fulfill(json={})
                return
            if state["fail_load"] and method == "GET":
                route.fulfill(status=503, json={"detail": "Test service unavailable"})
                return
            if path.endswith("recording") or path.endswith("/play"):
                route.fulfill(status=503 if state["fail_record"] else 200, json={"detail": "Recording unavailable"} if state["fail_record"] else {"ok": True})
                return
            rows = state[kind]
            tail = path.rstrip("/").split("/")[-1]
            item_id = int(tail) if tail.isdigit() else None
            row = next((row for row in rows if row["id"] == item_id), None)
            if method == "GET":
                route.fulfill(json=row if item_id else rows)
            elif method in ("POST", "PUT"):
                if state["fail_save"]:
                    route.fulfill(status=503, json={"detail": "Test save failed"})
                    return
                payload = json.loads(request.post_data or "{}")
                if method == "POST":
                    row = {"id": 102 if kind == "actions" else 202, **payload}
                    rows.append(row)
                else:
                    row.update(payload)
                route.fulfill(json=row)
            elif method == "DELETE":
                rows.remove(row)
                route.fulfill(json={"ok": True})
            else:
                route.abort()

        page.route("**/api/**", api)
        yield page, state
        browser.close()


def assert_fits(page):
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    assert page.evaluate("document.documentElement.scrollHeight <= innerHeight + 1")


@pytest.mark.parametrize("width", [375, 390, 768, 1280, 1440])
def test_actions_list_editor_and_back(mobile_page, width, tmp_path):
    page, state = mobile_page
    page.set_viewport_size({"width": width, "height": 844 if width < 760 else 900})
    page.goto("http://127.0.0.1:8765/actions/")
    item = page.locator(".action-item").first
    expect(item).to_be_visible()
    if width < 1024:
        expect(page.locator("#actions-editor")).to_be_hidden()
    page.screenshot(path=str(tmp_path / "actions-list.png"))
    item.click()
    expect(page.locator("#detail-title")).to_have_value(state["actions"][0]["title"])
    expect(page.locator("#tab-instruction")).to_be_visible()
    assert_fits(page)
    page.screenshot(path=str(tmp_path / "actions-editor.png"))
    page.locator("#detail-title").fill("Edited report")
    if width < 1024:
        page.locator("#action-back").click()
        expect(page.locator("#actions-editor")).to_be_hidden()
        item.click()
        expect(page.locator("#detail-title")).to_have_value("Edited report")
    page.locator("#action-update").click()
    expect(page.locator("#action-detail-title")).to_have_text("Edited report")
    assert state["actions"][0]["title"] == "Edited report"
    state["fail_save"] = True
    page.locator("#detail-description").fill("Keep this unsaved text")
    page.locator("#action-update").click()
    expect(page.get_by_text("Test save failed", exact=True)).to_be_visible()
    expect(page.locator("#detail-description")).to_have_value("Keep this unsaved text")
    page.screenshot(path=str(tmp_path / "actions-save-error.png"))
    page.get_by_role("button", name="Record Inputs", exact=True).click()
    state["fail_record"] = True
    page.locator("#detail-record-btn").click()
    expect(page.locator("#detail-record-btn")).to_be_visible()
    expect(page.locator("#action-back")).to_be_enabled()
    state["fail_record"] = False
    page.locator("#detail-record-btn").click()
    expect(page.locator("#detail-stop-btn")).to_be_visible()
    expect(page.locator("#action-back")).to_be_disabled()
    page.locator("#detail-stop-btn").click()
    expect(page.locator("#action-back")).to_be_enabled()
    page.locator("#action-remove").click()
    expect(page.get_by_role("dialog")).to_be_visible()
    assert_fits(page)
    page.screenshot(path=str(tmp_path / "actions-confirm.png"))
    page.get_by_role("button", name="Cancel", exact=True).click()
    expect(page.get_by_role("dialog")).to_be_hidden()


@pytest.mark.parametrize("width", [375, 390, 768, 1280, 1440])
def test_snippets_edit_hotkey_save_and_delete(mobile_page, width, tmp_path):
    page, state = mobile_page
    page.set_viewport_size({"width": width, "height": 844 if width < 760 else 900})
    page.goto("http://127.0.0.1:8765/snippets/")
    row = page.locator(".snippet-row").first
    expect(row).to_be_visible()
    assert_fits(page)
    if width <= 640:
        assert row.locator(".snippet-text").bounding_box()["width"] >= 240
        for selector in (".snippet-hotkey", ".snippet-save", ".snippet-delete"):
            assert row.locator(selector).bounding_box()["height"] >= 44
    page.screenshot(path=str(tmp_path / "snippets-list.png"))
    row.locator(".snippet-text").fill("Updated snippet")
    row.locator(".snippet-hotkey").click()
    row.locator(".snippet-hotkey").press("Backspace")
    row.locator(".snippet-save").click()
    expect(row.locator(".snippet-text")).to_have_attribute("value", "Updated snippet")
    assert state["snippets"][0]["remote_hotkey"] == ""
    state["fail_save"] = True
    row.locator(".snippet-text").fill("Preserve unsaved snippet")
    row.locator(".snippet-save").click()
    expect(page.get_by_text("Test save failed", exact=True)).to_be_visible()
    expect(row.locator(".snippet-text")).to_have_value("Preserve unsaved snippet")
    page.screenshot(path=str(tmp_path / "snippets-save-error.png"))
    row.locator(".snippet-delete").click()
    expect(page.get_by_role("dialog")).to_be_visible()
    assert_fits(page)
    page.screenshot(path=str(tmp_path / "snippets-confirm.png"))
    page.get_by_role("button", name="Cancel", exact=True).click()
    expect(page.get_by_role("dialog")).to_be_hidden()
    assert len(state["snippets"]) == 1
