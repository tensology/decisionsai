"""Current Plan acceptance tests with a disposable local server per test.

No user URL, real project, API key or model is used. Real model proposal tests
live separately in tests/core/test_plan_conversation.py.
"""
from __future__ import annotations
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import socket
import subprocess
import sys
from threading import Thread
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


@pytest.mark.parametrize("width", [390, 768, 1440])
def test_interactive_preview_follows_declared_page_links(page: Page, plan_server, width):
    page.set_viewport_size({"width": width, "height": 900})
    page.goto(plan_server)
    expect(page.locator('.iface-edge[data-chrome="no"]')).to_be_visible()
    page.wait_for_load_state("networkidle")
    page.locator(".iface-node", has_text="Saved").press("Enter")
    frame = page.frame_locator("#plan-preview iframe")
    expect(frame.get_by_text("Customer saved", exact=True)).to_be_visible()

    frame.get_by_role("link", name="Back to customers").press("Enter")
    expect(frame.get_by_role("textbox", name="Email")).to_be_visible()
    expect(page.locator("#plan-preview iframe")).to_have_attribute("title", "Customers")
    assert page.url == plan_server + "/"

    page.get_by_role("button", name="Back to all pages").click()
    expect(page.locator(".iface-node")).to_have_count(2)


@pytest.mark.parametrize("width", [390, 768, 1280, 1440])
def test_page_designer_saves_a_responsive_stencil(page: Page, plan_server, tmp_path, width):
    page.set_default_timeout(8000)
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.set_viewport_size({"width": width, "height": 900})
    page.goto(plan_server)
    expect(page.locator(".iface-node")).to_have_count(2)
    page.wait_for_load_state("networkidle")
    page.locator(".iface-node", has_text="Saved").press("Enter")
    page.get_by_role("button", name="Design", exact=True).click()
    designer = page.get_by_role("region", name="Page designer")
    canvas = page.frame_locator('iframe[title="Design canvas"]')
    designer.get_by_role("button", name="Add stack").click()
    designer.get_by_role("button", name="Add row").click()
    designer.get_by_role("button", name="Add field").click()
    designer.get_by_role("textbox", name="Widget label").fill("Bike model")
    designer.get_by_role("button", name="Apply label").click()
    expect(canvas.get_by_role("textbox", name="Bike model")).to_be_visible()
    designer.get_by_role("button", name="Add field").click()
    designer.get_by_role("button", name="Move up").click()
    expect(canvas.locator(".row input").first).to_have_attribute("aria-label", "Text field")
    expect(canvas.locator(".stack .row input")).to_have_count(2)
    designer.get_by_label("width", exact=True).fill("180")
    designer.get_by_role("button", name="Apply layout").click()
    expect(canvas.locator(".design-selected")).to_have_attribute("data-wf-width", "180")

    designer.get_by_role("button", name="Phone", exact=True).click()
    if width == 1440:
        expect(designer.locator("iframe")).to_have_css("width", "390px")
    expect(canvas.locator(".row input").last).to_be_visible()
    tops = canvas.locator(".row input").evaluate_all("inputs => inputs.map(input => input.getBoundingClientRect().top)")
    assert tops[1] > tops[0]
    designer.get_by_role("button", name="Full width").click()
    out = Path(os.environ.get("DECISIONS_DESIGN_EVIDENCE", str(tmp_path)))
    out.mkdir(parents=True, exist_ok=True)
    page.locator(".plan-detail-body").evaluate("body => { body.scrollTop = 0; }")
    page.locator("#plan-preview").evaluate("preview => { preview.scrollTop = 0; }")
    page.screenshot(path=out / f"page-designer-{width}.png", animations="disabled")
    if width == 390:
        designer.get_by_label("Widget label").scroll_into_view_if_needed()
        page.screenshot(path=out / "page-designer-390-inspector.png", animations="disabled")

    page.get_by_role("button", name="Preview", exact=True).click()
    expect(page.frame_locator('#plan-preview iframe').get_by_role("textbox", name="Bike model")).to_have_count(0)
    page.get_by_role("button", name="Design", exact=True).click()
    expect(canvas.get_by_role("textbox", name="Bike model")).to_be_visible()
    with page.expect_response(lambda response: response.request.method == "PATCH" and "/plan-items/" in response.url) as saved:
        designer.get_by_role("button", name="Save design").click()
    assert saved.value.ok
    assert '    stack\n      row\n        input "Text field" width="180"\n        input "Bike model"' in saved.value.request.post_data_json["content"]
    expect(designer.get_by_role("button", name="Save design")).to_be_disabled()
    page.reload()
    expect(page.locator(".iface-node")).to_have_count(2)
    page.wait_for_load_state("networkidle")
    page.locator(".iface-node", has_text="Saved").press("Enter")
    preview = page.frame_locator('#plan-preview iframe[title="Saved"]')
    expect(preview.get_by_role("textbox", name="Bike model")).to_be_visible()
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    assert not errors


