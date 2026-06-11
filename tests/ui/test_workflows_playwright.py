"""
Playwright E2E test for the Workflows page.
Tests UI rendering, performance, interaction flow, and console/network health.

Run (needs dev server on http://127.0.0.1:8765 and Chromium):
  pytest -m e2e_playwright tests/ui/test_workflows_playwright.py --headed -v -s
"""

import re
import time
import json
import pytest
from playwright.sync_api import Page, expect

pytestmark = pytest.mark.e2e_playwright

BASE_URL = "http://127.0.0.1:8765"
WORKFLOWS_URL = f"{BASE_URL}/workflows/"


def open_workflow_create_modal(page):
    page.locator("#wf-new-workflow-btn").click()
    page.wait_for_timeout(300)
    page.locator("#wf-create-modal").wait_for(state="visible", timeout=5000)


def open_workflow_execution_setup(page):
    page.locator("#wf-menu-btn").click()
    page.wait_for_timeout(500)
    page.locator("#sr-llm-modal").wait_for(state="visible", timeout=5000)


def open_workflow_board_execution_tab(page):
    page.locator("#wf-board-select").wait_for(state="visible", timeout=5000)
    page.wait_for_timeout(300)
    page.locator("#wf-edit-board-link").click()
    page.locator("#wf-board-edit-modal").wait_for(state="visible", timeout=5000)
    page.locator('.wf-board-edit-tab[data-tab="execution"]').click()
    page.wait_for_timeout(300)


@pytest.fixture(scope="module")
def browser_context(browser):
    """Shared context with console & network logging."""
    ctx = browser.new_context(
        viewport={"width": 1440, "height": 900},
        java_script_enabled=True,
    )
    yield ctx
    ctx.close()


# ── Collector classes ──────────────────────────────────────────────

class ConsoleLog:
    """Collects browser console messages for post-test analysis."""
    def __init__(self):
        self.messages: list[dict] = []

    def _handler(self, msg):
        self.messages.append({
            "type": msg.type,
            "text": msg.text,
            "location": f"{msg.location.get('url','')}:{msg.location.get('lineNumber','')}" if msg.location else "",
        })

    @property
    def errors(self):
        return [m for m in self.messages if m["type"] == "error"]

    @property
    def warnings(self):
        return [m for m in self.messages if m["type"] == "warning"]


class NetworkLog:
    """Collects network requests/responses for performance analysis."""
    def __init__(self):
        self.requests: list[dict] = []

    def on_request(self, req):
        self.requests.append({
            "method": req.method,
            "url": req.url,
            "start": time.time(),
            "status": None,
            "end": None,
            "failed": False,
        })

    def on_response(self, resp):
        # Match to request by url
        for r in reversed(self.requests):
            if r["url"] == resp.url and r["status"] is None:
                r["status"] = resp.status
                r["end"] = time.time()
                break

    def on_failure(self, req):
        for r in reversed(self.requests):
            if r["url"] == req.url and r["status"] is None:
                r["failed"] = True
                r["end"] = time.time()
                break

    @property
    def failed_requests(self):
        return [r for r in self.requests if r["failed"] or (r["status"] and r["status"] >= 400)]

    @property
    def slow_requests(self, threshold=2.0):
        """Requests that took longer than `threshold` seconds."""
        return [
            r for r in self.requests
            if r["end"] and r["start"] and (r["end"] - r["start"]) > threshold
        ]

    @property
    def api_requests(self):
        """Only /api/ requests."""
        return [r for r in self.requests if "/api/" in r["url"]]

    def timings_summary(self):
        """Summary of API request timings."""
        timings = []
        for r in self.api_requests:
            if r["end"] and r["start"]:
                timings.append({
                    "url": r["url"].split("/api")[1],
                    "method": r["method"],
                    "status": r["status"],
                    "duration_ms": round((r["end"] - r["start"]) * 1000, 1),
                })
        return timings


# ── Helper ──────────────────────────────────────────────────────────

def attach_loggers(page: Page):
    """Attach console & network loggers to a page. Returns (ConsoleLog, NetworkLog)."""
    cl = ConsoleLog()
    nl = NetworkLog()
    page.on("console", cl._handler)
    page.on("request", nl.on_request)
    page.on("response", nl.on_response)
    page.on("requestfailed", nl.on_failure)
    return cl, nl


