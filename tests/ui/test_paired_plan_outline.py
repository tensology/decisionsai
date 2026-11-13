"""Exercise paired outline editing through the actual scoped Plan routes."""
import json
import os
from pathlib import Path

import pytest
from playwright.sync_api import expect

from distr.core.planning import service
from tests.core.test_planning_workspace import plan_workspace_db, scoped_outline_plan  # noqa: F401

pytestmark = pytest.mark.e2e_playwright
SHOTS = Path("/tmp/decisions-paired-plan-outline")


@pytest.mark.parametrize(("width", "mode"), [(390, "empty"), (768, "empty"), (1280, "empty"),
                                             (1440, "empty"), (390, "placeholder"), (390, "legacy"),
                                             (390, "missing"), (390, "reassigned"), (390, "mismatch")])
def test_paired_plan_creates_edits_and_reviews_a_conflict(page, scoped_outline_plan, monkeypatch, width, mode):
    url = os.environ.get("DECISIONS_REMOTE_APP_TEST_URL")
    if not url:
        pytest.skip("Set DECISIONS_REMOTE_APP_TEST_URL to the local App View server.")
    client, root, scope, headers = scoped_outline_plan
    workspace_id = int(root.rsplit("/", 1)[1])
    project_id, board_id = scope["project_id"], scope["board_id"]
    if mode == "empty":
        service.create_item(workspace_id=workspace_id, item_type="prd", content_format="markdown",
                            content="# Devices requirements\n\nPreserve device names during activation.")
    initial_flow = None
    if mode == "placeholder":
        initial_flow = service.create_item(workspace_id=workspace_id, item_type="flows", content_format="wire",
                                           content='screen "No routes found" device=desktop\n  stack\n    heading "No route table detected"\n')
    label = f"{width}-with-placeholder" if mode == "placeholder" else str(width)
    sitemap = {"nodes": [
        {"kind": "page", "id": "devices", "label": "Devices", "route": "/devices"},
        {"kind": "page", "id": "settings", "label": "Settings", "route": "/settings"},
    ], "edges": []}
    monkeypatch.setattr(service, "_workspace_sitemap", lambda *_: sitemap)
    writes = []
    list_reads = []
    scoped_reads = []
    presence_responses = []
    reassigned = False

    def relay(socket):
        def handle(raw):
            frame = json.loads(raw)["body"]
            if frame["type"] == "ping":
                socket.send(json.dumps({"type": "pong"}))
                return
            command = frame["command"]
            if mode == "legacy" and command == "remote_presence" and not presence_responses:
                presence_responses.append((socket, frame["request_id"]))
                return
            data = {"screens": []}
            if command == "api_relay":
                call = frame["data"]
                path, method = call["path"], call["method"]
                response, status = {}, 200
                if path == "/api/tickets/boards":
                    selected_project = project_id + 1 if reassigned else project_id
                    name = "Moved phone plan" if reassigned else "Phone plan"
                    response = [{"id": board_id, "name": name, "default_project_id": selected_project},
                                {"id": board_id + 1, "name": "Another board", "default_project_id": project_id}]
                elif path == "/api/workflows/studio/plan-workspaces":
                    list_reads.append(path)
                    workspace_project = project_id + 1 if mode == "mismatch" else project_id
                    response = {"items": [
                        *([] if mode == "missing" else [{"id": workspace_id, "board_key": f"decisions:{board_id}", "project_id": workspace_project}]),
                        {"id": workspace_id + 1, "board_key": f"decisions:{board_id + 1}", "project_id": project_id},
                    ]}
                elif path.startswith(root + "/conversation?"):
                    scoped_reads.append(path)
                    messages = [{"role": "user", "content": "Keep the settings page."}]
                    if width == 768:
                        messages.extend({"role": "assistant", "content": f"Plan update {index}"}
                                        for index in range(1, 18))
                    response = {"messages": messages}
                elif path.startswith(root):
                    if method == "GET":
                        scoped_reads.append(path)
                    result = client.request(method, path, headers=headers, json=call.get("body"))
                    response, status = result.json(), result.status_code
                    if mode == "legacy" and method == "GET":
                        response.pop("outline_editing", None)
                    if method in {"POST", "PATCH"}:
                        writes.append((method, status))
                data = {"status": status, "response": response}
            socket.send(json.dumps({"type": "remote_control_response", "command": command,
                                    "request_id": frame["request_id"], "data": data}))
        socket.on_message(handle)

    page.route_web_socket("**/ws/telegram?*", relay)
    page.set_viewport_size({"width": width, "height": 900})
    page.goto(f"{url}?channel=paired-test")
    page.get_by_title("App View", exact=True).click()
    nav = page.get_by_role("navigation", name="App View", exact=True)
    expect(nav.get_by_role("button", name="Plan", exact=True)).to_have_count(0)
    nav.get_by_role("button", name="Boards", exact=True).click()
    page.get_by_role("button", name="Phone plan", exact=True).click()
    board_plan = page.get_by_role("button", name="Plan for Phone plan", exact=True)
    expect(board_plan).to_be_visible()
    SHOTS.mkdir(exist_ok=True)
    if mode == "empty":
        page.screenshot(path=SHOTS / f"{width}-board-plan-entry.png", animations="disabled")
    if mode == "legacy":
        socket, request_id = presence_responses[0]
        socket.send(json.dumps({"type": "client_disconnected"}))
    reassigned = mode == "reassigned"
    board_plan.click()
    if mode == "legacy":
        expect(page.get_by_role("status", name="Waiting for the Desktop connection")).to_be_visible()
        assert not list_reads
        page.screenshot(path=SHOTS / f"{width}-waiting-for-desktop.png", animations="disabled")
        socket.send(json.dumps({"type": "remote_control_response", "command": "remote_presence",
                                "request_id": request_id, "data": {}}))
    unavailable = {"missing": "This board has no saved plan yet.",
                   "reassigned": "This board or its project link changed.",
                   "mismatch": "Review the saved plan’s project assignment on Desktop."}
    if mode in unavailable:
        expect(page.get_by_role("status")).to_contain_text(unavailable[mode])
        expect(page.get_by_text("Another board", exact=True)).to_have_count(0)
        assert not scoped_reads and not writes
        page.screenshot(path=SHOTS / f"{width}-board-plan-{mode}.png", animations="disabled")
        page.get_by_role("button", name="Back", exact=True).click()
        if mode == "reassigned":
            moved_plan = page.get_by_role("button", name="Plan for Moved phone plan", exact=True)
            expect(moved_plan).to_be_focused()
            page.screenshot(path=SHOTS / f"{width}-board-plan-recovered.png", animations="disabled")
            moved_plan.click()
            expect(page.get_by_role("status")).to_contain_text(unavailable["mismatch"])
            assert not scoped_reads and not writes
        else:
            expect(board_plan).to_be_focused()
        return
    picker = page.get_by_role("searchbox", name="Find page")
    expect(picker).to_be_visible()
    assert len(scoped_reads) == 2 and all(f"board_id={board_id}" in path and f"project_id={project_id}" in path for path in scoped_reads)
    if mode == "legacy":
        picker.fill("/devices")
        page.get_by_role("button", name="Devices /devices", exact=True).click()
        expect(page.get_by_role("heading", name="Devices", exact=True)).to_be_visible()
        expect(page.get_by_role("button", name="Open page outline")).to_have_count(0)
        assert not writes
        page.screenshot(path=SHOTS / f"{width}-legacy-outline-read.png", animations="disabled")
        return

    def open_outline(label, route):
        picker.fill(route)
        page.get_by_role("button", name=f"{label} {route}", exact=True).click()
        page.get_by_role("button", name="Open page outline", exact=True).click()
        dialog = page.get_by_role("dialog", name=label)
        expect(dialog).to_be_visible()
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        return dialog

    devices = open_outline("Devices", "/devices")
    source = devices.get_by_label("Outline source")
    description = devices.get_by_label("Page description")
    expect(description).to_have_value("")
    expect(source).to_be_hidden()
    description.fill("Show connected devices.\n\nKeep the current search.")
    page.screenshot(path=SHOTS / f"{label}-new-outline.png", animations="disabled")
    devices.get_by_text("Advanced source", exact=True).click()
    expect(source).to_have_value('screen "Devices" id=page-devices route="/devices"\n  #| Show connected devices.\n  #|\n  #| Keep the current search.\n  heading "Devices"')
    devices.get_by_role("button", name="Save outline").click()
    expect(page.get_by_text("Outline saved.", exact=True)).to_be_visible()
    settings = open_outline("Settings", "/settings")
    settings.get_by_role("button", name="Save outline").click()
    expect(page.get_by_text("Outline saved.", exact=True)).to_be_visible()
    devices = open_outline("Devices", "/devices")
    source = devices.get_by_label("Outline source")
    expect(devices.get_by_label("Page description")).to_have_value("Show connected devices.\n\nKeep the current search.")
    devices.get_by_text("Advanced source", exact=True).click()
    initial = source.input_value()
    source.fill(initial + "\n  invalid")
    devices.get_by_role("button", name="Save outline").click()
    expect(devices.get_by_role("alert")).to_contain_text("Invalid wireframe")
    draft = initial.replace('heading "Devices"', 'heading "My draft"')
    source.fill(draft)
    devices.get_by_role("button", name="Close", exact=True).last.click()
    page.get_by_role("button", name="Back", exact=True).click()
    expect(board_plan).to_be_focused()
    board_plan.click()
    picker.fill("/devices")
    page.get_by_role("button", name="Devices /devices", exact=True).click()
    page.get_by_role("button", name="Open page outline", exact=True).click()
    devices.get_by_text("Advanced source", exact=True).click()
    expect(source).to_have_value(draft)
    item = next(row for row in service.get_workspace(workspace_id)["items"] if row["item_type"] == "wireframe")
    service.update_item(item["id"], content=item["content"].replace('heading "Settings"', 'heading "New settings"'),
                        expected_revision=item["revision_count"])
    devices.get_by_role("button", name="Save outline").click()
    expect(devices.get_by_role("alert")).to_contain_text("newer revision")
    expect(source).to_have_value(draft)
    devices.get_by_role("button", name="Review latest").click()
    expect(devices.get_by_text("Latest saved outline", exact=True)).to_be_visible()
    page.screenshot(path=SHOTS / f"{label}-outline-conflict.png", animations="disabled")
    devices.get_by_role("button", name="Keep draft on latest revision").click()
    expect(source).to_have_value(draft)
    devices.get_by_role("button", name="Save outline").click()
    expect(page.get_by_text("Outline saved.", exact=True)).to_be_visible()
    expect(page.get_by_text("Outline saved.", exact=True)).to_be_in_viewport()
    items = service.get_workspace(workspace_id)["items"]
    saved = next(row for row in items if row["id"] == item["id"])
    assert 'heading "My draft"' in saved["content"]
    assert '  #| Show connected devices.\n  #|\n  #| Keep the current search.\n' in saved["content"]
    assert 'heading "New settings"' in saved["content"]
    assert saved["revision_count"] == 4
    assert writes == [("POST", 200), ("PATCH", 200), ("PATCH", 422), ("PATCH", 409), ("PATCH", 200)]
    if initial_flow:
        unchanged = next(row for row in items if row["id"] == initial_flow["id"])
        assert unchanged["content"] == initial_flow["content"]
        assert unchanged["revision_count"] == initial_flow["revision_count"]
    page.screenshot(path=SHOTS / f"{label}-outline-saved.png", animations="disabled")
    note = page.get_by_text("Continue the Plan conversation on Desktop.", exact=True)

    def expect_visible_conversation_note():
        if width < 561:
            note.scroll_into_view_if_needed()
        expect(note).to_be_in_viewport(ratio=1)
        note_box = note.bounding_box()
        footer_box = page.locator(".voice-buttons-container.horizontal").bounding_box()
        assert note_box["y"] + note_box["height"] <= footer_box["y"] + 1

    expect_visible_conversation_note()
    if width == 768:
        latest = page.get_by_text("Plan update 17", exact=True)
        latest.scroll_into_view_if_needed()
        expect(latest).to_be_in_viewport(ratio=1)
        expect_visible_conversation_note()
        page.screenshot(path=SHOTS / f"{label}-outline-conversation-end.png", animations="disabled")
    if mode == "empty":
        page.get_by_role("tab", name="Data", exact=True).click()
        expect(page.get_by_text("No data yet", exact=True)).to_be_visible()
        expect_visible_conversation_note()
        page.screenshot(path=SHOTS / f"{label}-plan-data.png", animations="disabled")
        documents = page.get_by_role("tab", name="Documents", exact=True)
        documents.click()
        expect(documents).to_have_attribute("aria-selected", "true")
        expect(page.get_by_role("heading", name="Devices requirements")).to_be_visible()
        expect_visible_conversation_note()
        page.screenshot(path=SHOTS / f"{label}-plan-document.png", animations="disabled")
        page.get_by_role("button", name="Hide artifacts", exact=True).click()
        expect(page.get_by_role("tabpanel")).to_have_count(0)
        page.get_by_role("button", name="Show artifacts", exact=True).click()
        expect(page.get_by_role("heading", name="Devices requirements")).to_be_visible()
