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
    assert ("POST", "/api/chats/17/acknowledge-ready") in requests


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