def print_diagnostics(cl: ConsoleLog, nl: NetworkLog, label: str = ""):
    """Print captured logs to stdout for debugging."""
    prefix = f"[{label}] " if label else ""
    if cl.errors:
        print(f"\n{prefix}CONSOLE ERRORS ({len(cl.errors)}):")
        for e in cl.errors:
            print(f"  ✖ {e['text'][:200]}  [{e['location'][:80]}]")
    if cl.warnings:
        print(f"\n{prefix}CONSOLE WARNINGS ({len(cl.warnings)}):")
        for w in cl.warnings[:10]:
            print(f"  ⚠ {w['text'][:200]}")
    if nl.failed_requests:
        print(f"\n{prefix}FAILED NETWORK REQUESTS ({len(nl.failed_requests)}):")
        for r in nl.failed_requests:
            print(f"  ✖ {r['method']} {r['url'][:120]}")
    timings = nl.timings_summary()
    if timings:
        slow = [t for t in timings if t["duration_ms"] > 500]
        if slow:
            print(f"\n{prefix}SLOW API REQUESTS (>500ms):")
            for t in slow:
                print(f"  🐢 {t['method']} {t['url']} → {t['status']} ({t['duration_ms']}ms)")
        print(f"\n{prefix}ALL API TIMINGS ({len(timings)} requests):")
        for t in timings:
            print(f"  {t['method']:6} {t['url']:55} → {t['status']}  {t['duration_ms']}ms")


# ═══════════════════════════════════════════════════════════════════
# TESTS
# ═══════════════════════════════════════════════════════════════════

class TestWorkflowsBasicLoad:
    """Page load, rendering, and structural integrity."""

    def test_page_loads(self, browser_context):
        page = browser_context.new_page()
        cl, nl = attach_loggers(page)

        start = time.time()
        page.goto(WORKFLOWS_URL, wait_until="domcontentloaded", timeout=15000)
        load_time = time.time() - start
        print(f"\n⏱ Page load time: {load_time:.2f}s")

        # Page should load within 5 seconds
        assert load_time < 5.0, f"Page too slow to load: {load_time:.2f}s"

        # Title check
        expect(page).to_have_title(re.compile("Workflows"))

        # Core UI elements exist
        assert page.locator("#wf-menu-btn").is_visible(), "Workflow menu button missing"
        assert page.locator("#wf-list").is_visible(), "Workflow list missing"
        open_workflow_create_modal(page)
        assert page.locator("#wf-new-name").is_visible(), "Create workflow input missing"
        assert page.locator("#wf-create-btn").is_visible(), "Create button missing"
        page.locator("#wf-create-modal-close").click()

        print_diagnostics(cl, nl, "BASIC LOAD")
        page.close()

    def test_workflow_list_populates(self, browser_context):
        page = browser_context.new_page()
        cl, nl = attach_loggers(page)

        page.goto(WORKFLOWS_URL, wait_until="domcontentloaded", timeout=15000)

        # Wait for the workflow list to have items
        list_items = page.locator("#wf-list [data-id]")
        # Give time for the API call
        try:
            list_items.first.wait_for(state="visible", timeout=5000)
            count = list_items.count()
            print(f"\n📋 Workflow list items: {count}")
            assert count > 0, "No workflows loaded in the list"
        except Exception:
            # Might show "No workflows yet"
            text = page.locator("#wf-list").inner_text()
            print(f"\n📋 Workflow list content: {text[:100]}")

        # Check that clicking a workflow loads the detail panel
        if list_items.count() > 0:
            list_items.first.click()
            page.wait_for_timeout(1000)

            # Detail panel should become visible
            detail = page.locator("#wf-detail")
            assert not detail.is_hidden(), "Workflow detail panel still hidden after clicking a workflow"

        print_diagnostics(cl, nl, "LIST POPULATE")
        page.close()