def test_map_finds_a_journey_and_saves_a_drawn_connection(page: Page, plan_server):
    page.set_viewport_size({"width": 1440, "height": 900})
    page.goto(plan_server)
    page.get_by_role("button", name="Find page", exact=True).click()
    page.get_by_role("searchbox").fill("/customers")
    page.get_by_role("dialog", name="Find page").get_by_role("button", name="Customers").click()
    page.get_by_role("button", name="Focus journey", exact=True).click()
    customers = page.locator('.iface-node[data-screen-key="customers"]')
    saved = page.locator('.iface-node[data-screen-key="saved"]')
    with page.expect_response(lambda response: response.request.method == "PATCH" and "/plan-items/" in response.url) as response:
        customers.get_by_role("button", name="Connect from Customers").drag_to(saved)
    assert response.value.ok
    expect(page.get_by_role("button", name="All routes", exact=True)).to_be_visible()
    forward = '.iface-edge[data-from="w:wf-id-customers"][data-to="w:wf-id-saved"]'
    expect(page.locator(forward)).to_have_count(1)
    page.reload()
    expect(page.locator(forward)).to_have_count(1)


def _json_messages(page):
    """The composer streams by default; these tests drive the JSON fallback it uses when the stream route is missing."""
    page.route("**/plan-workspaces/1/messages/stream", lambda route: route.fulfill(status=404, body=""))


def test_double_click_opens_stencil_and_edits_base_template(page, plan_server, tmp_path):
    page.set_viewport_size({"width": 1440, "height": 900})
    page.goto(plan_server)
    page.locator(".iface-node", has_text="Saved").dblclick()
    design = page.get_by_role("region", name="Page designer")
    expect(design.get_by_text("Inherits Shell")).to_be_visible()
    design.get_by_role("button", name="Edit base template").click()
    drawer = page.get_by_role("dialog", name="Edit base template · Shell")
    source = drawer.get_by_role("textbox", name="Page source")
    assert source.evaluate("box => box.value.substring(box.selectionStart, box.selectionEnd)") == "template Shell"
    out = Path(os.environ.get("DECISIONS_DESIGN_EVIDENCE", str(tmp_path)))
    out.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=out / "page-designer-base-template.png", animations="disabled")
    source.fill(source.input_value().replace('image "Example logo"', 'image "Updated logo"'))
    drawer.get_by_role("button", name="Apply", exact=True).click()
    canvas = page.frame_locator('iframe[title="Design canvas"]')
    expect(canvas.get_by_role("img", name="Updated logo")).to_be_visible()
    design.get_by_role("button", name="Undo", exact=True).click()
    expect(canvas.get_by_role("img", name="Example logo")).to_be_visible()
    design.get_by_role("button", name="Redo", exact=True).click()
    design.get_by_role("button", name="Save design").click()
    page.get_by_role("button", name="Back to all pages").click()
    page.locator('.iface-node[data-screen-key="customers"]').press("Enter")
    expect(page.frame_locator("#plan-preview iframe").get_by_role("img", name="Updated logo")).to_be_visible()


