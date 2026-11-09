"""Read-to-me state and failure recovery against the real UI with isolated APIs."""
import os
from pathlib import Path

import pytest
from playwright.sync_api import Page, expect

from tests.ui.test_development_harness_playwright import _install_api

pytestmark = pytest.mark.e2e_playwright
BASE = os.environ.get("DEVELOPMENT_TEST_URL", "http://127.0.0.1:8765")


def install_reader(page, *, delay=False):
    _install_api(page, [], terminal_session=False)
    rows = [{"id": f"read-{i}", "title": f"Review proposal {i}", "project_name": "Menu Project",
             "project_id": 7, "source": "gmail", "source_label": "Gmail",
             "description": "Review the launch copy before publishing."} for i in (1, 2)]
    calls = []
    state = {"status": 200, "replay_ok": True}

    def proposals(route):
        if route.request.url.split("?")[0].endswith("/read"):
            calls.append(("read", route.request.post_data_json))
            route.fulfill(status=state["status"], json={"success": state["status"] == 200,
                          "spoken_text": "Review the launch copy.", "error": "Speech is unavailable."})
        else:
            route.fulfill(json={"proposals": rows})

    def replay(route):
        calls.append(("replay", route.request.post_data_json))
        route.fulfill(json={"ok": state["replay_ok"]})

    page.route("**/api/workflows/studio/incoming/proposals**", proposals)
    page.route("**/api/chats/speak-spoken", replay)
    if delay:
        page.add_init_script("""const originalFetch = window.fetch.bind(window);
          window.fetch = async (...args) => {
            if (String(args[0]).endsWith('/proposals/read')) await new Promise(r => setTimeout(r, 600));
            return originalFetch(...args);
          };""")
    page.goto(BASE + "/development/incoming/")
    expect(page.locator("[data-incoming-proposal-read]")).to_have_count(2)
    return rows, calls, state


def test_read_survives_rerender_and_prevents_overlapping_clicks(page: Page):
    _, calls, _ = install_reader(page, delay=True)
    first = page.locator('[data-incoming-proposal-read="read-1"]')
    second = page.locator('[data-incoming-proposal-read="read-2"]')
    first.click()
    expect(first).to_have_text("Preparing...")
    expect(second).to_be_disabled()
    second.dispatch_event("click")
    page.locator("#incoming-resync").click()
    expect(first).to_have_attribute("aria-busy", "true")
    expect(first).to_be_enabled(timeout=5000)
    assert [kind for kind, _ in calls] == ["read"]


def test_cached_replay_checks_speech_failure_and_recovers(page: Page):
    _, calls, state = install_reader(page)
    button = page.locator('[data-incoming-proposal-read="read-1"]')
    button.click()
    expect(button).to_be_enabled()
    state["replay_ok"] = False
    button.click()
    expect(page.locator("#studio-toast")).to_contain_text("Speech is unavailable")
    expect(button).to_be_enabled()
    state["replay_ok"] = True
    button.click()
    expect(button).to_be_enabled()
    assert [kind for kind, _ in calls] == ["read", "replay", "replay"]


def test_changed_proposal_regenerates_the_spoken_summary(page: Page):
    rows, calls, _ = install_reader(page)
    button = page.locator('[data-incoming-proposal-read="read-1"]')
    button.click()
    expect(button).to_be_enabled()
    rows[0]["description"] = "The scope changed. Check the payment form instead."
    page.locator("#incoming-resync").click()
    expect(page.locator('.proposal-card').filter(has=button)).to_contain_text("The scope changed")
    button.click()
    expect(button).to_be_enabled()
    assert [kind for kind, _ in calls] == ["read", "read"]
    assert "payment form" in calls[-1][1]["text"]


def test_generation_failure_does_not_cache_and_allows_retry(page: Page):
    _, calls, state = install_reader(page)
    state["status"] = 503
    button = page.locator('[data-incoming-proposal-read="read-1"]')
    button.click()
    expect(page.locator("#studio-toast")).to_contain_text("Speech is unavailable")
    expect(button).to_be_enabled()
    state["status"] = 200
    button.click()
    expect(button).to_be_enabled()
    assert [kind for kind, _ in calls] == ["read", "read"]


def test_stalled_read_times_out_and_restores_buttons(page: Page):
    page.add_init_script("""const timer = window.setTimeout.bind(window);
      window.setTimeout = (fn, delay, ...args) => timer(fn, delay === 65000 ? 50 : delay, ...args);
      const fetchRequest = window.fetch.bind(window);
      window.fetch = (...args) => String(args[0]).endsWith('/proposals/read')
        ? new Promise((resolve, reject) => args[1].signal.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError'))))
        : fetchRequest(...args);""")
    install_reader(page)
    button = page.locator('[data-incoming-proposal-read="read-1"]')
    button.click()
    expect(page.locator("#studio-toast")).to_contain_text("timed out")
    expect(button).to_be_enabled()
    expect(page.locator('[data-incoming-proposal-read="read-2"]')).to_be_enabled()


def test_leaving_incoming_cancels_pending_read_without_late_speech(page: Page):
    _, calls, _ = install_reader(page, delay=True)
    page.locator('[data-incoming-proposal-read="read-1"]').click()
    page.locator("#sidebar-reports-toggle").click()
    expect(page.locator("#incoming-workspace")).to_be_hidden()
    page.locator("#sidebar-incoming-toggle").click()
    expect(page.locator('[data-incoming-proposal-read="read-1"]')).to_be_enabled()
    page.wait_for_timeout(650)
    assert calls == []


@pytest.mark.parametrize("width", [390, 768, 1280, 1440])
def test_read_states_at_supported_widths(page: Page, width):
    page.set_viewport_size({"width": width, "height": 900})
    _, _, state = install_reader(page, delay=True)
    state["status"] = 503
    folder = Path("/tmp/decisions-read-qa")
    folder.mkdir(exist_ok=True)
    button = page.locator('[data-incoming-proposal-read="read-1"]')
    page.screenshot(path=str(folder / f"{width}-idle.png"))
    button.click()
    expect(button).to_have_text("Preparing...")
    page.screenshot(path=str(folder / f"{width}-preparing.png"))
    expect(page.locator("#studio-toast")).to_contain_text("Speech is unavailable")
    expect(button).to_be_enabled()
    page.screenshot(path=str(folder / f"{width}-error.png"))
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