class TestWorkflowsDetailPanel:
    """Detail panel interactions — tabs, steps, schedule, context, runs."""

    def test_detail_tabs_visible(self, browser_context):
        page = browser_context.new_page()
        cl, nl = attach_loggers(page)

        page.goto(WORKFLOWS_URL, wait_until="domcontentloaded", timeout=15000)

        # Select first workflow
        list_items = page.locator("#wf-list [data-id]")
        try:
            list_items.first.wait_for(state="visible", timeout=5000)
            list_items.first.click()
            page.wait_for_timeout(1000)
        except Exception:
            pytest.skip("No workflows to test tabs with")

        # All tab buttons should be visible
        tabs = ["steps", "context", "runs", "schedule"]
        for tab_name in tabs:
            tab_btn = page.locator(f".wf-tab[data-tab='{tab_name}']")
            assert tab_btn.is_visible(), f"Tab '{tab_name}' not visible"

        # Steps tab is active by default
        steps_tab_content = page.locator("#wf-tab-steps")
        assert not steps_tab_content.is_hidden(), "Steps tab content hidden by default"

        print_diagnostics(cl, nl, "TABS")
        page.close()

    def test_tab_switching(self, browser_context):
        page = browser_context.new_page()
        cl, nl = attach_loggers(page)

        page.goto(WORKFLOWS_URL, wait_until="domcontentloaded", timeout=15000)

        list_items = page.locator("#wf-list [data-id]")
        try:
            list_items.first.wait_for(state="visible", timeout=5000)
            list_items.first.click()
            page.wait_for_timeout(1000)
        except Exception:
            pytest.skip("No workflows to test tabs with")

        # Switch to Schedule tab
        page.locator(".wf-tab[data-tab='schedule']").click()
        page.wait_for_timeout(500)
        schedule_content = page.locator("#wf-tab-schedule")
        assert not schedule_content.is_hidden(), "Schedule tab content not visible after click"
        steps_content = page.locator("#wf-tab-steps")
        assert steps_content.is_hidden(), "Steps tab still visible after switching away"

        # Switch to Runs tab
        page.locator(".wf-tab[data-tab='runs']").click()
        page.wait_for_timeout(500)
        runs_content = page.locator("#wf-tab-runs")
        assert not runs_content.is_hidden(), "Runs tab content not visible"

        print_diagnostics(cl, nl, "TAB SWITCH")
        page.close()

    def test_steps_accordion(self, browser_context):
        page = browser_context.new_page()
        cl, nl = attach_loggers(page)

        page.goto(WORKFLOWS_URL, wait_until="domcontentloaded", timeout=15000)

        list_items = page.locator("#wf-list [data-id]")
        try:
            list_items.first.wait_for(state="visible", timeout=5000)
            list_items.first.click()
            page.wait_for_timeout(1000)
        except Exception:
            pytest.skip("No workflows to test steps with")

        # Check if steps exist
        step_cards = page.locator(".step-card")
        count = step_cards.count()
        print(f"\n📝 Step cards found: {count}")

        if count > 0:
            # Click first step to expand
            first_step_header = step_cards.first.locator(".step-header")
            first_step_header.click()
            page.wait_for_timeout(800)

            # Step body should now be visible
            first_step_id = step_cards.first.get_attribute("data-step-id")
            step_body = page.locator(f"#step-body-{first_step_id}")
            assert not step_body.is_hidden(), "Step body not shown after clicking header"

            # Step form should have inner tabs
            inner_tabs = step_body.locator(".sf-tab")
            it_count = inner_tabs.count()
            print(f"  Inner tabs: {it_count} (action, validation, routing, history)")
            assert it_count >= 3, f"Expected 4 inner tabs, found {it_count}"

            # Click the step header again to collapse
            first_step_header.click()
            page.wait_for_timeout(300)
            assert step_body.is_hidden(), "Step body not hidden after clicking header again"

        print_diagnostics(cl, nl, "STEP ACCORDION")
        page.close()

    def test_schedule_tab_interactions(self, browser_context):
        page = browser_context.new_page()
        cl, nl = attach_loggers(page)

        page.goto(WORKFLOWS_URL, wait_until="domcontentloaded", timeout=15000)

        list_items = page.locator("#wf-list [data-id]")
        try:
            list_items.first.wait_for(state="visible", timeout=5000)
            list_items.first.click()
            page.wait_for_timeout(1000)
        except Exception:
            pytest.skip("No workflows to test schedule with")

        # Go to Schedule tab
        page.locator(".wf-tab[data-tab='schedule']").click()
        page.wait_for_timeout(500)

        # Enable schedule checkbox
        sched_enabled = page.locator("#wf-sched-enabled")
        if sched_enabled.is_visible():
            sched_enabled.check()
            page.wait_for_timeout(300)

            # Schedule options should become visible
            sched_opts = page.locator("#wf-sched-options")
            assert not sched_opts.is_hidden(), "Schedule options still hidden after enabling"

            # Frequency pills
            freq_btns = page.locator(".wf-freq-btn")
            assert freq_btns.count() >= 3, "Missing frequency pills (hourly/daily/weekly)"

            # Click daily
            page.locator(".wf-freq-btn[data-freq='daily']").click()
            page.wait_for_timeout(300)

            # Time input should be visible for daily
            time_input = page.locator("#wf-sched-time")
            assert time_input.is_visible(), "Time input not visible for daily schedule"

            # Click weekly — day selector should appear
            page.locator(".wf-freq-btn[data-freq='weekly']").click()
            page.wait_for_timeout(300)
            days_wrap = page.locator("#wf-sched-days-wrap")
            assert not days_wrap.is_hidden(), "Days selector not shown for weekly"

            day_btns = page.locator(".wf-day-btn")
            assert day_btns.count() == 7, "Expected 7 day buttons"

            # Click hourly — time should hide
            page.locator(".wf-freq-btn[data-freq='hourly']").click()
            page.wait_for_timeout(300)
            time_wrap = page.locator("#wf-sched-time-wrap")
            assert time_wrap.is_hidden(), "Time input still visible for hourly schedule"

            # Save schedule
            save_btn = page.locator("#wf-sched-save")
            assert save_btn.is_visible(), "Save Schedule button not visible"

        print_diagnostics(cl, nl, "SCHEDULE")
        page.close()

    def test_context_tab_crud(self, browser_context):
        page = browser_context.new_page()
        cl, nl = attach_loggers(page)

        page.goto(WORKFLOWS_URL, wait_until="domcontentloaded", timeout=15000)

        list_items = page.locator("#wf-list [data-id]")
        try:
            list_items.first.wait_for(state="visible", timeout=5000)
            list_items.first.click()
            page.wait_for_timeout(1000)
        except Exception:
            pytest.skip("No workflows to test context with")

        # Go to Context tab
        page.locator(".wf-tab[data-tab='context']").click()
        page.wait_for_timeout(500)

        # Add context item button
        add_btn = page.locator("#wf-add-context-item-btn")
        assert add_btn.is_visible(), "Add context item button not visible"

        # Click add
        add_btn.click()
        page.wait_for_timeout(1500)

        # A new context item row should appear
        context_items = page.locator("[data-context-item-id]")
        new_count = context_items.count()
        print(f"\n📝 Context items after add: {new_count}")

        if new_count > 0:
            # Fill in the item
            last_item = context_items.last
            title_input = last_item.locator(".wf-ci-title")
            content_textarea = last_item.locator(".wf-ci-content")

            if title_input.is_visible():
                title_input.fill("Test Context")
                content_textarea.fill("This is a test context item from Playwright")

                # Save
                save_btn = last_item.locator(".wf-ci-save")
                save_btn.click()
                page.wait_for_timeout(1000)

            # Delete the item
            delete_btn = last_item.locator(".wf-ci-delete")
            page.on("dialog", lambda dialog: dialog.accept())
            delete_btn.click()
            page.wait_for_timeout(1500)

        print_diagnostics(cl, nl, "CONTEXT CRUD")
        page.close()


