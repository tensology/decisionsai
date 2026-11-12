"""Render a fallback planner in the Development transcript with isolated APIs."""

import pytest
from playwright.sync_api import expect

from distr.core.initiative.context import ContextBundle
from distr.core.initiative.planners import _fallback_planner_markdown, format_pending_proposals_markdown
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
            }, *[{
                "chat_id": chat_id, "title": f"Other checkout task {chat_id}", "status": "waiting",
                "attention": [{"kind": "delivery_unconfirmed", "recommendation": "Check the saved delivery receipt."}],
            } for chat_id in range(32, 37)]],
        }, {
            "project_id": 8, "project_name": "Devices project", "threads": [{
                "chat_id": 41, "title": "Device registration", "status": "completed",
                "attention": [{"kind": "delivery_unconfirmed", "recommendation":
                    "Telegram did not confirm this send in time. Reconcile the original Telegram conversation before sending the result again."}],
            }],
        }],
    })
    markdown = _fallback_planner_markdown("morning", {}, bundle, "", "")
    markdown += "\n" + format_pending_proposals_markdown([
        {"id": 81, "title": "Review device registration", "project": {"name": "Devices project"}},
        {"id": 107, "title": "Confirm checkout requirements", "project": {"name": "Shop project"}},
    ])
    requests = []
    _install_api(page, requests)

    def chat(chat_id, title, response, *, project_id=7, board_id=70, ticket_id=42):
        return {"id": chat_id, "title": title, "project_id": project_id, "development": True,
                "development_board_key": f"decisions:{board_id}", "development_ticket_id": ticket_id,
                "messages": [{"role": "user", "content": "Show my latest plan.", "chat_row_id": 17},
                             {"role": "assistant", "content": response, "chat_row_id": 18}]}

    brief_chat = chat(17, "Morning brief", markdown)
    destination = chat(31, "Checkout rollout", "Exact rollout thread.")
    device = chat(41, "Device registration", "Exact device thread.", project_id=8, board_id=80, ticket_id=43)
    page.route("**/api/chats?*", lambda route: route.fulfill(json={"chats": [brief_chat, destination, device]}))
    page.route("**/api/chats/17", lambda route: route.fulfill(json=brief_chat))
    page.route("**/api/chats/17?*", lambda route: route.fulfill(json=brief_chat))
    page.route("**/api/chats/31", lambda route: route.fulfill(json=destination))
    page.route("**/api/chats/31?*", lambda route: route.fulfill(json=destination))
    page.route("**/api/chats/41", lambda route: route.fulfill(json=device))
    page.route("**/api/chats/41?*", lambda route: route.fulfill(json=device))
    for chat_id in (31, 41):
        for resource in ("artifacts", "plans"):
            page.route(f"**/api/workflows/studio/tasks/{chat_id}/{resource}", lambda route: route.fulfill(json={"items": []}))
        page.route(f"**/api/workflows/studio/tasks/{chat_id}/execution", lambda route: route.fulfill(json={}))
        page.route(f"**/api/workflows/studio/tasks/{chat_id}/time", lambda route: route.fulfill(json={"seconds": 0, "paused": True}))
    page.set_viewport_size({"width": width, "height": 900})
    page.goto(f"{fixture_base}/development/threads/17/")

    brief = page.locator(".message-markdown")
    link = brief.get_by_role("link", name="Checkout rollout", exact=True).first
    expect(link).to_have_attribute("href", "/development/threads/31/")
    second_project = brief.get_by_role("link", name="Device registration", exact=True)
    expect(second_project).to_have_attribute("href", "/development/threads/41/")
    labels = brief.get_by_role("link").all_text_contents()
    assert labels.index("Device registration") < labels.index("Other checkout task 32")
    expect(brief.get_by_text("Reconcile the original Telegram delivery before retrying.", exact=False)).to_have_count(1)
    expect(brief.get_by_text("Telegram did not confirm this send in time.", exact=False)).to_have_count(1)
    expect(brief).to_contain_text("Can checkout deploy after 18:00?")
    expect(brief).to_contain_text("2 other threads fell outside the saved project snapshot.")
    expect(brief).to_contain_text("1 thread needs its project link checked before review.")
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    assert page.locator("#development-task-header").evaluate("""header => {
        const bounds = header.getBoundingClientRect();
        const project = header.querySelector('#header-project').getBoundingClientRect();
        const title = header.querySelector('#header-task .name-marquee__text:not([aria-hidden])');
        const switches = [...header.querySelectorAll('#board-chrome-unit .board-view-switch button')];
        return project.width > 40 && project.top >= bounds.top && project.bottom <= bounds.bottom
            && title?.getBoundingClientRect().height > 0
            && switches.length === 4 && switches.every(button => {
                const rect = button.getBoundingClientRect();
                return rect.left >= bounds.left && rect.right <= bounds.right && rect.bottom <= bounds.bottom
                    && button.contains(document.elementFromPoint(rect.x + rect.width / 2, rect.y + rect.height / 2));
            });
    }""")

    brief.get_by_role("heading", name="Morning Check-in", exact=True).scroll_into_view_if_needed()
    page.screenshot(path=tmp_path / "planner-top.png")
    brief.get_by_text("1 thread needs its project link checked before review.", exact=True).evaluate(
        "item => item.scrollIntoView({block: 'center'})"
    )
    page.screenshot(path=tmp_path / "planner-blockers.png")
    proposal_heading = brief.get_by_role("heading", name="Proposals needing your call", exact=True)
    proposal_heading.evaluate("item => item.scrollIntoView({block: 'center'})")
    expect(brief.get_by_text("#81 Review device registration", exact=False)).to_be_in_viewport()
    expect(brief.get_by_text("#107 Confirm checkout requirements", exact=False)).to_be_in_viewport()
    expect(brief.get_by_text("Ask for proposals to review and act in Telegram", exact=False)).to_be_in_viewport()
    page.screenshot(path=tmp_path / "planner-proposal-references.png")
    link.click()
    page.wait_for_url("**/development/threads/31/")
    expect(page.locator("#message-list").get_by_text("Exact rollout thread.", exact=True)).to_be_visible()
    page.screenshot(path=tmp_path / "planner-destination.png")
    page.go_back()
    page.wait_for_url("**/development/threads/17/")
    second_project.click()
    page.wait_for_url("**/development/threads/41/")
    expect(page.locator("#message-list").get_by_text("Exact device thread.", exact=True)).to_be_visible()
    page.screenshot(path=tmp_path / "planner-device.png")