@pytest.mark.parametrize("width", [390, 768, 1280, 1440])
def test_planning_endpoint_runs_against_local_dev_and_keeps_example(page, plan_server, tmp_path, width):
    page.set_default_timeout(8000)
    received = []
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))

    class DevHandler(BaseHTTPRequestHandler):
        def answer(self):
            body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            request = {"method": self.command, "path": self.path, "json": json.loads(body) if body else None}
            received.append(request)
            payload = json.dumps(request).encode()
            self.send_response(200)
            self.send_header("Access-Control-Allow-Origin", plan_server)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        do_GET = do_POST = do_PUT = do_DELETE = answer

        def do_OPTIONS(self):
            self.send_response(204)
            self.send_header("Access-Control-Allow-Origin", plan_server)
            self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.end_headers()

        def log_message(self, *_args):
            pass

    with ThreadingHTTPServer(("127.0.0.1", 0), DevHandler) as server:
        worker = Thread(target=server.serve_forever)
        worker.start()
        try:
            page.set_viewport_size({"width": width, "height": 900})
            page.goto(plan_server)
            if width == 1440:
                with page.expect_file_chooser() as picker:
                    page.get_by_role("button", name="Attach project files").click()
                picker.value.set_files({"name": "api-notes.txt", "mimeType": "text/plain", "buffer": b"API reference"})
                expect(page.get_by_role("region", name="Artifacts").get_by_role("button", name="api-notes.txt")).to_be_visible()
            page.get_by_role("button", name="Add endpoint").click()
            creator = page.get_by_role("dialog", name="Add endpoint")
            creator.get_by_label("Endpoint name").fill("Customer API")
            creator.get_by_label("Route").fill("/api/customers")
            creator.get_by_label("Method").select_option("POST")
            creator.get_by_role("button", name="Add endpoint", exact=True).click()
            endpoint = page.locator('.iface-node[data-kind="endpoint"]', has_text="Customer API")
            expect(endpoint).to_contain_text("POST")
            endpoint.dblclick()
            editor = page.get_by_role("dialog", name="Customer API")
            assert not errors, errors
            expect(editor).to_be_visible()
            base = f"http://127.0.0.1:{server.server_port}"
            editor.get_by_label("Local or development server").fill(base)
            expect(editor.get_by_label("Request path")).to_have_value("/api/customers")
            expect(editor.get_by_label("Method")).to_have_value("POST")
            out = Path(os.environ.get("DECISIONS_DESIGN_EVIDENCE", str(tmp_path)))
            out.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=out / f"endpoint-empty-{width}.png", animations="disabled")

            editor.get_by_label("Request path").fill("//other-server/path")
            editor.get_by_role("button", name="Send", exact=True).click()
            expect(editor.get_by_role("status")).to_contain_text("local request path")
            assert received == []
            editor.get_by_label("Request path").fill("/api/customers?active=true")
            for method in ["POST", "PUT", "GET", "DELETE"]:
                editor.get_by_label("Method").select_option(method)
                if method != "GET":
                    editor.get_by_label("Request JSON").fill('{"active":true}')
                editor.get_by_role("button", name="Send", exact=True).click()
                expect(editor.get_by_role("region", name="Response")).to_contain_text(f'"method": "{method}"')
                assert received[-1] == {"method": method, "path": "/api/customers?active=true",
                                        "json": None if method == "GET" else {"active": True}}
            editor.get_by_role("button", name="Use as example").click()
            editor.get_by_role("button", name="Save example").click()
            expect(editor.get_by_role("status")).to_have_text("Example saved")
            editor.get_by_label("JSON response example").scroll_into_view_if_needed()
            page.screenshot(path=out / f"endpoint-response-{width}.png", animations="disabled")
            editor.get_by_role("button", name="Close", exact=True).click()
            page.reload()
            endpoint.press("Enter")
            expect(editor.get_by_label("Local or development server")).to_have_value(base)
            expect(editor.get_by_label("JSON response example")).to_have_value(json.dumps(received[-1], indent=2))
            assert not errors, errors
        finally:
            server.shutdown()
            worker.join()


