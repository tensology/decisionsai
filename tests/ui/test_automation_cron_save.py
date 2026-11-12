"""A browser save must preserve cron and non-editable schedule metadata."""
from copy import deepcopy
from pathlib import Path
from urllib.parse import urlparse

import pytest
from playwright.sync_api import Page, expect

from tests.ui.test_development_modules_playwright import BASE, install_plan

pytestmark = pytest.mark.e2e_playwright


@pytest.mark.parametrize("width", [390, 768, 1280, 1440])
def test_cron_save_round_trip(page: Page, width, tmp_path):
    install_plan(page)
    page.add_init_script("""sessionStorage.setItem('decisions_automations_view', 'list');
        sessionStorage.setItem('decisions_automations_status_filter', 'all');
        window.WebSocket = class { static OPEN = 1; readyState = 1; };""")
    row = {
        "id": "auto_9", "name": "Support monitor", "status": "paused",
        "automation_type": "tool_action", "instruction": "", "preset_id": "",
        "schedule": {"kind": "cron", "cron": "1-51/10 7-19 * * *",
                     "timezone": "Africa/Johannesburg", "ignored_occurrences": ["2026-10-30T07:01"]},
    }
    original_schedule = deepcopy(row["schedule"])
    writes, errors = [], []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.set_viewport_size({"width": width, "height": 900})

    def api(route):
        request = route.request
        path = urlparse(request.url).path
        if request.method == "PUT":
            payload = request.post_data_json
            writes.append(payload)
            row.update(payload)
            route.fulfill(json={"success": True, "automation": row})
        elif request.method != "GET":
            raise AssertionError("Unexpected mutation: " + request.method + " " + path)
        elif path == "/api/automations":
            route.fulfill(json={"automations": [row]})
        elif path.endswith("/runs"):
            route.fulfill(json={"runs": []})
        elif path.endswith("/presets"):
            route.fulfill(json={"presets": []})
        elif path == "/api/automations/auto_9":
            route.fulfill(json={"automation": row, "runs": []})
        else:
            route.fulfill(json={})

    page.route("**/api/automations**", api)
    page.goto(BASE + "/development/automations/?automation=auto_9")
    expect(page.locator("#scheduled-workspace")).to_be_visible()
    expect(page.locator("#automation-kind")).to_have_value("cron")
    expect(page.locator("#automation-cron")).to_have_value(row["schedule"]["cron"])
    expect(page.locator("#automation-timezone")).to_have_value("Africa/Johannesburg")
    expect(page.locator("#automation-time-wrap")).to_be_hidden()
    page.locator("#automation-name").fill("Renamed support monitor")
    page.screenshot(path=str(tmp_path / "cron-editor.png"))
    with page.expect_response(lambda response: response.request.method == "PUT" and "/api/automations/auto_9" in response.url):
        page.locator("#automation-save").click()
    expect(page.locator("#automation-detail-title")).to_have_text("Renamed support monitor")
    assert writes[0]["automation_type"] == "tool_action"
    assert writes[0]["schedule"] == original_schedule
    assert writes[0]["schedule"]["cron"] == "1-51/10 7-19 * * *"
    assert writes[0]["schedule"]["ignored_occurrences"] == ["2026-10-30T07:01"]
    page.locator("#automation-cron").fill("0 8 * * 1-5")
    page.locator("#automation-timezone").fill("Europe/London")
    page.locator("#automation-save").click()
    page.wait_for_function("document.querySelector('#automation-cron').value === '0 8 * * 1-5'")
    assert writes[-1]["schedule"]["cron"] == "0 8 * * 1-5"
    assert writes[-1]["schedule"]["timezone"] == "Europe/London"
    page.locator("#automation-kind").select_option("daily")
    expect(page.locator("#automation-cron-wrap")).to_be_hidden()
    expect(page.locator("#automation-time-wrap")).to_be_visible()
    page.locator("#automation-save").click()
    page.wait_for_timeout(150)
    assert writes[-1]["schedule"]["kind"] == "daily"
    assert "cron" not in writes[-1]["schedule"]
    assert writes[-1]["schedule"]["timezone"] == "Europe/London"
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    assert errors == []
