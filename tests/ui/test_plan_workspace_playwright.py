"""Live end-to-end proof for the compact board Plan workspace."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from playwright.sync_api import Page, expect


pytestmark = pytest.mark.e2e_playwright
ROOT = Path(__file__).resolve().parents[2]
BASE_URL = os.environ.get("DECISIONS_PLAN_E2E_URL", "")


@pytest.mark.skipif(not BASE_URL, reason="Set DECISIONS_PLAN_E2E_URL to run the live Plan workspace proof.")
def test_plan_workspace_create_edit_preview_and_mobile(page: Page):
    errors: list[str] = []
    page.on("console", lambda message: errors.append(message.text) if message.type == "error" else None)
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.set_viewport_size({"width": 1440, "height": 900})
    page.goto(f"{BASE_URL}/development/plan/")

    expect(page.get_by_role("heading", name="Plans")).to_be_visible()
    board_button = page.locator('[data-plan-board-open="decisions:1"]')
    expect(board_button).to_be_visible()
    page.screenshot(path=str(ROOT / "artifacts/plan-workspace-live-home.png"), full_page=True)
    board_button.click()

    expect(page).to_have_url(f"{BASE_URL}/development/boards/decisions/1/plan/")
    if page.get_by_text("No plan items yet").is_visible():
        page.get_by_label("Plan instruction").fill("Create a PRD")
        with page.expect_response("**/plan-workspaces/*/instructions") as response_info:
            page.get_by_role("button", name="Apply instruction").click()
        assert response_info.value.status == 200, response_info.value.text()

    expect(page.get_by_label("Plan item title")).to_have_value("Product requirements")
    page.get_by_label("Plan item content").fill(
        "# Product requirements\n\n## Outcome\n\nA compact Plan workspace.\n\n## Requirements\n\n- FR-001: A board owns its plan."
    )
    page.get_by_label("Plan item status").select_option("review")
    with page.expect_response("**/plan-items/*") as save_response:
        page.get_by_role("button", name="Save").click()
    assert save_response.value.status == 200, save_response.value.text()
    assert save_response.value.json()["revision_count"] >= 2

    page.get_by_role("button", name="Preview").click()
    expect(page.get_by_role("heading", name="Product requirements")).to_be_visible()
    expect(page.get_by_text("FR-001: A board owns its plan.")).to_be_visible()
    page.screenshot(path=str(ROOT / "artifacts/plan-workspace-live-desktop.png"), full_page=True)

    page.set_viewport_size({"width": 390, "height": 844})
    page.reload()
    expect(page.get_by_label("Plan item title")).to_be_visible()
    expect(page.locator(".plan-outline")).to_be_hidden()
    expect(page.locator(".plan-language")).to_be_visible()
    page.screenshot(path=str(ROOT / "artifacts/plan-workspace-live-mobile.png"), full_page=True)

    assert not errors, errors
