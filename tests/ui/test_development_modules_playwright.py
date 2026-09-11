"""Public feature-boundary checks using fixture APIs and the real Development UI."""
from __future__ import annotations

import os
TEST_BASE_URL = os.environ.get("DEVELOPMENT_TEST_URL", "http://127.0.0.1:8765")

import json
import re
from pathlib import Path

import pytest
from playwright.sync_api import Page, expect
from tests.ui.test_development_harness_playwright import _install_api

pytestmark = pytest.mark.e2e_playwright
ROOT = Path(__file__).resolve().parents[2]
BASE = TEST_BASE_URL + ""


def install_plan(page, *, reject_save=False, delay_home=False):
    requests = []
    _install_api(page, requests, terminal_session=False)
    workspace = {
        "id": 101, "board_key": "jira:jira-1", "board_name": "Jira engineering",
        "project_id": 7, "item_count": 2, "root_path": "/tmp/fixture/planning",
        "items": [{"id": i, "title": title, "item_type": "brief", "content_format": "markdown", "content": content, "status": "draft", "revision_count": 1} for i, title, content in [(201, "Brief", "Original brief"), (202, "Requirements", "Original requirements")]],
    }
    def handler(route):
        request = route.request
        path = request.url.split("?")[0]
        if "/plan-items/" in path and request.method == "PATCH":
            if reject_save:
                route.fulfill(status=409, json={"detail": "A newer revision exists. Your draft is retained."})
                return
            item = next(item for item in workspace["items"] if item["id"] == int(path.rsplit("/", 1)[1]))
            item.update({k: v for k, v in request.post_data_json.items() if k != "expected_revision"})
            item["revision_count"] += 1
            route.fulfill(json=item)
        elif path.endswith("/plan-workspaces"):
            route.fulfill(json=workspace if request.method == "POST" else {"items": [workspace]})
        elif path.endswith("/plan-workspaces/101"):
            route.fulfill(json=workspace)
        else:
            route.fallback()
    page.route("**/api/workflows/studio/plan-**", handler)
    if delay_home:
        page.add_init_script("""const original = window.fetch.bind(window); window.fetch = async (...args) => {
          if (String(args[0]).endsWith('/plan-workspaces') && (!args[1]?.method || args[1].method === 'GET')) await new Promise(resolve => setTimeout(resolve, 600));
          return original(...args);
        };""")
    return workspace, requests


def open_plan(page):
    page.goto(BASE + "/development/boards/jira/jira-1/plan/")
    expect(page.locator("#plan-editor")).to_have_value("Original brief")


def test_failed_plan_save_blocks_item_and_sidebar_navigation(page: Page):
    install_plan(page, reject_save=True)
    page.set_viewport_size({"width": 1440, "height": 900})
    open_plan(page)
    page.locator("#plan-editor").fill("Retain this unsaved draft")
    page.locator('[data-plan-item="202"]').click()
    expect(page.locator("#plan-editor")).to_have_value("Retain this unsaved draft")
    page.locator("#sidebar-workflows-toggle").click()
    expect(page.locator("#plan-workspace")).to_be_visible()
    expect(page).to_have_url(BASE + "/development/boards/jira/jira-1/plan/")
    expect(page.locator("#plan-editor")).to_have_value("Retain this unsaved draft")


def test_board_plan_wins_over_delayed_home_and_mobile_can_select_second_item(page: Page):
    install_plan(page, delay_home=True)
    page.set_viewport_size({"width": 390, "height": 844})
    open_plan(page)
    page.wait_for_timeout(800)
    expect(page.locator("#plan-editor")).to_have_value("Original brief")
    page.locator("#plan-mobile-item").select_option("202")
    expect(page.locator("#plan-editor")).to_have_value("Original requirements")
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")


