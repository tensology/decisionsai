"""Canonical Workflows E2E: ticket queue -> red loop -> green completion.

Keep workflow coverage here instead of adding more workflow E2E files. The
fixture, SSE capture, and runner live in scripts/workflow_ticket_loop_e2e.py so
this test stays easy to tweak as the developer workflow evolves.

Run (server required on http://127.0.0.1:8765):
  rtk scripts/run_workflow_ticket_loop_e2e.sh --browser chromium --browser webkit -q
"""

from __future__ import annotations

import os
import re

import pytest
from playwright.sync_api import Page, expect

from scripts.workflow_ticket_loop_e2e import (
    EXPECTED_SSE_EVENT_TYPES,
    WorkflowTicketLoopHarness,
    wait_until_workflow_ws_open,
    workflow_ws_bootstrap_script,
    workflow_ws_close_script,
    workflow_ws_messages_script,
)


pytestmark = pytest.mark.e2e_playwright

BASE_URL = os.environ.get("WORKFLOW_E2E_BASE_URL", "http://127.0.0.1:8765").rstrip("/")


def _select_seeded_workflow(page: Page, workflow_name: str) -> None:
    search = page.locator("#wf-search")
    if search.count() > 0:
        search.fill(workflow_name)
        page.wait_for_timeout(700)
    row = page.locator("#wf-list [data-id]", has_text=workflow_name).first
    expect(row).to_be_visible(timeout=15000)
    row.click()
    page.wait_for_timeout(1000)


def _select_seeded_board(page: Page, board_id: int, ticket_title: str) -> None:
    board_select = page.locator("#wf-board-select")
    expect(board_select).to_be_visible(timeout=15000)
    board_select.select_option(value=f"database:{board_id}")
    page.wait_for_timeout(1200)
    ticket_row = page.locator("#wf-board-ticket-list .wf-board-ticket-row", has_text=ticket_title).first
    if ticket_row.count() == 0 or not ticket_row.is_visible():
        lane_header = page.locator("#wf-board-ticket-list .kb-ticket-list-section-head").first
        if lane_header.count() > 0:
            lane_header.click()
            page.wait_for_timeout(500)
    expect(ticket_row).to_be_visible(timeout=15000)


def _relevant_console_errors(messages: list[str]) -> list[str]:
    ignored = ("favicon", "ResizeObserver loop", "websocket", "ws://")
    return [msg for msg in messages if not any(token.lower() in msg.lower() for token in ignored)]


def _backend_display_pattern(backend_id: str) -> re.Pattern[str]:
    """Match the route id or its human-facing label in Mission Control."""
    labels = {
        "codex": r"(?:codex|Codex CLI)",
        "pi": r"(?:pi|Pi CLI)",
        "claude_code": r"(?:claude_code|Claude Code)",
    }
    return re.compile(labels.get(backend_id, re.escape(backend_id)), re.IGNORECASE)