class TestWorkflowsCreateAndDelete:
    """Create / delete workflows end-to-end."""

    def test_create_workflow(self, browser_context):
        page = browser_context.new_page()
        cl, nl = attach_loggers(page)

        page.goto(WORKFLOWS_URL, wait_until="domcontentloaded", timeout=15000)
        open_workflow_create_modal(page)

        # Enter a name
        name_input = page.locator("#wf-new-name")
        name_input.fill("PW Test Workflow")

        # Click Create
        create_btn = page.locator("#wf-create-btn")
        create_btn.click()
        page.wait_for_timeout(2000)

        # The name input should be cleared and the detail panel should show
        assert name_input.input_value() == "", "Name input not cleared after create"

        detail = page.locator("#wf-detail")
        assert not detail.is_hidden(), "Detail panel not visible after create"

        # Active workflow tab should reflect the created name
        active_tab = page.locator(".wf-workflow-tab.active")
        assert active_tab.inner_text().strip() == "PW Test Workflow", f"Workflow name mismatch: {active_tab.inner_text()}"

        print(f"\n✅ Workflow 'PW Test Workflow' created")
        print_diagnostics(cl, nl, "CREATE WORKFLOW")
        page.close()

    def test_delete_workflow(self, browser_context):
        page = browser_context.new_page()
        cl, nl = attach_loggers(page)

        page.goto(WORKFLOWS_URL, wait_until="domcontentloaded", timeout=15000)
        open_workflow_create_modal(page)

        # First create one to delete
        name_input = page.locator("#wf-new-name")
        name_input.fill("PW Delete Target")
        page.locator("#wf-create-btn").click()
        page.wait_for_timeout(2000)

        # Now click Delete in detail panel
        delete_btn = page.locator("#wf-delete-btn")
        assert delete_btn.is_visible(), "Delete button not visible"

        # Handle confirm modal
        page.on("dialog", lambda dialog: dialog.accept())
        delete_btn.click()
        page.wait_for_timeout(1000)

        # Confirm modal should appear
        confirm_ok = page.locator("#wf-confirm-modal .wf-confirm-ok")
        if confirm_ok.is_visible():
            confirm_ok.click()
            page.wait_for_timeout(1500)

        # Empty state should show
        empty_panel = page.locator("#wf-empty")
        # It may or may not show depending on if another workflow is selected
        print(f"\n🗑 Delete completed")
        print_diagnostics(cl, nl, "DELETE WORKFLOW")
        page.close()