def _drag_designer_widget(page, start, end):
    start.scroll_into_view_if_needed()
    end.scroll_into_view_if_needed()
    source_box, target_box = start.bounding_box(), end.bounding_box()
    assert source_box and target_box
    source_point = (source_box["x"] + 12, source_box["y"] + 12)
    target_point = (target_box["x"] + 12, target_box["y"] + 12)
    page.mouse.move(*source_point)
    page.mouse.down()
    page.mouse.move(*target_point, steps=18)
    page.mouse.move(target_point[0] + 2, target_point[1] + 2)
    page.mouse.up()


def test_page_designer_drops_palette_widget(page: Page, plan_server):
    page.set_viewport_size({"width": 1440, "height": 900})
    page.goto(plan_server)
    expect(page.locator(".iface-node")).to_have_count(2)
    page.wait_for_load_state("networkidle")
    page.locator(".iface-node", has_text="Saved").press("Enter")
    page.get_by_role("button", name="Design", exact=True).click()
    designer = page.get_by_role("region", name="Page designer")
    canvas = page.frame_locator('iframe[title="Design canvas"]')
    designer.get_by_role("button", name="Add row").click()
    row = canvas.locator(".row")
    expect(row).to_have_attribute("data-design-empty", "")
    _drag_designer_widget(page, designer.get_by_role("button", name="Add text"), row)
    expect(row.locator(".text")).to_have_count(1)


def test_page_designer_drops_where_the_pointer_is(page: Page, plan_server):
    page.set_default_timeout(8000)
    page.set_viewport_size({"width": 1440, "height": 900})
    page.goto(plan_server)
    expect(page.locator(".iface-node")).to_have_count(2)
    page.wait_for_load_state("networkidle")
    page.locator(".iface-node", has_text="Saved").press("Enter")
    page.get_by_role("button", name="Design", exact=True).click()
    designer = page.get_by_role("region", name="Page designer")
    canvas = page.frame_locator('iframe[title="Design canvas"]')
    heading = canvas.get_by_text("Customer saved", exact=True)
    # Dropping on the top of the heading puts the new text before it, not at the end of the page.
    _drag_designer_widget(page, designer.get_by_role("button", name="Add text"), heading)
    expect(canvas.get_by_text("Text", exact=True)).to_have_count(1)
    with page.expect_response(lambda response: response.request.method == "PATCH" and "/plan-items/" in response.url) as saved:
        designer.get_by_role("button", name="Save design").click()
    content = saved.value.request.post_data_json["content"]
    assert content.index('text "Text"') < content.index('heading "Customer saved"'), content


def test_page_designer_warns_when_leaving_with_unsaved_changes(page: Page, plan_server):
    page.set_default_timeout(8000)
    page.set_viewport_size({"width": 1440, "height": 900})
    page.goto(plan_server)
    expect(page.locator(".iface-node")).to_have_count(2)
    page.wait_for_load_state("networkidle")
    page.locator(".iface-node", has_text="Saved").press("Enter")
    page.get_by_role("button", name="Design", exact=True).click()
    designer = page.get_by_role("region", name="Page designer")
    designer.get_by_role("button", name="Add text").click()
    expect(designer).to_contain_text("Unsaved changes")
    # The designer draft is registered with the shared unsaved-edits check used when leaving Plan.
    assert page.evaluate("""async () => {
        const { hasUnsavedEdits } = await import('/static/development/planning/inline-edit/editor.js?v=20261103-ifaces3');
        return hasUnsavedEdits();
    }""")
    prevented = page.evaluate("""() => {
        const event = new Event('beforeunload', { cancelable: true });
        const ok = window.dispatchEvent(event);
        return event.defaultPrevented || event.returnValue === '' || ok === false;
    }""")
    assert prevented, 'leaving with unsaved designer edits should be cancelled'
    expect(designer).to_contain_text("Unsaved changes")


