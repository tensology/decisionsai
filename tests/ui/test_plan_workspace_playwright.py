"""Current Plan acceptance tests with a disposable local server per test.

No user URL, real project, API key or model is used. Real model proposal tests
live separately in tests/core/test_plan_conversation.py.
"""
from __future__ import annotations
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
from urllib.request import urlopen
import pytest
from playwright.sync_api import Page, expect

pytestmark = pytest.mark.e2e_playwright
ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def plan_server(tmp_path):
    # A reserved inherited socket prevents attaching mutation tests to a live app.
    with socket.socket() as listener, (tmp_path / "server.log").open("w+") as log:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        url = f"http://127.0.0.1:{listener.getsockname()[1]}"
        process = subprocess.Popen(
            [sys.executable, "-m", "tests.ui.plan_integration_server", "--fd", str(listener.fileno())],
            cwd=ROOT, pass_fds=(listener.fileno(),), stdout=log, stderr=log,
            env={**os.environ, "DECISIONS_DB_DIR": str(tmp_path / "bootstrap-db"),
                 "DECISIONS_TEST_MODE": "1", "PLAN_FIXTURE_DELAY": "0"},
        )
        try:
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    log.seek(0)
                    pytest.fail(f"Isolated Plan server exited: {log.read()}")
                try:
                    with urlopen(url, timeout=.5) as response:
                        if response.status == 200:
                            break
                except (OSError, TimeoutError):
                    time.sleep(.1)
            else:
                pytest.fail("Isolated Plan server did not become ready")
            yield url
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


@pytest.mark.parametrize("size", [{"width": 1440, "height": 900}, {"width": 390, "height": 844}], ids=["desktop", "mobile"])
def test_prompt_linked_artifacts_and_repeated_build(page: Page, plan_server, size, tmp_path):
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.set_viewport_size(size)
    page.goto(plan_server)
    expect(page.get_by_label("Model", exact=True)).to_have_value("fixture-model")
    expect(page.get_by_role("button", name="Build tasks", exact=True)).to_be_enabled()
    page.get_by_label("Wireframe page").select_option("sitemap")
    expect(page.locator(".plan-mermaid svg")).to_be_visible()
    expect(page.locator(".plan-mermaid")).to_contain_text("Customers (/customers)")
    expect(page.locator(".plan-mermaid")).to_contain_text("Saved (/saved)")
    page.get_by_label("Wireframe page").select_option("")
    page.get_by_role("button", name="Customers /customers", exact=True).click()
    expect(page.get_by_label("Wireframe page")).to_have_value("page:wf-id-customers")
    expect(page.get_by_role("button", name="HTML preview", exact=True)).to_have_attribute("aria-pressed", "true")
    preview = page.frame_locator('iframe[title="Customers"]')
    logo = preview.get_by_role("img", name="Example logo", exact=True)
    expect(logo).to_be_visible()
    expect(logo).to_have_js_property("naturalWidth", 200)
    expect(preview.get_by_role("textbox", name="Email", exact=True)).to_be_visible()
    page.get_by_label("Message", exact=True).fill("Add an optional phone field and update its data and requirements.")
    with page.expect_response("**/plan-workspaces/1/messages") as response:
        page.get_by_role("button", name="Send message", exact=True).click()
    assert response.value.status == 200
    expect(page.get_by_text("Added the phone field to Customers.", exact=True)).to_be_visible()
    expect(preview.get_by_role("textbox", name="Phone", exact=True)).to_be_visible()
    expect(preview.get_by_role("img", name="Example logo", exact=True)).to_have_js_property("naturalWidth", 200)
    page.get_by_role("button", name="ERD", exact=True).click()
    expect(page.locator(".plan-mermaid svg")).to_be_visible()
    expect(page.locator(".plan-mermaid")).to_contain_text("phone")
    page.get_by_role("button", name="Requirements", exact=True).click()
    expect(page.get_by_text("FR-002: Store an optional phone number.", exact=True)).to_be_visible()
    page.get_by_label("Message", exact=True).fill("Keep this draft")
    page.get_by_role("button", name="Close artifact pane").click()
    expect(page.get_by_role("region", name="Artifacts", exact=True)).to_be_hidden()
    expect(page.get_by_label("Message", exact=True)).to_have_value("Keep this draft")
    page.get_by_role("button", name="Wireframes", exact=True).click()
    expect(page.get_by_role("region", name="Artifacts", exact=True)).to_be_visible()
    expect(page.get_by_label("Message", exact=True)).to_have_value("Keep this draft")
    page.get_by_label("Message", exact=True).fill("")
    with page.expect_response("**/plan-workspaces/1/build") as first:
        page.get_by_role("button", name="Build tasks", exact=True).click()
    assert first.value.json()["created"] == 3
    task_list = page.get_by_role("list", name="Generated tasks", exact=True)
    expect(task_list).to_contain_text("Persist customer details")
    expect(task_list.locator("ul li")).to_have_count(2)
    page.reload()
    expect(task_list).to_contain_text("After #2")
    with page.expect_response("**/plan-workspaces/1/build") as repeated:
        page.get_by_role("button", name="Build tasks", exact=True).click()
    assert repeated.value.json()["created"] == 0
    assert repeated.value.json()["reused"] == 3
    assert [task["id"] for task in repeated.value.json()["tasks"]] == [task["id"] for task in first.value.json()["tasks"]]
    page.get_by_role("button", name="Close artifact pane").click()
    page.screenshot(path=str(tmp_path / "plan-tasks.png"), full_page=True)
    assert not errors


def test_uploaded_source_and_pdf_preview(page: Page, plan_server):
    page.goto(plan_server)
    page.get_by_role("button", name="Example brochure.pdf", exact=True).click()
    expect(page.get_by_role("img", name="Example brochure.pdf, page 1", exact=True)).to_be_visible()
    page.locator('input[type="file"]').set_input_files({
        "name": "source-notes.txt", "mimeType": "text/plain", "buffer": b"Synthetic source: customer phone is optional."})
    expect(page.get_by_role("button", name="Remove source-notes.txt", exact=True)).to_be_visible()
    page.get_by_label("Message", exact=True).fill("Use these source notes for the customer form.")
    with page.expect_response("**/plan-workspaces/1/messages") as response:
        page.get_by_role("button", name="Send message", exact=True).click()
    assert response.value.status == 200
    assert response.value.request.post_data_json["attachments"]
    page.reload()
    expect(page.get_by_role("button", name="source-notes.txt", exact=True)).to_be_visible()