@pytest.mark.parametrize(
    "viewport",
    [{"width": 1440, "height": 1000}, {"width": 390, "height": 844}],
    ids=["desktop", "mobile"],
)
def test_ticket_queue_loop_realtime_context_and_green_exit(
    page: Page,
    tmp_path,
    viewport: dict[str, int],
    request,
) -> None:
    """One dependable workflow journey that can be expanded in place."""
    harness = WorkflowTicketLoopHarness(BASE_URL)
    if not harness.server_reachable():
        pytest.skip(f"Web server not reachable at {BASE_URL}")
    ids = harness.seed_until_green_fixture(tmp_path)
    backend_id = str(ids["backend_id"])
    backend_model = str(ids["backend_model"])
    backend_display = _backend_display_pattern(backend_id)
    page.set_viewport_size(viewport)

    console_errors: list[str] = []
    failed_requests: list[str] = []
    page.on("console", lambda msg: console_errors.append(msg.text) if msg.type == "error" else None)
    page.on("requestfailed", lambda req: failed_requests.append(req.url))

    page.goto(f"{BASE_URL}/workflows/", wait_until="domcontentloaded", timeout=30000)
    expect(page).to_have_title(re.compile("Workflows"))
    expect(page.locator("#wf-list")).to_be_visible()

    sse_capture = harness.start_sse_capture(f"workflow-e2e-{ids['workflow_id']}-{viewport['width']}")
    request.addfinalizer(sse_capture.stop)
    page.evaluate(workflow_ws_bootstrap_script())
    wait_until_workflow_ws_open(page)

    _select_seeded_workflow(page, str(ids["workflow_name"]))
    _select_seeded_board(page, int(ids["board_id"]), str(ids["ticket_title"]))

    board_ticket = page.locator("#wf-board-ticket-list .wf-board-ticket-row", has_text=str(ids["ticket_title"])).first
    badges_box = board_ticket.locator(".kb-ticket-list-badges").bounding_box()
    title_box = board_ticket.locator(".kb-ticket-list-title").bounding_box()
    assert badges_box and title_box
    assert badges_box["x"] + badges_box["width"] <= title_box["x"], \
        "Board ticket badges overlap and clip the start of the ticket title"
    board_ticket.locator(".kb-act-add-workflow").click()
    queued_ticket = page.locator("#wf-workflow-tickets-list .wf-workflow-ticket-row", has_text=str(ids["ticket_title"])).first
    expect(queued_ticket).to_be_visible(timeout=15000)
    expect(queued_ticket).to_contain_text("Queued")

    queued_ticket.locator(".wf-workflow-ticket-run").click()
    preview = page.locator("#wf-run-preview-modal")
    expect(preview).to_be_visible(timeout=15000)
    preview_body = page.locator("#wf-run-preview-body")
    expect(preview_body).to_contain_text(str(ids["ticket_title"]))
    expect(preview_body).to_contain_text(str(ids["project_name"]))
    expect(preview_body).to_contain_text("Executor")
    # The modal first renders its local ticket snapshot, then replaces it with
    # the authoritative project/board route fetched from ``cli-context``.
    # A cold server may still be warming its backend catalog at this point.
    expect(preview_body).to_contain_text(backend_display, timeout=15000)
    expect(preview_body).to_contain_text(backend_model, timeout=15000)
    expect(preview_body).to_contain_text("Skills", timeout=15000)
    expect(preview_body).to_contain_text("webapp-testing", timeout=15000)

    page.locator("#wf-run-preview-confirm").click()

    page.locator(".wf-tab[data-tab='loop']").click()
    expect(page.locator(".wf-loop-list-row", has_text="Validate browser with Playwright")).to_be_visible(timeout=15000)
    expect(page.locator(".wf-loop-list-row", has_text="Inspect with Browser Use")).to_be_visible(timeout=15000)
    expect(page.locator(".wf-loop-list-row", has_text="Inspect with Computer Use")).to_be_visible(timeout=15000)
    failed_check = page.locator(".wf-loop-list-row.wf-loop-step-status--failed", has_text="Validate browser with Playwright").first
    expect(failed_check).to_be_visible(timeout=35000)
    running_fix = page.locator(".wf-loop-list-row.wf-loop-step-status--running", has_text="Fix and rerun green check").first
    expect(running_fix).to_be_visible(timeout=35000)

    feed = page.locator("#wf-loop-feed-messages")
    expect(feed).to_contain_text("RED validation failed", timeout=25000)
    expect(feed).to_contain_text("Loop iteration 1", timeout=25000)
    expect(feed).to_contain_text(
        re.compile(
            rf"Route selected:\s*{re.escape(backend_id)}\s*/\s*{re.escape(backend_model)}",
            re.IGNORECASE,
        ),
        timeout=25000,
    )
    expect(feed).to_contain_text("Skills: webapp-testing", timeout=25000)
    expect(feed).to_contain_text("Tools: playwright, browser_use", timeout=25000)
    expect(feed).to_contain_text("Context: ticket_workflow_brief", timeout=25000)
    transcript = feed.locator(".wf-loop-transcript")
    expect(transcript).to_be_visible(timeout=15000)
    expect(transcript).to_contain_text("Execution transcript")
    transcript.locator(":scope > summary").click()
    expect(transcript.locator(".wf-loop-transcript-record").first).to_be_visible(timeout=15000)
    expect(transcript).to_contain_text("Developer data (JSON)")

    runs_tab = page.locator("#wf-runs-tab-btn")
    expect(runs_tab).to_be_visible(timeout=20000)
    runs_tab.click()
    active_runs = page.locator("#wf-active-runs-list")
    expect(active_runs).to_contain_text(str(ids["ticket_title"]), timeout=25000)
    expect(active_runs).to_contain_text("Fix and rerun green check", timeout=25000)
    expect(active_runs).to_contain_text(re.compile(r"Pass\s+2\s+·\s+1/3 retries", re.IGNORECASE), timeout=25000)
    expect(active_runs).to_contain_text(backend_display)
    expect(active_runs).to_contain_text(backend_model)
    expect(active_runs).to_contain_text("webapp-testing")
    expect(active_runs).to_contain_text("agent")

    page.locator("#wf-runs-tab-btn").click()
    page.locator(".wf-runs-subtab[data-runs-tab='timeline']").click()
    timeline = page.locator("#wf-orchestrator-events-list")
    expect(timeline).to_contain_text("route_decided", timeout=25000)
    expect(timeline).to_contain_text("Skills: executing-plans, tdd-workflow, webapp-testing, verification-loop")
    expect(timeline).to_contain_text("Tools: playwright, browser_use")
    expect(timeline).to_contain_text("RED validation failed")
    expect(timeline).to_contain_text("GREEN validation passed", timeout=60000)
    expect(timeline).to_contain_text("BROWSER_USE_GREEN", timeout=60000)
    expect(timeline).to_contain_text("workflow_run_completed", timeout=60000)

    harness.wait_until_ticket_not_active(str(ids["ticket_title"]))
    page.locator(".wf-runs-subtab[data-runs-tab='active']").click()
    expect(page.locator("#wf-active-runs-list")).not_to_contain_text(str(ids["ticket_title"]), timeout=15000)

    terminal = harness.wait_until_run_completed(
        int(ids["workflow_id"]),
        int(ids["ticket_id"]),
        board_id=int(ids["board_id"]),
        ticket_title=str(ids["ticket_title"]),
    )
    assert terminal["run_status"] == "completed"
    assert terminal["ticket_workflow_status"] == "completed"
    assert terminal["green_seen"]
    assert terminal["completed_seen"]

    ws_messages = page.evaluate(workflow_ws_messages_script())
    workflow_updates = [msg for msg in ws_messages if msg.get("type") == "workflow_updated"]
    assert len(workflow_updates) >= 4, f"Expected realtime workflow WebSocket updates, saw {ws_messages}"
    assert not page.evaluate("() => window.__workflowWsErrors || []")
    page.evaluate(workflow_ws_close_script())

    sse_orchestration = harness.wait_for_sse_events(
        sse_capture.events,
        int(ids["workflow_id"]),
        EXPECTED_SSE_EVENT_TYPES,
    )
    sse_capture.stop()
    assert not sse_capture.state.get("error"), f"SSE stream failed: {sse_capture.state['error']}"
    loop_event = next(event for event in sse_orchestration if event.get("event_type") == "loop_iteration")
    assert loop_event.get("payload", {}).get("iteration") == 1
    assert loop_event.get("payload", {}).get("context"), "Loop SSE event missing context handoff"
    passed_step = [
        event for event in sse_orchestration
        if event.get("event_type") == "workflow_step_completed"
        and event.get("status") == "passed"
        and event.get("payload", {}).get("step_name") == "Validate browser with Playwright"
    ]
    assert passed_step, "SSE stream missing final Playwright step pass"
    assert passed_step[0].get("payload", {}).get("tools") == ["playwright", "browser_use"]
    browser_use_step = [
        event for event in sse_orchestration
        if event.get("event_type") == "workflow_step_completed"
        and event.get("status") == "passed"
        and event.get("payload", {}).get("step_name") == "Inspect with Browser Use"
    ]
    assert browser_use_step, "SSE stream missing explicit Browser Use step pass"
    assert browser_use_step[0].get("payload", {}).get("tools") == ["browser_use"]

    assert not failed_requests, f"Unexpected failed browser requests: {failed_requests[:5]}"
    assert not _relevant_console_errors(console_errors), f"Unexpected console errors: {console_errors[:5]}"