def test_page_designer_delete_undo_redo(page: Page, plan_server):
    page.set_default_timeout(8000)
    page.set_viewport_size({"width": 1280, "height": 900})
    page.goto(plan_server)
    expect(page.locator(".iface-node")).to_have_count(2)
    page.wait_for_load_state("networkidle")
    page.locator(".iface-node", has_text="Saved").press("Enter")
    page.get_by_role("button", name="Design", exact=True).click()
    designer = page.get_by_role("region", name="Page designer")
    canvas = page.frame_locator('iframe[title="Design canvas"]')
    undo = designer.get_by_role("button", name="Undo", exact=True)
    redo = designer.get_by_role("button", name="Redo", exact=True)
    delete = designer.get_by_role("button", name="Delete widget")
    expect(undo).to_be_disabled()
    designer.get_by_role("button", name="Add button").click()
    added = canvas.get_by_role("button", name="Button", exact=True)
    expect(added).to_have_count(1)
    delete.click()
    expect(added).to_have_count(0)
    undo.click()
    expect(added).to_have_count(1)
    redo.click()
    expect(added).to_have_count(0)
    # Keyboard: Ctrl+Z restores, Delete removes the selected widget again.
    designer.get_by_role("treeitem", selected=True).focus()
    page.keyboard.press("Control+z")
    expect(added).to_have_count(1)
    page.keyboard.press("Delete")
    expect(added).to_have_count(0)
    # Undoing everything returns to the saved stencil: nothing left to save.
    while undo.is_enabled():
        undo.click()
    expect(designer.get_by_role("button", name="Save design")).to_be_disabled()
    expect(designer.get_by_role("button", name="Discard", exact=True)).to_be_hidden()


def test_page_designer_move_and_revision_conflict(page: Page, plan_server, tmp_path):
    page.set_default_timeout(8000)
    page.set_viewport_size({"width": 1440, "height": 900})
    page.goto(plan_server)
    expect(page.locator(".iface-node")).to_have_count(2)
    page.get_by_role("button", name="Advanced · page source").click()
    source = page.get_by_role("textbox", name="Page source")
    original = source.input_value() + '''
screen "Services" route=/services
  heading "Services"
screen "Book" route=/book
  heading "Book"
'''
    source.fill(original)
    page.get_by_role("button", name="Apply", exact=True).click()
    expect(page.locator(".iface-node")).to_have_count(4)
    page.locator(".iface-node", has_text="Saved").press("Enter")
    page.get_by_role("button", name="Design", exact=True).click()
    designer = page.get_by_role("region", name="Page designer")
    canvas = page.frame_locator('iframe[title="Design canvas"]')
    add_row = designer.get_by_role("button", name="Add row")
    add_row.click()
    rows = canvas.locator(".row")
    expect(rows.first).to_have_attribute("data-design-empty", "")
    out = Path(os.environ.get("DECISIONS_DESIGN_EVIDENCE", str(tmp_path)))
    page.screenshot(path=out / "page-designer-empty-row.png", animations="disabled")

    designer.get_by_role("button", name="Add text").click()
    expect(rows.first.locator(".text")).to_have_count(1)
    designer.get_by_role("treeitem", name="content", exact=True).click()
    add_row.click()
    expect(rows.last).to_have_attribute("data-design-empty", "")
    _drag_designer_widget(page, rows.first.locator(".text"), rows.last)
    expect(rows.last.locator(".text")).to_have_count(1)
    expect(rows.first).to_have_attribute("data-design-empty", "")

    workspace = page.request.get(plan_server + "/api/workflows/studio/plan-workspaces/1").json()
    item = next(item for item in workspace["items"] if item["item_type"] == "wireframe")
    latest = original.replace('heading "Customer saved"', 'heading "Booking confirmed"')
    updated = page.request.patch(
        f"{plan_server}/api/workflows/studio/plan-items/{item['id']}",
        data={"content": latest, "expected_revision": item["revision_count"]},
    )
    assert updated.ok
    designer.get_by_role("button", name="Save design").click()
    expect(designer.get_by_role("status")).to_contain_text("newer revision")
    expect(rows.last.locator(".text")).to_have_count(1)
    page.screenshot(path=out / "page-designer-conflict.png", animations="disabled")
    designer.get_by_role("button", name="Discard", exact=True).click()
    expect(canvas.get_by_text("Booking confirmed", exact=True)).to_be_visible()
    expect(rows).to_have_count(0)
    page.get_by_role("button", name="Back to all pages").click()
    expect(page.locator(".iface-node")).to_have_count(4)
    page.locator(".iface-node", has_text="Services").press("Enter")
    expect(page.frame_locator('#plan-preview iframe').get_by_text("Services", exact=True)).to_be_visible()


