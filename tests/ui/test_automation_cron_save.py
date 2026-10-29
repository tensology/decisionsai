"""A browser save must preserve cron and non-editable schedule metadata."""
from urllib.parse import urlparse
from copy import deepcopy

import pytest
from playwright.sync_api import sync_playwright, expect


@pytest.mark.parametrize("width", [390, 768, 1280, 1440])
def test_cron_save_round_trip(width, tmp_path):
    row = {
        "id": "auto_9", "name": "Support monitor", "status": "paused",
        "automation_type": "tool_action", "instruction": "", "preset_id": "",
        "schedule": {"kind": "cron", "cron": "1-51/10 7-19 * * *",
                     "timezone": "Africa/Johannesburg", "ignored_occurrences": ["2026-10-30T07:01"]},
    }
    original_schedule = deepcopy(row["schedule"])
    writes, errors = [], []
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": width, "height": 900})
        page.add_init_script("""(() => {
            const sockets = [];
            window.WebSocket = class {
                static CONNECTING = 0; static OPEN = 1;
                readyState = 1;
                constructor() { sockets.push(this); }
            };
            window.automationTestUpdate = () => sockets.forEach(socket =>
                socket.onmessage?.({data: JSON.stringify({type: 'workflow_updated'})}));
        })();""")
        page.on("pageerror", lambda error: errors.append(str(error)))

        def api(route):
            request = route.request
            path = urlparse(request.url).path
            if request.method != "GET":
                payload = request.post_data_json
                writes.append(payload)
                row.update(payload)
                route.fulfill(json={"success": True, "automation": row})
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

        page.route("**/api/**", api)
        page.goto("http://127.0.0.1:8765/automations/")
        page.locator(".automation-item").first.click()
        expect(page.locator("#automation-kind")).to_have_value("cron")
        expect(page.locator("#automation-cron")).to_have_value(row["schedule"]["cron"])
        expect(page.locator("#automation-timezone")).to_have_value("Africa/Johannesburg")
        expect(page.locator("#automation-time-wrap")).to_be_hidden()
        page.locator("#automation-name").fill("Renamed support monitor")
        # A live run emits these updates while another automation is edited.
        with page.expect_response(lambda response: response.url.endswith('/api/automations/auto_9')):
            page.evaluate("window.automationTestUpdate()")
        page.wait_for_timeout(100)
        expect(page.locator("#automation-name")).to_have_value("Renamed support monitor")
        page.screenshot(path=str(tmp_path / "cron-editor.png"))
        page.locator("#automation-save").click()
        expect(page.locator("#automation-detail-title")).to_have_text("Renamed support monitor")
        assert writes[0]["automation_type"] == "tool_action"
        assert writes[0]["schedule"] == original_schedule
        assert writes[0]["schedule"]["cron"] == "1-51/10 7-19 * * *"
        assert writes[0]["schedule"]["ignored_occurrences"] == ["2026-10-30T07:01"]
        page.locator("#automation-cron").fill("0 8 * * 1-5")
        page.locator("#automation-timezone").fill("Europe/London")
        with page.expect_response(lambda response: response.url.endswith('/api/automations/auto_9')):
            page.evaluate("window.automationTestUpdate()")
        page.wait_for_timeout(100)
        expect(page.locator("#automation-cron")).to_have_value("0 8 * * 1-5")
        expect(page.locator("#automation-timezone")).to_have_value("Europe/London")
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
        browser.close()
