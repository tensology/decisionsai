"""Project folder ready (orange) colour clears once its ready threads have been viewed.

2026-11-12 (Paul, Merrypak / 575 "Load latest Merrypak DB"): the folder stayed orange
after every ready thread under it had been opened. Opening a thread POSTs
acknowledge-ready; when that write fails (seeded zone-aware stamps raised TypeError
server-side) nothing cleared the colour. The harness mock answers that POST with 404,
which reproduces the failure without touching live data.
"""

from __future__ import annotations

import re

import pytest
from playwright.sync_api import Page, expect

from tests.ui.test_development_harness_playwright import TEST_BASE_URL, URL, _install_api

pytestmark = pytest.mark.e2e_playwright

INCOMING = TEST_BASE_URL + "/development/incoming/"
READY = re.compile(r"\bhas-ready-thread\b")

# Thread 17 under decisions:70 finished (blocked) at 07:03 UTC and was never acknowledged.
_READY_THREAD = """
(() => {
  const cfg = () => JSON.parse(localStorage.getItem('grok-ready-test') || '{}');
  const original = window.fetch.bind(window);
  window.fetch = async (input, init) => {
    const response = await original(input, init);
    const url = new URL(typeof input === 'string' ? input : input.url, location.href);
    const method = String(init?.method || 'GET').toUpperCase();
    if (method !== 'GET' || !/^\\/api\\/chats(\\/17)?$/.test(url.pathname)) return response;
    const data = await response.clone().json().catch(() => null);
    if (!data) return response;
    const c = cfg();
    const patch = (row) => {
      if (Number(row?.id) !== 17) return;
      row.ready_acknowledged_at = c.ack ?? null;
      row.development_execution = { ...(row.development_execution || {}), status: 'blocked',
        completed_at: c.completed || '2026-11-12T07:03:24.586604' };
    };
    if (Array.isArray(data.chats)) data.chats.forEach(patch); else patch(data);
    return new Response(JSON.stringify(data), { status: response.status, headers: response.headers });
  };
})();
"""


def _ready_page(page: Page, requests: list, *, completed: str | None = None, seen: str | None = None) -> None:
    _install_api(page, requests)
    page.add_init_script(_READY_THREAD)
    if completed:
        page.add_init_script(f"localStorage.setItem('grok-ready-test', JSON.stringify({{completed: {completed!r}}}));")
    if seen:
        page.add_init_script(
            f"localStorage.setItem('decisions-development-ready-seen', JSON.stringify({{17: {seen!r}}}));")


def test_folder_returns_to_default_colour_after_its_ready_thread_is_viewed(page: Page):
    requests: list[tuple[str, str]] = []
    _ready_page(page, requests)
    page.goto(INCOMING)
    expect(page.locator("#incoming-workspace")).to_be_visible()
    group = page.locator('[data-board-group="decisions:70"]')
    expect(group).to_have_class(READY)
    expect(group.locator(".folder-ready-alert")).to_be_visible()

    if group.locator(".task-list").is_hidden():
        group.locator('[data-board-toggle="decisions:70"]').click()
    group.locator('[data-chat-id="17"]').click()
    expect(page).to_have_url(URL)
    expect(group).not_to_have_class(READY)
    expect(group.locator(".folder-ready-alert")).to_have_count(0)

    # Leaving the thread and reloading keep the default colour; the server write was still tried.
    page.locator("#sidebar-incoming-toggle").click()
    expect(page.locator("#incoming-workspace")).to_be_visible()
    expect(group).not_to_have_class(READY)
    page.reload()
    expect(page.locator("#incoming-workspace")).to_be_visible()
    expect(page.locator('[data-board-group="decisions:70"]')).not_to_have_class(READY)
    assert ("POST", "/api/agent-conversations/17/acknowledge-ready") in requests


def test_opening_the_ready_thread_directly_clears_its_folder(page: Page):
    requests: list[tuple[str, str]] = []
    _ready_page(page, requests)
    page.goto(URL)
    group = page.locator('[data-board-group="decisions:70"]')
    expect(group.locator('.task-row-wrap.active [data-chat-id="17"]')).to_be_visible()
    expect(group).not_to_have_class(READY)
    # 11:09 rule still holds: the open thread's row is highlighted, not the folder.
    expect(group.locator(".project-row")).not_to_have_class(re.compile(r"\bactive\b"))