@pytest.mark.parametrize("width", [390, 768, 1440])
def test_desktop_reopen_rejects_plan_from_previous_project(page: Page, plan_server, width):
    page.set_viewport_size({"width": width, "height": 900})
    page.goto(plan_server)
    initial = page.evaluate("""async () => {
        const {createPlanning} = await import('/static/development/planning/index.js');
        const api = async (path, options={}) => {
            if (options.method === 'POST' && options.body?.project_id === 1) {
                return api('/workflows/studio/plan-workspaces/1');
            }
            const response = await fetch('/api' + path, {...options,
                headers: {'Content-Type': 'application/json'},
                body: options.body ? JSON.stringify(options.body) : undefined});
            const data = await response.json();
            if (!response.ok) throw Object.assign(Error(typeof data.detail === 'string' ? data.detail : 'Request failed'), {status: response.status, data});
            return data;
        };
        const host = {api, escapeHtml: String, toast: () => {}, openHome: () => {}};
        const root = document.querySelector('#plan-root');
        const planner = createPlanning(host, root);
        const board = {key: 'decisions:1', name: 'Plan integration', provider: 'decisions'};
        const opened = await planner.openBoard({...board, project_id: 1}, [{id: 1}]);
        window.openMovedPlan = () => {
            const opening = planner.openBoard({...board, project_id: 2}, [{id: 2}]);
            const showedOldCanvas = Boolean(root.querySelector('.iface-canvas'));
            return opening.then(opened => ({opened, showedOldCanvas}));
        };
        return {opened, text: root.innerText.slice(0, 300)};
    }""")
    assert initial["opened"], initial["text"]
    expect(page.locator(".iface-canvas")).to_be_visible()
    assert page.evaluate("window.openMovedPlan()") == {"opened": False, "showedOldCanvas": False}
    expect(page.get_by_role("alert")).to_contain_text("This saved plan belongs to a different project (Plan integration)")
    expect(page.get_by_role("button", name="Start a new plan for this project")).to_be_visible()
    expect(page.locator(".iface-canvas")).to_have_count(0)
    page.screenshot(path=f"/tmp/decisions-desktop-plan-project-conflict-{width}.png")
    page.get_by_role("button", name="Back to Plans").click()
    expect(page.get_by_role("heading", name="Plans", exact=True)).to_be_visible()