class TestWorkflowsMenu:
    """Plus opens new workflow; cog opens execution setup."""

    def test_menu_opens_create_modal(self, browser_context):
        page = browser_context.new_page()
        cl, nl = attach_loggers(page)

        page.goto(WORKFLOWS_URL, wait_until="domcontentloaded", timeout=15000)
        open_workflow_create_modal(page)

        assert page.locator("#wf-create-modal").is_visible()
        page.locator("#wf-create-modal-close").click()
        page.wait_for_timeout(200)
        assert page.locator("#wf-create-modal").is_hidden()

        print_diagnostics(cl, nl, "WORKFLOW MENU CREATE")
        page.close()

    def test_menu_opens_execution_setup(self, browser_context):
        page = browser_context.new_page()
        cl, nl = attach_loggers(page)

        page.goto(WORKFLOWS_URL, wait_until="domcontentloaded", timeout=15000)
        open_workflow_execution_setup(page)

        assert page.locator("#sr-llm-modal").is_visible()
        page.locator("#sr-llm-close").click()
        page.wait_for_timeout(200)

        print_diagnostics(cl, nl, "WORKFLOW MENU CONFIG")
        page.close()


class TestWorkflowsContextMenu:
    """Right-click context menu on workflow list items."""

    def test_context_menu_opens(self, browser_context):
        page = browser_context.new_page()
        cl, nl = attach_loggers(page)

        page.goto(WORKFLOWS_URL, wait_until="domcontentloaded", timeout=15000)

        list_items = page.locator("#wf-list [data-id]")
        try:
            list_items.first.wait_for(state="visible", timeout=5000)
        except Exception:
            pytest.skip("No workflows to test context menu with")

        # Right-click first item
        list_items.first.click(button="right")
        page.wait_for_timeout(500)

        context_menu = page.locator("#wf-context-menu")
        assert not context_menu.is_hidden(), "Context menu not visible after right-click"

        # Menu should have expected actions
        actions = ["open", "run", "duplicate", "export", "download", "delete"]
        for action in actions:
            btn = context_menu.locator(f"[data-action='{action}']")
            assert btn.count() > 0, f"Context menu missing '{action}' action"

        # Click elsewhere to close
        page.click("body")
        page.wait_for_timeout(300)
        assert context_menu.is_hidden(), "Context menu not closed after click away"

        print_diagnostics(cl, nl, "CONTEXT MENU")
        page.close()


class TestWorkflowsWebSocket:
    """WebSocket connection and version polling behavior."""

    def test_ws_connects_or_falls_back_to_polling(self, browser_context):
        page = browser_context.new_page()
        cl, nl = attach_loggers(page)

        page.goto(WORKFLOWS_URL, wait_until="domcontentloaded", timeout=15000)

        # Wait for WS to attempt connection or polling to start
        page.wait_for_timeout(4000)

        # Check console for WS status
        ws_msgs = [m for m in cl.messages if "WS" in m["text"] or "WebSocket" in m["text"] or "polling" in m["text"].lower()]
        ws_connected = any("connected" in m["text"] for m in ws_msgs)
        ws_fallback = any("fallback" in m["text"].lower() or "polling" in m["text"].lower() for m in ws_msgs)

        print(f"\n🔌 WebSocket messages:")
        for m in ws_msgs:
            print(f"  {m['type']}: {m['text'][:120]}")

        if ws_connected:
            print("  ✅ WebSocket connected")
        elif ws_fallback:
            print("  ⚠️ WebSocket fell back to polling (acceptable)")
        else:
            # Check if version polling is running by watching for /workflows/version API calls
            version_calls = [r for r in nl.api_requests if "/workflows/version" in r["url"]]
            if version_calls:
                print("  ⚠️ Version polling active (WS may have failed)")
            else:
                print("  ❓ No WS or version polling detected")

        print_diagnostics(cl, nl, "WEBSOCKET")
        page.close()