def test_failed_optional_connector_does_not_blank_local_catalog(page: Page):
    _install_api(page, [], terminal_session=False)
    page.route("**/api/tickets/external-boards", lambda route: route.fulfill(status=503, json={"detail": "Unavailable"}))
    page.goto(BASE + "/development/workflows/")
    expect(page.locator('[data-workflow-card="44"]')).to_be_visible()
    expect(page.locator("#board-task-tree")).to_contain_text("Decisions delivery")
    expect(page.locator("#studio-toast")).to_contain_text("Available data remains usable")


@pytest.mark.parametrize("width,height", [(1440, 900), (390, 844)])
def test_feature_views_are_independent_and_render_without_script_errors(page: Page, width, height):
    install_plan(page)
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.set_viewport_size({"width": width, "height": height})
    output = ROOT / "artifacts/development-refactor-2026-09-05"
    output.mkdir(exist_ok=True)
    for name, route, selector in [
        ("planning", "boards/jira/jira-1/plan/", "#plan-editor"),
        ("incoming", "incoming/", "#incoming-list"),
        ("automations", "automations/", "#scheduled-prompt-input"),
        ("workflows", "workflows/", '[data-workflow-card="44"]'),
        ("terminals", "terminals/", '[data-terminal-project="7"]'),
        ("reports", "reports/", "#reports-workspace"),
        ("threads", "threads/17/", "#task-prompt"),
    ]:
        page.goto(BASE + "/development/" + route)
        expect(page.locator(selector)).to_be_visible()
        if name == "incoming":
            expect(page.locator("#incoming-list")).to_contain_text("Menu Project client")
        if name == "threads":
            expect(page.locator("#header-task")).to_contain_text("Implement ticket")
        if width < 680:
            expect(page.locator("#sidebar-open")).to_be_visible()
            page.locator("#sidebar-open").click()
            expect(page.locator("#studio-sidebar")).to_have_class(re.compile(r"\bopen\b"))
            page.locator("#sidebar-close").click()
        page.screenshot(path=str(output / f"{name}-{width}.png"), full_page=True, animations="disabled")
        if width < 680:
            assert page.locator("#studio-sidebar").evaluate("node => node.getBoundingClientRect().right <= 0")
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), name
    assert errors == []


def test_slow_thread_load_cannot_reopen_chat_after_switching_to_plan(page: Page):
    install_plan(page)
    page.add_init_script("""const request = fetch.bind(window); window.fetch = async (...args) => {
      if (String(args[0]).includes('/chats/17?surface=development')) await new Promise(resolve => setTimeout(resolve, 650));
      return request(...args);
    };""")
    page.goto(BASE + "/development/threads/17/")
    page.locator("#sidebar-plan-toggle").click()
    expect(page).to_have_url(BASE + "/development/plan/")
    page.wait_for_timeout(850)
    expect(page.locator("#plan-workspace")).to_be_visible()
    expect(page.locator("#conversation")).to_be_hidden()
    expect(page).to_have_url(BASE + "/development/plan/")