@pytest.mark.parametrize(
    "size",
    [
        {"width": 1440, "height": 900},
        {"width": 1024, "height": 900},
        {"width": 390, "height": 844},
    ],
    ids=["desktop", "mid", "mobile"],
)
def test_prompt_linked_artifacts_and_repeated_build(page: Page, plan_server, size, tmp_path):
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.set_viewport_size(size)
    page.goto(plan_server)
    _json_messages(page)
    expect(page.get_by_role("button", name="Hide artifacts", exact=True)).to_have_count(0)
    expect(page.get_by_role("region", name="Artifacts", exact=True)).to_be_visible()
    expect(page.get_by_label("Model", exact=True)).to_have_value("fixture-model")
    expect(page.get_by_role("button", name="Build tickets", exact=True)).to_be_enabled()
    expect(page.get_by_role("button", name="Rebuild", exact=True)).to_be_visible()
    expect(page.locator('select[aria-label="Browse pages"]')).to_have_count(0)
    expect(page.locator('select[aria-label="Wireframe page"]')).to_have_count(0)
    expect(page.locator(".iface-canvas")).to_be_visible()
    expect(page.locator(".iface-node")).to_have_count(2)
    page.get_by_role("button", name="Project panels", exact=True).click()
    page.get_by_label("Message", exact=True).fill("Add an optional phone field and update its data and requirements.")
    with page.expect_response("**/plan-workspaces/1/messages") as response:
        page.get_by_role("button", name="Send message", exact=True).click()
    assert response.value.status == 200
    expect(page.get_by_text("Added the phone field to Customers.", exact=True)).to_be_visible()
    expect(page.locator(".plan-message-author [data-assistant-identity] .assistant-avatar-image").first).to_be_visible()
    expect(page.locator(".plan-message-author [data-assistant-name]").first).to_have_text("Oracle")
    page.locator(".plan-message-author").first.scroll_into_view_if_needed()
    page.screenshot(path=str(tmp_path / f"plan-oracle-{size['width']}x{size['height']}.png"), full_page=True)
    page.get_by_role("button", name="Data", exact=True).click()
    # erDiagrams open in the visual ERD canvas (ERD editor phase 1), not as a static Mermaid SVG.
    expect(page.locator(".erd-editor .erd-table").first).to_be_visible()
    expect(page.locator(".erd-editor .erd-nodes")).to_contain_text("phone")
    page.get_by_role("button", name="Documents", exact=True).click()
    expect(page.get_by_text("FR-002: Store an optional phone number.", exact=True)).to_be_visible()
    page.get_by_label("Message", exact=True).fill("Keep this draft")
    page.get_by_role("button", name="Interfaces", exact=True).click()
    expect(page.get_by_role("region", name="Artifacts", exact=True)).to_be_visible()
    expect(page.get_by_label("Message", exact=True)).to_have_value("Keep this draft")
    page.get_by_label("Message", exact=True).fill("")
    with page.expect_response("**/plan-workspaces/1/build") as first:
        page.get_by_role("button", name="Build tickets", exact=True).click()
    assert first.value.json()["created"] == 3
    task_list = page.get_by_role("list", name="Generated tasks", exact=True)
    expect(task_list).to_contain_text("Persist customer details")
    expect(task_list.locator("ul li")).to_have_count(2)
    page.reload()
    expect(task_list).to_contain_text("After #2")
    with page.expect_response("**/plan-workspaces/1/build") as repeated:
        page.get_by_role("button", name="Build tickets", exact=True).click()
    assert repeated.value.json()["created"] == 0
    assert repeated.value.json()["reused"] == 3
    assert [task["id"] for task in repeated.value.json()["tasks"]] == [task["id"] for task in first.value.json()["tasks"]]
    jump = page.get_by_role("button", name="View generated tickets", exact=True)
    expect(jump).to_be_visible(timeout=2500)
    jump.click()
    ticket = task_list.last.get_by_role("link", name="#1 Implement customer save", exact=True)
    expect(ticket).to_be_focused()
    assert ticket.evaluate("""link => {
        const row = link.getBoundingClientRect();
        const log = link.closest('#plan-messages').getBoundingClientRect();
        return row.top >= Math.max(log.top, 0) && row.bottom <= Math.min(log.bottom, innerHeight);
    }""")
    page.screenshot(
        path=str(tmp_path / f"plan-tasks-{size['width']}x{size['height']}.png"),
        full_page=True,
    )
    assert not errors