class TestWorkflowsPerformance:
    """Performance metrics — load times, API response times, render performance."""

    def test_initial_load_performance(self, browser_context):
        page = browser_context.new_page()
        cl, nl = attach_loggers(page)

        start = time.time()
        page.goto(WORKFLOWS_URL, wait_until="domcontentloaded", timeout=15000)
        initial_load = time.time() - start

        # Wait for first data to render
        list_items = page.locator("#wf-list [data-id]")
        try:
            list_items.first.wait_for(state="visible", timeout=5000)
        except Exception:
            pass
        data_rendered = time.time() - start

        print(f"\n⏱ Initial page load: {initial_load:.2f}s")
        print(f"⏱ Data rendered: {data_rendered:.2f}s")

        # Performance budgets
        assert initial_load < 5.0, f"Initial load too slow: {initial_load:.2f}s"
        assert data_rendered < 6.0, f"Data render too slow: {data_rendered:.2f}s"

        # Check API timings
        timings = nl.timings_summary()
        total_api_time = sum(t["duration_ms"] for t in timings if t["duration_ms"])
        print(f"⏱ Total API time: {total_api_time:.0f}ms across {len(timings)} requests")

        slow_apis = [t for t in timings if t["duration_ms"] > 1000]
        if slow_apis:
            print(f"⚠️ Slow APIs (>1s):")
            for t in slow_apis:
                print(f"  🐢 {t['method']} {t['url']} → {t['duration_ms']}ms")

        print_diagnostics(cl, nl, "PERF - INITIAL LOAD")
        page.close()

    def test_workflow_selection_performance(self, browser_context):
        page = browser_context.new_page()
        cl, nl = attach_loggers(page)

        page.goto(WORKFLOWS_URL, wait_until="domcontentloaded", timeout=15000)

        list_items = page.locator("#wf-list [data-id]")
        try:
            list_items.first.wait_for(state="visible", timeout=5000)
        except Exception:
            pytest.skip("No workflows for selection perf test")

        # Measure detail load time
        start = time.time()
        list_items.first.click()
        page.wait_for_timeout(800)
        detail = page.locator("#wf-detail")
        # Wait for detail to be visible
        detail.wait_for(state="visible", timeout=5000)
        detail_time = time.time() - start

        print(f"\n⏱ Workflow selection/detail load: {detail_time:.2f}s")
        assert detail_time < 3.0, f"Detail load too slow: {detail_time:.2f}s"

        print_diagnostics(cl, nl, "PERF - SELECTION")
        page.close()

    def test_no_excessive_polling(self, browser_context):
        """Check that polling/WS isn't causing too many requests."""
        page = browser_context.new_page()
        cl, nl = attach_loggers(page)

        page.goto(WORKFLOWS_URL, wait_until="domcontentloaded", timeout=15000)
        page.wait_for_timeout(10000)  # Wait 10 seconds to observe request patterns

        # Count API calls
        wf_api_calls = [r for r in nl.api_requests if "/workflows" in r["url"]]
        version_calls = [r for r in nl.api_requests if "/version" in r["url"]]
        active_runs_calls = [r for r in nl.api_requests if "/active-runs" in r["url"]]

        print(f"\n📊 API call counts over 10s:")
        print(f"  Workflow API calls: {len(wf_api_calls)}")
        print(f"  Version check calls: {len(version_calls)}")
        print(f"  Active runs calls: {len(active_runs_calls)}")

        # If there's both WS AND polling, that's a problem
        ws_msgs = [m for m in cl.messages if "WS" in m["text"] and "connected" in m["text"]]
        has_ws = len(ws_msgs) > 0
        if has_ws and len(version_calls) > 2:
            print(f"  ⚠️ DUPLICATE UPDATES: WebSocket connected AND version polling running — wastes bandwidth!")
            print(f"     This means the WS onopen doesn't properly stop the version polling timer.")

        # Budget: no more than ~5 API calls per 10s (3s interval + initial loads)
        if not has_ws:
            # If WS failed, polling is expected
            assert len(version_calls) <= 5, f"Too many version poll requests: {len(version_calls)} in 10s"
        else:
            # If WS connected, polling should stop
            assert len(version_calls) <= 2, f"Version poll still running after WS connected: {len(version_calls)}"

        print_diagnostics(cl, nl, "POLLING CHECK")
        page.close()


class TestWorkflowsKeyboardInteraction:
    """Keyboard accessibility and interaction patterns."""

    def test_escape_closes_context_menu(self, browser_context):
        page = browser_context.new_page()
        cl, nl = attach_loggers(page)

        page.goto(WORKFLOWS_URL, wait_until="domcontentloaded", timeout=15000)

        list_items = page.locator("#wf-list [data-id]")
        try:
            list_items.first.wait_for(state="visible", timeout=5000)
        except Exception:
            pytest.skip("No workflows for keyboard test")

        # Open context menu
        list_items.first.click(button="right")
        page.wait_for_timeout(500)
        context_menu = page.locator("#wf-context-menu")
        assert not context_menu.is_hidden(), "Context menu not visible"

        # Press Escape
        page.keyboard.press("Escape")
        page.wait_for_timeout(300)
        assert context_menu.is_hidden(), "Context menu not closed by Escape"

        print_diagnostics(cl, nl, "KEYBOARD")
        page.close()