def test_a_run_finishing_after_the_last_view_turns_the_folder_orange_again(page: Page):
    requests: list[tuple[str, str]] = []
    _ready_page(page, requests, completed="2026-11-12T09:00:00.000000", seen="2026-11-12T08:00:00.000")
    page.goto(INCOMING)
    expect(page.locator("#incoming-workspace")).to_be_visible()
    expect(page.locator('[data-board-group="decisions:70"]')).to_have_class(READY)


def test_a_view_after_the_run_finished_keeps_the_folder_default_on_load(page: Page):
    requests: list[tuple[str, str]] = []
    _ready_page(page, requests, completed="2026-11-12T07:03:24.586604", seen="2026-11-12T09:36:00.000")
    page.goto(INCOMING)
    expect(page.locator("#incoming-workspace")).to_be_visible()
    expect(page.locator('[data-board-group="decisions:70"]')).not_to_have_class(READY)


def test_section_mark_all_as_read_includes_hidden_threads_and_preserves_other_sections(page: Page):
    _install_api(page, [])
    rows = {
        thread_id: {
            "id": thread_id, "title": f"Finished thread {thread_id}", "development": True,
            "development_board_key": "decisions:70", "development_source_type": "prompt",
            "modified_date": f"2026-11-12T08:{thread_id:02d}:00",
            "development_execution": {"status": "completed", "completed_at": "2026-11-12T08:00:00"},
            "ready_acknowledged_at": None,
        } for thread_id in range(21, 28)
    }
    rows[31] = {**rows[21], "id": 31, "title": "Automation result", "development_board_key": None,
                "development_source_type": "automation"}
    rows[32] = {**rows[31], "id": 32, "title": "Automation running",
                "development_execution": {"status": "running"}}
    rows[33] = {**rows[21], "id": 33, "title": "Other board result", "development_board_key": "decisions:80"}
    page.route("**/api/agent-conversations?*", lambda route: route.fulfill(json={"chats": list(rows.values())}))
    writes = []
    blocked = {21}

    def acknowledge(route):
        thread_id = int(route.request.url.split("/")[-2])
        writes.append(thread_id)
        if thread_id in blocked:
            return route.fulfill(status=500, json={"detail": "Could not acknowledge"})
        rows[thread_id]["ready_acknowledged_at"] = "2026-11-12T09:00:00"
        return route.fulfill(json={"acknowledged": True, "ready_acknowledged_at": rows[thread_id]["ready_acknowledged_at"]})

    page.route("**/api/agent-conversations/*/acknowledge-ready", acknowledge)
    page.goto(INCOMING)

    pinned = page.get_by_role("button", name="Mark all as read in Pinned")
    expect(pinned).to_be_enabled()
    pinned.hover()
    expect(page.get_by_role("tooltip")).to_have_text("Mark all as read")
    assert page.locator('#pinned-board-tree [data-chat-id="21"]').count() == 0
    pinned.click()
    expect(page.get_by_text("Some threads could not be marked as read. Try again.")).to_be_visible()
    assert sorted(writes) == list(range(21, 28))
    expect(pinned).to_be_enabled()
    expect(page.locator("#pinned-section-toggle")).to_have_attribute("aria-expanded", "true")
    expect(page).to_have_url(INCOMING)

    blocked.clear()
    pinned.click()
    expect(pinned).to_be_disabled()
    expect(page.locator('[data-board-group="decisions:70"]')).not_to_have_class(READY)
    assert writes.count(21) == 2
    expect(page.get_by_role("button", name="Mark all as read in Running")).to_be_disabled()
    expect(page.get_by_role("button", name="Mark all as read in Boards")).to_be_enabled()
    expect(page.get_by_role("button", name="Mark all as read in Automations")).to_be_enabled()

    page.reload()
    expect(pinned).to_be_disabled()
    automations = page.get_by_role("button", name="Mark all as read in Automations")
    automations.focus()
    expect(page.get_by_role("tooltip")).to_have_text("Mark all as read")
    automations.press("Enter")
    expect(page.get_by_text("Marked as read.", exact=True)).to_be_visible()
    expect(automations).to_be_disabled()
    expect(page.locator("#automation-threads-toggle")).to_have_attribute("aria-expanded", "false")
    assert writes[-1] == 31
    expect(page.get_by_role("button", name="Mark all as read in Boards")).to_be_enabled()