def test_plan_retry_after_post_and_recovery_read_are_lost(page: Page, plan_server):
    page.goto(plan_server)
    _json_messages(page)
    page.get_by_role("button", name="Project panels", exact=True).click()
    field = page.get_by_label("Message", exact=True)
    send = page.get_by_role("button", name="Send message", exact=True)
    expect(page.get_by_label("Model", exact=True)).to_have_value("fixture-model")
    requests = []
    lose_read = False

    def message_request(route):
        nonlocal lose_read
        requests.append(route.request.post_data_json)
        if len(requests) > 1:
            return route.continue_()
        response = route.fetch()
        assert response.ok
        assert response.json()["status"] == "complete"
        lose_read = True
        route.abort()

    def conversation_read(route):
        nonlocal lose_read
        if lose_read:
            lose_read = False
            return route.abort()
        route.continue_()

    page.route("**/plan-workspaces/1/messages", message_request)
    page.route("**/plan-workspaces/1/conversation*", conversation_read)
    prompt = "Add an optional phone field and update its data and requirements."
    field.fill(prompt)
    send.click()
    expect(page.locator("#plan-operation-status .plan-error")).to_be_visible()
    expect(field).to_have_value(prompt)
    send.click()
    expect(page.get_by_text("Added the phone field to Customers.", exact=True)).to_have_count(1)
    expect(field).to_have_value("")
    assert len(requests) == 2
    assert requests[0] == requests[1] and requests[0]["request_id"]
    response = page.request.get(plan_server + "/api/workflows/studio/plan-workspaces/1/conversation")
    turns = response.json()["messages"]
    assert [turn["role"] for turn in turns] == ["user", "assistant"]
    assert turns[0]["request_id"] == requests[0]["request_id"]


@pytest.mark.parametrize("committed", [False, True], ids=["never-received", "saved-response-lost"])
def test_plan_reload_recovers_the_original_submission(page: Page, plan_server, committed):
    page.set_viewport_size({"width": 1280, "height": 900})
    page.goto(plan_server)
    _json_messages(page)
    field = page.get_by_label("Message", exact=True)
    send = page.get_by_role("button", name="Send message", exact=True)
    expect(page.get_by_label("Model", exact=True)).to_have_value("fixture-model")
    if committed:
        page.get_by_role("button", name="Documents", exact=True).click()
    else:
        page.get_by_role("button", name="Project panels", exact=True).click()
        page.locator(".iface-node").first.press("Enter")
        expect(page.get_by_label("Back to all pages", exact=True)).to_be_visible()
    requests = []
    lose_read = False

    def message_request(route):
        nonlocal lose_read
        requests.append(route.request.post_data_json)
        if len(requests) > 1:
            return route.continue_()
        if committed:
            assert route.fetch().json()["status"] == "complete"
        lose_read = True
        route.abort()

    def conversation_read(route):
        nonlocal lose_read
        if lose_read:
            lose_read = False
            return route.abort()
        route.continue_()

    page.route("**/plan-workspaces/1/messages", message_request)
    page.route("**/plan-workspaces/1/conversation*", conversation_read)
    prompt = "Add an optional phone field and update its data and requirements."
    field.fill(prompt)
    send.click()
    expect(page.locator("#plan-operation-status .plan-error")).to_be_visible()
    page.reload()
    assert len(requests) == 1
    if committed:
        expect(page.get_by_role("button", name="Documents", exact=True)).to_have_attribute("aria-pressed", "true")
        expect(field).to_have_value("")
    else:
        expect(page.get_by_label("Back to all pages", exact=True)).to_be_visible()
        assert requests[0]["page_id"]
        expect(field).to_have_value(prompt)
        page.screenshot(path="/tmp/decisions-plan-reload-draft-1280.png")
        send.click()
        expect(field).to_have_value("")
        assert len(requests) == 2 and requests[0] == requests[1]
    expect(page.get_by_text("Added the phone field to Customers.", exact=True)).to_have_count(1)
    if committed:
        page.screenshot(path="/tmp/decisions-plan-reload-confirmed-1280.png")
    assert page.evaluate("Object.keys(sessionStorage).filter(key => key.startsWith('decisions.plan.draft.'))") == []
    if not committed:
        def inspect_map_request(route):
            requests.append(route.request.post_data_json)
            route.abort()

        page.route("**/plan-workspaces/1/messages", inspect_map_request)
        page.get_by_label("Back to all pages", exact=True).click()
        field.fill("Review all pages.")
        send.click()
        expect(page.locator("#plan-operation-status .plan-error")).to_be_visible()
        assert requests[-1]["page_id"] is None


def test_uploaded_source_and_pdf_preview(page: Page, plan_server):
    page.goto(plan_server)
    _json_messages(page)
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