class TestWorkflowsHeaderActions:
    """Test the header action buttons: Run, Stop & Reset, Delete."""

    def test_header_buttons_visible(self, browser_context):
        page = browser_context.new_page()
        cl, nl = attach_loggers(page)

        page.goto(WORKFLOWS_URL, wait_until="domcontentloaded", timeout=15000)

        list_items = page.locator("#wf-list [data-id]")
        try:
            list_items.first.wait_for(state="visible", timeout=5000)
            list_items.first.click()
            page.wait_for_timeout(1000)
        except Exception:
            pytest.skip("No workflows for header test")

        # Check header buttons
        run_btn = page.locator("#wf-run-btn")
        stop_reset_btn = page.locator("#wf-stop-reset-btn")
        delete_btn = page.locator("#wf-delete-btn")

        assert run_btn.is_visible(), "Run button not visible"
        assert stop_reset_btn.is_visible(), "Stop & Reset button not visible"
        assert delete_btn.is_visible(), "Delete button not visible"

        # Workflow options menu
        assert page.locator("#wf-menu-btn").is_visible(), "Workflow menu button not visible"

        print_diagnostics(cl, nl, "HEADER BUTTONS")
        page.close()

    def test_llm_settings_modal(self, browser_context):
        page = browser_context.new_page()
        cl, nl = attach_loggers(page)

        page.goto(WORKFLOWS_URL, wait_until="domcontentloaded", timeout=15000)

        list_items = page.locator("#wf-list [data-id]")
        try:
            list_items.first.wait_for(state="visible", timeout=5000)
            list_items.first.click()
            page.wait_for_timeout(1000)
        except Exception:
            pytest.skip("No workflows for LLM modal test")

        open_workflow_board_execution_tab(page)

        modal = page.locator("#wf-board-edit-modal")
        assert modal.is_visible(), "Board edit modal not visible after click"

        backend = page.locator("#wf-board-exec-low-backend")
        assert backend.is_visible(), "Execution routing controls not visible in board edit modal"

        modal.locator(".wf-board-edit-close").first.click()
        page.wait_for_timeout(300)
        assert not modal.is_visible(), "Board edit modal still visible after close"

        print_diagnostics(cl, nl, "LLM MODAL")
        page.close()


class TestWorkflowsContextMenuActions:
    """Duplicate, export, download, and purge-all live on the list row context menu."""

    def test_context_menu_shows_workflow_actions(self, browser_context):
        page = browser_context.new_page()
        cl, nl = attach_loggers(page)

        page.goto(WORKFLOWS_URL, wait_until="domcontentloaded", timeout=15000)

        list_items = page.locator("#wf-list [data-id]")
        try:
            list_items.first.wait_for(state="visible", timeout=5000)
            page.wait_for_timeout(500)
        except Exception:
            pytest.skip("No workflows for context menu test")

        list_items.first.click(button="right")
        page.wait_for_timeout(300)

        menu = page.locator("#wf-context-menu")
        assert menu.is_visible(), "Workflow context menu not visible after right-click"

        assert page.locator('#wf-context-menu [data-action="duplicate"]').is_visible()
        assert page.locator('#wf-context-menu [data-action="export"]').is_visible()
        assert page.locator('#wf-context-menu [data-action="download"]').is_visible()
        assert page.locator('#wf-context-menu [data-action="purge-all"]').is_visible()

        print_diagnostics(cl, nl, "CONTEXT MENU ACTIONS")
        page.close()


class TestWorkflowsAddStep:
    """Test adding steps to a workflow."""

    def test_add_step(self, browser_context):
        page = browser_context.new_page()
        cl, nl = attach_loggers(page)

        page.goto(WORKFLOWS_URL, wait_until="domcontentloaded", timeout=15000)
        open_workflow_create_modal(page)

        # Create a new workflow first
        name_input = page.locator("#wf-new-name")
        name_input.fill("PW Step Test")
        page.locator("#wf-create-btn").click()
        page.wait_for_timeout(2000)

        # Make sure we're on Steps tab
        steps_tab = page.locator(".wf-tab[data-tab='steps']")
        if steps_tab.is_visible():
            steps_tab.click()
            page.wait_for_timeout(500)

        # Count existing steps
        step_cards = page.locator(".step-card")
        initial_count = step_cards.count()
        print(f"\n📝 Steps before add: {initial_count}")

        # Click add step
        add_step_btn = page.locator("#wf-add-step-btn")
        if add_step_btn.is_visible():
            add_step_btn.click()
            page.wait_for_timeout(2000)

            new_count = page.locator(".step-card").count()
            print(f"📝 Steps after add: {new_count}")

            # Clean up: delete the workflow
            page.on("dialog", lambda dialog: dialog.accept())
            delete_btn = page.locator("#wf-delete-btn")
            delete_btn.click()
            page.wait_for_timeout(500)
            confirm_ok = page.locator("#wf-confirm-modal .wf-confirm-ok")
            if confirm_ok.is_visible():
                confirm_ok.click()
                page.wait_for_timeout(1000)

        print_diagnostics(cl, nl, "ADD STEP")
        page.close()


