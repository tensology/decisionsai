"""Render a fallback planner in the Development transcript with isolated APIs."""

import pytest
from playwright.sync_api import expect

from distr.core.initiative.context import ContextBundle
from distr.core.initiative.planners import _fallback_planner_markdown
from tests.ui.test_development_harness_playwright import _install_api
from tests.ui.test_incoming_dismiss_undo import fixture_base

pytestmark = pytest.mark.e2e_playwright


@pytest.mark.parametrize("width", [390, 768, 1280, 1440])
def test_planner_oversight_link_opens_the_exact_development_thread(page, fixture_base, tmp_path, width):
    bundle = ContextBundle(project_oversight={
        "omitted_threads": 2, "skipped_scope_mismatches": 1, "projects": [{
            "project_id": 7, "project_name": "Shop project", "threads": [{
                "chat_id": 31, "title": "Checkout rollout", "status": "waiting",
                "assigned_workflow_id": 44, "attention": [
                    {"kind": "needs_input", "recommendation": "Review the thread question.",
                     "question": "Can checkout deploy after 18:00?"},
                    {"kind": "delivery_unconfirmed", "recommendation": "Reconcile the original Telegram delivery before retrying."},
                ],
            }],
        }],
    })
    markdown = _fallback_planner_markdown("morning", {}, bundle, "", "")
    requests = []
    _install_api(page, requests)

    def chat(chat_id, title, response):
        return {"id": chat_id, "title": title, "project_id": 7, "development": True,
                "development_board_key": "decisions:70", "development_ticket_id": 42,
                "messages": [{"role": "user", "content": "Show my latest plan.", "chat_row_id": 17},
                             {"role": "assistant", "content": response, "chat_row_id": 18}]}

    brief_chat = chat(17, "Morning brief", markdown)
    destination = chat(31, "Checkout rollout", "Exact rollout thread.")
    page.route("**/api/chats?*", lambda route: route.fulfill(json={"chats": [brief_chat, destination]}))
    page.route("**/api/chats/17", lambda route: route.fulfill(json=brief_chat))
    page.route("**/api/chats/17?*", lambda route: route.fulfill(json=brief_chat))
    page.route("**/api/chats/31", lambda route: route.fulfill(json=destination))
    page.route("**/api/chats/31?*", lambda route: route.fulfill(json=destination))
    for resource in ("artifacts", "plans"):
        page.route(f"**/api/workflows/studio/tasks/31/{resource}", lambda route: route.fulfill(json={"items": []}))
    page.route("**/api/workflows/studio/tasks/31/execution", lambda route: route.fulfill(json={}))
    page.route("**/api/workflows/studio/tasks/31/time", lambda route: route.fulfill(json={"seconds": 0, "paused": True}))
    page.set_viewport_size({"width": width, "height": 900})
    page.goto(f"{fixture_base}/development/threads/17/")

    brief = page.locator(".message-markdown")
    link = brief.get_by_role("link", name="Checkout rollout", exact=True).first
    expect(link).to_have_attribute("href", "/development/threads/31/")
    expect(brief.get_by_text("Reconcile the original Telegram delivery before retrying.", exact=False)).to_have_count(1)
    expect(brief).to_contain_text("Can checkout deploy after 18:00?")
    expect(brief).to_contain_text("2 other threads fell outside the saved project snapshot.")
    expect(brief).to_contain_text("1 thread needs its project link checked before review.")
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")

    brief.get_by_role("heading", name="Morning Check-in", exact=True).scroll_into_view_if_needed()
    page.screenshot(path=tmp_path / "planner-top.png")
    brief.get_by_text("1 thread needs its project link checked before review.", exact=True).scroll_into_view_if_needed()
    page.screenshot(path=tmp_path / "planner-blockers.png")
    link.click()
    page.wait_for_url("**/development/threads/31/")
    expect(page.locator("#message-list").get_by_text("Exact rollout thread.", exact=True)).to_be_visible()
    page.screenshot(path=tmp_path / "planner-destination.png")