@pytest.mark.parametrize("width,height", [(1440, 900), (390, 844)])
def test_file_review_and_reports_have_independent_actions(page: Page, width, height):
    workspace, requests = install_plan(page)
    workspace["items"][0]["file_path"] = "/tmp/fixture/planning/brief.md"
    imports = []
    def file_review(route):
        if route.request.method == "POST":
            imports.append(route.request.post_data_json)
            workspace["items"][0].update(content="Externally edited brief", revision_count=2)
            route.fulfill(json=workspace["items"][0])
        else:
            route.fulfill(json={"item_id": 201, "saved_content": "Original brief", "file_content": "Externally edited brief", "file_hash": "reviewed-hash", "expected_revision": 1})
    page.route("**/api/workflows/studio/plan-items/201/file-review", file_review)
    page.set_viewport_size({"width": width, "height": height})
    open_plan(page)
    page.locator("#plan-review-file").click()
    expect(page.locator(".plan-file-dialog")).to_be_visible()
    expect(page.locator(".plan-file-dialog")).to_contain_text("Original brief")
    expect(page.locator(".plan-file-dialog")).to_contain_text("Externally edited brief")
    output = ROOT / "artifacts/development-refactor-2026-09-05"
    page.screenshot(path=str(output / f"file-review-{width}.png"), full_page=True, animations="disabled")
    page.get_by_role("button", name="Import reviewed file").click()
    expect(page.locator("#plan-editor")).to_have_value("Externally edited brief")
    assert imports == [{"expected_revision": 1, "file_hash": "reviewed-hash", "action": "import"}]
    page.route("**/api/workflows/studio/reports?*", lambda route: route.fulfill(json={"items": [
        {"id": "workflow:1", "kind": "workflow", "name": "Release verification", "status": "completed", "started_at": "2026-09-05T09:00:00", "duration_seconds": 48},
        {"id": "automation:2", "kind": "automation", "name": "Morning checks", "status": "failed", "started_at": "2026-09-05T09:01:00", "duration_seconds": 3},
    ]}))
    page.goto(BASE + "/development/reports/")
    expect(page.locator("#reports-results tbody tr")).to_have_count(2)
    page.screenshot(path=str(output / f"reports-populated-{width}.png"), full_page=True, animations="disabled")
    if width < 680:
        page.locator(".reports-table-scroll").evaluate("node => node.scrollLeft = node.scrollWidth")
        page.screenshot(path=str(output / f"reports-details-{width}.png"), full_page=True, animations="disabled")
    page.locator("#reports-source").select_option("automation")
    expect(page.locator("#reports-results tbody tr")).to_have_count(1)
    page.locator("#reports-status").select_option("failed")
    expect(page.locator("#reports-results tbody tr")).to_have_count(1)
    expect(page.locator("#reports-results")).to_contain_text("Morning checks")
    page.route("**/api/workflows/studio/reports?*", lambda route: route.fulfill(status=503, json={"detail": "Temporary report outage"}))
    page.locator("#reports-refresh").click()
    expect(page.locator("#reports-results")).to_contain_text("Use Refresh to retry")
    page.locator("#reports-source").select_option("workflow")
    expect(page.locator("#reports-results")).to_contain_text("Use Refresh to retry")


def test_empty_workflow_cannot_be_started_from_the_library(page: Page):
    _install_api(page, [])
    page.route("**/api/workflows?*", lambda route: route.fulfill(json=[{"id": 44, "name": "Empty workflow", "steps": [], "step_count": 0}]))
    page.goto(BASE + "/development/workflows/")
    expect(page.get_by_role("button", name="Start Empty workflow")).to_be_disabled()
    expect(page.locator('[data-workflow-card="44"]')).to_contain_text("Add a step before running")


def test_restoring_missing_plan_file_keeps_unsaved_editor_draft(page: Page):
    workspace, _ = install_plan(page, reject_save=True)
    workspace["items"][0]["file_path"] = "/tmp/fixture/planning/brief.md"
    actions = []
    def review(route):
        if route.request.method == "POST":
            actions.append(route.request.post_data_json)
            route.fulfill(json=workspace["items"][0])
        else:
            route.fulfill(json={"saved_content": "Original brief", "file_content": None, "file_hash": None, "expected_revision": 1})
    page.route("**/api/workflows/studio/plan-items/201/file-review", review)
    open_plan(page)
    page.locator("#plan-editor").fill("Keep my unsaved draft")
    page.locator("#plan-review-file").click()
    expect(page.locator(".plan-file-comparison label").first).to_contain_text("Original brief")
    page.get_by_role("button", name="Restore saved file").click()
    expect(page.locator(".plan-file-dialog")).to_have_count(0)
    expect(page.locator("#plan-editor")).to_have_value("Keep my unsaved draft")
    page.locator("#sidebar-reports-toggle").click()
    expect(page.locator("#plan-editor")).to_have_value("Keep my unsaved draft")
    assert actions == [{"expected_revision": 1, "file_hash": None, "action": "restore"}]