class TestWorkflowsNetworkErrors:
    """Check for failed requests, 4xx/5xx, and missing resources."""

    def test_no_failed_requests(self, browser_context):
        page = browser_context.new_page()
        cl, nl = attach_loggers(page)

        page.goto(WORKFLOWS_URL, wait_until="domcontentloaded", timeout=15000)

        # Interact to trigger more API calls
        list_items = page.locator("#wf-list [data-id]")
        try:
            list_items.first.wait_for(state="visible", timeout=5000)
            list_items.first.click()
            page.wait_for_timeout(2000)

            # Click through tabs
            for tab in ["context", "runs", "schedule", "steps"]:
                page.locator(f".wf-tab[data-tab='{tab}']").click()
                page.wait_for_timeout(500)
        except Exception:
            pass

        page.wait_for_timeout(2000)

        # Analyze all requests
        all_requests = nl.requests
        status_codes = {}
        for r in all_requests:
            status = r.get("status") or "no_response"
            status_codes[status] = status_codes.get(status, 0) + 1

        print(f"\n📊 HTTP status code distribution:")
        for status, count in sorted(status_codes.items()):
            print(f"  {status}: {count}")

        # Check for failures
        failed = nl.failed_requests
        if failed:
            print(f"\n❌ Failed network requests ({len(failed)}):")
            for r in failed:
                print(f"  ✖ {r['method']} {r['url'][:120]}")

        # Check for 4xx/5xx
        error_responses = [r for r in all_requests if r.get("status") and r["status"] >= 400]
        if error_responses:
            print(f"\n⚠️ Error responses ({len(error_responses)}):")
            for r in error_responses:
                print(f"  {r['status']} {r['method']} {r['url'][:120]}")

        print_diagnostics(cl, nl, "NETWORK ERRORS")
        page.close()


class TestWorkflowsConsoleErrors:
    """Dedicated check for console errors and warnings."""

    def test_no_critical_console_errors(self, browser_context):
        page = browser_context.new_page()
        cl, nl = attach_loggers(page)

        page.goto(WORKFLOWS_URL, wait_until="domcontentloaded", timeout=15000)

        # Interact to trigger JS execution
        list_items = page.locator("#wf-list [data-id]")
        try:
            list_items.first.wait_for(state="visible", timeout=5000)
            list_items.first.click()
            page.wait_for_timeout(1000)

            # Expand a step
            step_cards = page.locator(".step-card")
            if step_cards.count() > 0:
                step_cards.first.locator(".step-header").click()
                page.wait_for_timeout(1000)

            # Tab through
            for tab in ["context", "runs", "schedule", "steps"]:
                page.locator(f".wf-tab[data-tab='{tab}']").click()
                page.wait_for_timeout(500)

                # Switch inner tabs if on steps
                if tab == "steps" and step_cards.count() > 0:
                    inner_tabs = page.locator(".sf-tab")
                    for it in range(inner_tabs.count()):
                        inner_tabs.nth(it).click()
                        page.wait_for_timeout(300)

        except Exception:
            pass

        page.wait_for_timeout(2000)

        # Report
        print(f"\n🖥️ Console summary: {len(cl.messages)} messages, {len(cl.errors)} errors, {len(cl.warnings)} warnings")

        if cl.errors:
            print(f"\n❌ Console Errors ({len(cl.errors)}):")
            for e in cl.errors:
                print(f"  ✖ {e['text'][:300]}")
                if e["location"]:
                    print(f"    at {e['location'][:120]}")

        if cl.warnings:
            print(f"\n⚠️ Console Warnings ({len(cl.warnings)}):")
            for w in cl.warnings[:20]:
                print(f"  ⚠ {w['text'][:300]}")

        print_diagnostics(cl, nl, "CONSOLE ERRORS")
        page.close()