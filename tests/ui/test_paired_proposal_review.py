"""Drive App View with the native review document and isolated proposal routes."""
import base64
import hashlib
import json
import os
import re
from pathlib import Path

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient
from playwright.sync_api import expect

from distr.core.agent.services.integrations.google_workspace import GoogleWorkspaceConnector
from distr.core.incoming import media, proposal_sync, proposals
from distr.gui.web.routes.development.incoming import register_routes

pytestmark = pytest.mark.e2e_playwright
PREFIX = "/api/workflows/studio/incoming/proposals"
EVIDENCE = Path("/tmp/decisions-paired-proposal")


@pytest.mark.parametrize("width", [390, 768, 1280, 1440])
@pytest.mark.parametrize("mode", ["local-receipt", "upstream-retry", "mixed-intent"])
@pytest.mark.browser_context_args(has_touch=True)
def test_paired_incoming_resumes_saved_merge_without_starting_work(page, width, mode):
    import time
    from distr.core.incoming.telegram_updates import ACK_TIMEOUT_SECONDS, public_proposal_update_status

    app_view = os.environ.get("DECISIONS_REMOTE_APP_TEST_URL")
    if not app_view:
        pytest.skip("Set DECISIONS_REMOTE_APP_TEST_URL to the local App View development server.")
    page.set_viewport_size({"width": width, "height": 850})
    merge_key = "merge:" + "a" * 64
    recovery = {"chat_id": 17, "merge_key": merge_key, "title": "Merged delivery", "project_name": "DecisionsAI"}
    retry_upstream = mode == "upstream-retry"
    if mode == "mixed-intent":
        del recovery["chat_id"]
        recovery["local_recovery"] = True
    retry = {"replacement_id": 50, "original_ids": [40, 41]}
    upstream = {"id": "tensology:40", "title": "Reserved original", "project_name": "DecisionsAI",
                "source": "Gmail", "merge_retry": retry, "review_key": "c" * 64}
    writes = []
    unavailable = ["scope.txt"] if mode == "mixed-intent" else []
    telegram = public_proposal_update_status({"telegram_proposal_update": {
        "recipient_id": 12345, "pending": {"key": "original:done", "text": "Original result",
            "awaiting": {"token": "proposal-update:17:fixture", "state": "sending", "end": 15,
                         "at": time.time() - ACK_TIMEOUT_SECONDS - 1}},
    }}) if mode == "mixed-intent" else {}
    message = ("The saved merge sources changed. Refresh Incoming before retrying." if retry_upstream
               else "An original proposal changed after it was merged. Review the merge before resuming.")

    def relay(socket):
        def handle(raw):
            frame = json.loads(raw)["body"]
            if frame["type"] == "ping":
                socket.send(json.dumps({"type": "pong"}))
                return
            command = frame.get("command")
            data = {"screens": []}
            if command == "api_relay":
                request = frame["data"]
                path = request["path"]
                status, result = 200, {"items": [], "chats": []}
                if request["method"] == "POST" and path == "/api/chats/17/acknowledge-ready":
                    assert request.get("body") == {}
                    result = {"ready_acknowledged_at": "2026-11-12T12:00:00Z"}
                elif request["method"] == "POST":
                    writes.append((path, request.get("body")))
                    if len(writes) == 1:
                        status, result = 409, {"error": message}
                    else:
                        result = {"chat_id": 17, "review_key": "b" * 64, "success": True,
                                  "failed_attachments": [{"name": "scope.txt"}] if mode == "mixed-intent" else []}
                elif path.startswith(PREFIX + "?"):
                    result = {"proposals": [upstream], "pending_merges": []} if retry_upstream else {
                        "proposals": [], "pending_merges": [recovery],
                    }
                elif path.startswith(PREFIX + "/review?"):
                    result = {"status": "thread", "chat_id": 17}
                elif path == "/api/chats/17?surface=development":
                    result = {"title": "Merged delivery", "messages": [{"role": "assistant", "content": "Saved merge thread."}],
                              "unavailable_attachment_names": unavailable, "telegram_proposal_update_status": telegram}
                data = {"status": status, "response": result}
            socket.send(json.dumps({"type": "remote_control_response", "command": command,
                                    "request_id": frame.get("request_id"), "data": data}))
        socket.on_message(handle)

    page.route_web_socket("**/ws/telegram?*", relay)
    if mode == "mixed-intent":
        page.clock.install()
    page.goto(f"{app_view}?channel=paired-merge")
    page.get_by_title("App View", exact=True).click()
    page.get_by_role("button", name="Incoming", exact=True).click()
    page.get_by_role("tab", name="Proposals", exact=True).click()
    button = page.get_by_role("button", name="Retry merge" if retry_upstream else "Resume merge", exact=True)
    expect(button).to_be_visible()
    expect(page.get_by_text("Reserved original" if retry_upstream else "Merged delivery", exact=True)).to_be_visible()
    assert writes == []
    EVIDENCE.mkdir(exist_ok=True)
    kind = mode
    page.screenshot(path=EVIDENCE / f"{width}-{kind}-saved.png", animations="disabled")
    button.tap()
    expect(page.get_by_role("alert")).to_have_text(message)
    expect(button).to_be_enabled()
    page.screenshot(path=EVIDENCE / f"{width}-{kind}-held.png", animations="disabled")
    button.tap()
    expect(page.get_by_text("Saved merge thread.", exact=True)).to_be_visible()
    if mode == "mixed-intent":
        expect(page.get_by_role("status").filter(has_text="Files unavailable: scope.txt")).to_be_visible()
    expected = (PREFIX + "/merge", {"retry": retry}) if retry_upstream else (PREFIX + "/merge/17/resume", {"merge_key": merge_key})
    if mode == "mixed-intent":
        expected = (PREFIX + "/merge", {"local_recovery": merge_key})
    assert writes == [expected] * 2
    page.screenshot(path=EVIDENCE / f"{width}-{kind}-opened.png", animations="disabled")
    warning = page.get_by_role("status").filter(has_text="Files unavailable: scope.txt")
    delivery = page.locator(".av-telegram-update-status")
    if mode == "mixed-intent":
        page.reload()
        expect(page.get_by_text("Saved merge thread.", exact=True)).to_be_visible()
        expect(warning).to_be_visible()
        expect(delivery).to_have_text(telegram["message"])
        expect(delivery).to_have_attribute("data-state", "unconfirmed")
        assert writes == [expected] * 2
        page.screenshot(path=EVIDENCE / f"{width}-attachment-warning.png", animations="disabled")
        unavailable.clear()
        telegram = public_proposal_update_status({"telegram_proposal_update": {
            "recipient_id": 12345, "sent": "original:done",
            "pending": {"key": "original:done", "text": "Already delivered"},
        }})
        page.clock.fast_forward(16000)
        expect(delivery).to_have_text(telegram["message"])
    expect(warning).to_have_count(0)
    if mode == "mixed-intent":
        expect(delivery).to_have_text(telegram["message"])
        page.screenshot(path=EVIDENCE / f"{width}-attachment-warning-cleared.png", animations="disabled")
    else:
        expect(delivery).to_have_count(0)


@pytest.mark.parametrize("width", [390, 768, 1280, 1440])
@pytest.mark.parametrize("case", ["single", "merged", "reserved"])
def test_paired_app_view_uses_native_proposal_review(page, monkeypatch, tmp_path, width, case):
    app_view = os.environ.get("DECISIONS_REMOTE_APP_TEST_URL")
    if not app_view:
        pytest.skip("Set DECISIONS_REMOTE_APP_TEST_URL to the local App View development server.")
    page.set_default_timeout(7000)
    page.set_viewport_size({"width": width, "height": 850})
    proposal = {
        "id": "local:gmail:fixture", "source": "gmail", "source_thread_id": "fixture",
        "title": "Review delivery proposal", "description": "Review the request before starting work.",
        "project_id": 7, "project_name": "Decisions delivery", "message_ids": ["message-1"],
        "candidate_projects": [{"id": 7, "name": "Decisions delivery"}, {"id": 8, "name": "Operations"}],
        "tasks": ["Confirm the delivery window"], "updated_at": "2026-11-11T12:00:00",
        "evidence": [{"id": "message-1", "sender_name": "Operations", "body": "Delivery scope and timings"}],
    }
    merged = case == "merged"
    if case == "reserved":
        proposal.update(id="tensology:986123", origin="tensology", merge_source_reserved=True)
    if merged:
        proposal.update(merged_from=["first", "second"], message_ids=["message-1", "message-3"], source_scope=[
            {"source": "gmail", "source_thread_id": "fixture", "message_ids": ["message-1"], "participant": "Operations"},
            {"source": "gmail", "source_thread_id": "second", "message_ids": ["message-3"], "participant": "Dispatch"},
        ])
    proposal["review_key"] = key = proposals.proposal_review_key(proposal)
    original = b"Exact proposal attachment."
    file = tmp_path / "Delivery scope.txt"
    file.write_bytes(original)
    attachment = {"id": "a" * 24, "name": file.name, "mime_type": "text/plain",
                  "sha256": hashlib.sha256(original).hexdigest(), "path": str(file)}
    proposal["attachments"] = [{"id": attachment["id"], "name": attachment["name"]}]
    scope = {"snapshot": proposal, "chat_id": None}
    source_mode = {"available": False}
    launches = []
    requests = []
    held_launch = []
    held_source = []
    updates = []

    def resolve(review_key, *, submitted_only=False):
        if review_key != key:
            return {"status": "unavailable"}
        if scope["chat_id"]:
            return {"status": "thread", "chat_id": scope["chat_id"],
                    **({"proposal": scope["snapshot"]} if submitted_only else {})}
        if submitted_only:
            return {"status": "unavailable"}
        return {"status": "proposal", "proposal": scope["snapshot"]}

    def update(current, changes, *, expected_updated_at):
        assert expected_updated_at == current["updated_at"]
        scope["snapshot"] = {**current, **changes, "updated_at": "2026-11-11T12:01:00"}
        return scope["snapshot"]

    def launch(snapshot, *, start_work, require_review):
        assert start_work and require_review and snapshot["task_completed_indexes"] == [0] and snapshot["project_id"] == 8
        if scope.get("attachment_unavailable"):
            raise proposals.ReviewedMergeAttachmentUnavailable([attachment["name"]])
        launches.append(snapshot)
        scope["chat_id"] = 17
        return 17

    monkeypatch.setattr(proposals, "resolve_proposal_review", resolve)
    monkeypatch.setattr(proposals, "list_incoming_proposals", lambda **kwargs: {
        "proposals": [scope["snapshot"]] if not scope["chat_id"] else [],
    })
    monkeypatch.setattr(proposal_sync, "update_proposal", update)
    monkeypatch.setattr(proposals, "reassign_proposal_project", lambda current, project_id: update(
        current, {"project_id": project_id, "project_name": "Operations"}, expected_updated_at=current["updated_at"],
    ))
    monkeypatch.setattr(proposals, "create_proposal_thread", launch)
    monkeypatch.setattr(proposals, "proposal_thread_attachment_report", lambda _: {})
    monkeypatch.setattr(media, "ensure_proposal_attachments", lambda *args, **kwargs: {
        "attachments": [attachment], "failures": [],
    })
    monkeypatch.setattr(media, "resolve_proposal_managed_attachment", lambda *args: (file, attachment))
    monkeypatch.setattr(GoogleWorkspaceConnector, "__init__", lambda self: None)
    monkeypatch.setattr(GoogleWorkspaceConnector, "get_email_thread", lambda self, thread: [
        {"id": "message-1", "threadId": "second", "subject": "Dispatch conversation", "body": "Dispatch context only"},
        {"id": "message-3", "threadId": "second", "subject": "Dispatch conversation", "body": "Dispatch original"},
    ] if thread == "second" and source_mode["available"] else [
        {"id": "message-1", "threadId": "unrelated", "subject": "Unrelated", "body": "Unrelated newer request"},
        *([
            {"id": "message-1", "threadId": thread, "subject": "Delivery conversation", "body": "Exact source message"},
            {"id": "message-2", "threadId": thread, "subject": "Delivery conversation", "body": "Later message in the same thread"},
        ] if source_mode["available"] else []),
    ])
    api_app, router = FastAPI(), APIRouter()
    register_routes(router, None)
    api_app.include_router(router, prefix="/api")
    client = TestClient(api_app)

    def relay(socket):
        def handle(raw):
            frame = json.loads(raw)["body"]
            if frame["type"] == "ping":
                socket.send(json.dumps({"type": "pong"}))
                return
            command = frame.get("command")
            reply = {"type": "remote_control_response", "command": command,
                     "request_id": frame.get("request_id"), "data": {}}
            if command == "list_screens":
                reply["data"] = {"screens": []}
            if command == "api_relay":
                data = frame["data"]
                path = data["path"]
                requests.append((data["method"], path))
                if path == PREFIX + "/thread":
                    held_launch.append((socket, reply, data["body"]))
                    return
                if path in {PREFIX + "/details", PREFIX + "/project"}:
                    assert data["body"]["reviewed_proposal"] == scope["snapshot"]
                if path == "/api/chats/17?surface=development":
                    reply["data"] = {"status": 200, "response": {
                        "title": "Linked Development thread", "messages": [
                            {"role": "assistant", "content": "Review started on this thread."},
                            *updates,
                        ],
                    }}
                elif path.startswith(PREFIX):
                    headers = {"Range": "bytes=0-1048575"} if data.get("response_type") == "base64" else {}
                    response = client.request(data["method"], path, json=data.get("body"), headers=headers)
                    result = ({"body_base64": base64.b64encode(response.content).decode(),
                               "content_range": response.headers["content-range"], "etag": response.headers["etag"],
                               "content_type": response.headers["content-type"]}
                              if response.status_code == 206 else response.json())
                    reply["data"] = {"status": response.status_code, "response": result}
                    if source_mode.get("hold") and path.startswith(PREFIX + "/source?"):
                        held_source.append((socket, reply))
                        return
                else:
                    reply["data"] = {"status": 200, "response": {"items": [], "chats": []}}
            socket.send(json.dumps(reply))
        socket.on_message(handle)

    page.route_web_socket("**/ws/telegram?*", relay)
    base = f"{app_view}?channel=paired-test"
    EVIDENCE.mkdir(exist_ok=True)
    page.goto(base)
    page.get_by_title("App View", exact=True).click()
    page.get_by_role("button", name="Incoming", exact=True).click()
    page.get_by_role("tab", name="Proposals", exact=True).click()
    opener = page.get_by_role("button", name=re.compile(r"^Review delivery proposal\b"))
    expect(opener).to_be_visible()
    page.screenshot(path=EVIDENCE / f"{width}-list.png", animations="disabled")
    opener.click()
    expect(page).to_have_url(f"{base}&proposal_key={key}")
    frame = page.frame_locator('iframe[title="Development proposal review"]')
    dialog = frame.get_by_role("dialog", name="Review delivery proposal")
    expect(dialog).to_be_visible()
    assert page.locator('iframe[title="Development proposal review"]').get_attribute("sandbox") == "allow-scripts allow-downloads"
    if case == "reserved":
        expect(dialog).to_contain_text("Reserved for a merge. Retry with its original partner.")
        expect(dialog.get_by_label("Project", exact=True)).to_be_disabled()
        expect(dialog.get_by_role("button", name="Create Thread", exact=True)).to_be_disabled()
        assert not held_launch
        page.screenshot(path=EVIDENCE / f"{width}-reserved-review.png", animations="disabled")
        return
    dialog.get_by_label("Project", exact=True).select_option("8")
    expect(dialog.locator("#proposal-project-status")).to_have_text("Saved")
    page.screenshot(path=EVIDENCE / f"{width}-review.png", animations="disabled")
    dialog.get_by_role("tab", name="Source").click()
    expect(dialog.get_by_text("Delivery scope and timings")).to_be_visible()
    view_source = dialog.get_by_role("button", name="View in conversation", exact=True)
    original_message = page.locator("article").filter(has_text="Original message")
    if merged:
        view_source.click()
        picker = dialog.get_by_label("Original conversation", exact=True)
        expect(picker).to_be_focused()
        expect(view_source).to_be_disabled()
        page.screenshot(path=EVIDENCE / f"{width}-merged-review-picker.png", animations="disabled")
        picker.select_option(label="Gmail · Dispatch")
        view_source.click()
        expect(dialog.get_by_text("The original message is no longer available in this conversation.", exact=True)).to_be_visible()
        expect(view_source).to_be_focused()
        page.screenshot(path=EVIDENCE / f"{width}-merged-review-unavailable.png", animations="disabled")
        source_mode["available"] = True
        view_source.click()
        expect(original_message.get_by_text("Dispatch original", exact=True)).to_be_visible()
        expect(original_message).to_be_focused()
        expect(page.get_by_text("Dispatch context only", exact=True)).to_be_visible()
        expect(page.get_by_text("Exact source message", exact=True)).to_have_count(0)
        page.screenshot(path=EVIDENCE / f"{width}-merged-review-reader.png", animations="disabled")
        page.get_by_role("button", name="Back", exact=True).click()
        expect(dialog.get_by_role("tab", name="Source")).to_have_attribute("aria-selected", "true")
    else:
        check_single_pending_source(page, dialog, view_source, original_message, source_mode, held_source, width, opener)
    dialog.get_by_role("tab", name="Details").click()
    expect(dialog.get_by_label("Project", exact=True)).to_have_value("8")
    expect(page).to_have_url(f"{base}&proposal_key={key}")
    dialog.get_by_role("tab", name="Files").click()
    file_button = dialog.get_by_role("button", name="Delivery scope", exact=False)
    expect(file_button).to_be_visible()
    if not merged:
        page.screenshot(path=EVIDENCE / f"{width}-files.png", animations="disabled")
    file_button.click()
    preview = frame.get_by_role("dialog", name="Delivery scope")
    expect(preview.get_by_text(original.decode())).to_be_visible()
    with page.expect_download() as downloaded:
        preview.get_by_role("link", name="Download").click()
    assert downloaded.value.path().read_bytes() == original
    preview.get_by_role("button", name="Close", exact=True).click()
    expect(file_button).to_be_focused()
    dialog.get_by_role("tab", name="Tasks").click()
    dialog.get_by_role("checkbox", name="Mark task done").click()
    expect(dialog.get_by_role("checkbox", name="Mark task not done")).to_be_checked()
    dialog.get_by_role("button", name="Create Thread", exact=True).click()
    expect(frame.get_by_role("dialog", name="Preparing thread...")).to_be_visible()
    expect(frame.get_by_role("button", name="Return to proposals")).to_be_visible()
    assert len(held_launch) == 1
    socket, reply, body = held_launch.pop()
    if merged:
        scope["snapshot"] = {**scope["snapshot"], "tasks": [*scope["snapshot"]["tasks"], "Confirm revised dispatch"]}
        rejected = client.post(PREFIX + "/thread", json=body)
        assert rejected.status_code == 409 and not launches
        socket.send(json.dumps({**reply, "data": {"status": rejected.status_code, "response": rejected.json()}}))
        expect(page.get_by_role("alert")).to_contain_text("The linked proposal changed.")
        page.screenshot(path=EVIDENCE / f"{width}-changed-before-start.png", animations="disabled")
        page.get_by_role("button", name="Reopen proposal").click()
        dialog.get_by_role("tab", name="Tasks").click()
        expect(dialog.get_by_text("Confirm revised dispatch", exact=True)).to_be_visible()
        dialog.get_by_role("button", name="Create Thread", exact=True).click()
        expect(frame.get_by_role("dialog", name="Preparing thread...")).to_be_visible()
        assert len(held_launch) == 1
        socket, reply, body = held_launch.pop()
        scope["attachment_unavailable"] = True
        blocked = client.post(PREFIX + "/thread", json=body)
        assert blocked.status_code == 409 and not launches
        socket.send(json.dumps({**reply, "data": {"status": blocked.status_code, "response": blocked.json()}}))
        expect(page.get_by_role("alert")).to_contain_text("Restore the reviewed attachment files and retry: Delivery scope.txt")
        page.screenshot(path=EVIDENCE / f"{width}-merge-attachment-blocked.png", animations="disabled")
        scope["attachment_unavailable"] = False
        page.get_by_role("button", name="Reopen proposal").click()
        dialog.get_by_role("button", name="Create Thread", exact=True).click()
        expect(frame.get_by_role("dialog", name="Preparing thread...")).to_be_visible()
        assert len(held_launch) == 1
        socket, reply, body = held_launch.pop()
    started = client.post(PREFIX + "/thread", json=body)
    socket.send(json.dumps({**reply, "data": {"status": started.status_code, "response": started.json()}}))
    expect(page.get_by_text("Review started on this thread.")).to_be_visible()
    expect(page.get_by_placeholder("Type a message...")).to_have_count(0)
    thread_source = page.get_by_role("button", name="View original conversation", exact=True)
    expect(thread_source).to_be_visible()
    assert len(launches) == 1
    page.reload()
    expect(page.get_by_text("Review started on this thread.")).to_be_visible()
    if merged:
        thread_source.click()
        picker = page.get_by_label("Original conversation", exact=True)
        expect(picker).to_be_focused()
        expect(thread_source).to_be_disabled()
        page.screenshot(path=EVIDENCE / f"{width}-merged-thread-picker.png", animations="disabled")
        picker.select_option(label="Gmail · Operations")
        thread_source.click()
        expect(original_message.get_by_text("Exact source message", exact=True)).to_be_visible()
        expect(page.get_by_text("Dispatch context only", exact=True)).to_have_count(0)
        page.screenshot(path=EVIDENCE / f"{width}-merged-thread-first.png", animations="disabled")
        page.get_by_role("button", name="Back", exact=True).click()
        expect(thread_source).to_be_focused()
        picker.select_option(label="Gmail · Dispatch")
        thread_source.click()
        expect(original_message.get_by_text("Dispatch original", exact=True)).to_be_visible()
        expect(page.get_by_text("Exact source message", exact=True)).to_have_count(0)
        page.get_by_role("button", name="Back", exact=True).click()
        expect(picker.locator("option:checked")).to_have_text("Gmail · Dispatch")
        page.screenshot(path=EVIDENCE / f"{width}-merged-thread-return.png", animations="disabled")
    else:
        check_single_submitted_source(page, thread_source, original_message, source_mode, held_source, updates, width, base, key)
    assert ("GET", f"{PREFIX}/source?review_key={key}&chat_id=17") in requests
    assert requests.count(("POST", PREFIX + "/thread")) == (3 if merged else 1)
    page.goto(f"{base}&proposal_key={'f' * 64}")
    expect(page.get_by_role("alert").get_by_text("This proposal link is no longer available.")).to_be_visible()
    expect(page.get_by_role("alert")).to_be_focused()


def check_single_pending_source(page, dialog, view_source, original_message, source_mode, held_source, width, opener):
    page.screenshot(path=EVIDENCE / f"{width}-source.png", animations="disabled")
    view_source.click()
    expect(dialog.get_by_text("The original message is no longer available in this conversation.", exact=True)).to_be_visible()
    expect(view_source).to_be_focused()
    page.screenshot(path=EVIDENCE / f"{width}-source-unavailable.png", animations="disabled")
    source_mode["available"] = True
    source_mode["hold"] = True
    view_source.click()
    expect(dialog.get_by_text("Opening the original conversation…", exact=True)).to_be_visible()
    page.screenshot(path=EVIDENCE / f"{width}-source-loading.png", animations="disabled")
    expect(view_source).to_be_disabled()
    dialog.get_by_role("button", name="Close", exact=True).last.click()
    expect(dialog).to_have_count(0)
    assert len(held_source) == 1
    source_socket, source_reply = held_source.pop()
    source_socket.send(json.dumps(source_reply))
    source_mode["hold"] = False
    expect(opener).to_be_visible()
    expect(page.get_by_text("Exact source message", exact=True)).to_have_count(0)
    opener.click()
    dialog.get_by_role("tab", name="Source").click()
    view_source.click()
    expect(original_message.get_by_text("Exact source message", exact=True)).to_be_visible()
    expect(original_message).to_be_focused()
    expect(page.get_by_text("Later message in the same thread", exact=True)).to_be_visible()
    expect(page.get_by_text("Unrelated newer request", exact=True)).to_have_count(0)
    page.screenshot(path=EVIDENCE / f"{width}-original-conversation.png", animations="disabled")
    page.get_by_role("button", name="Back", exact=True).click()
    expect(dialog).to_be_visible()
    expect(dialog.get_by_role("tab", name="Source")).to_have_attribute("aria-selected", "true")
    page.screenshot(path=EVIDENCE / f"{width}-source-return.png", animations="disabled")


def check_single_submitted_source(page, thread_source, original_message, source_mode, held_source, updates, width, base, key):
    page.screenshot(path=EVIDENCE / f"{width}-submitted-thread.png", animations="disabled")
    source_mode["available"] = False
    thread_source.click()
    expect(page.get_by_role("alert")).to_have_text("The original message is no longer available in this conversation.")
    expect(thread_source).to_be_focused()
    page.screenshot(path=EVIDENCE / f"{width}-submitted-unavailable.png", animations="disabled")
    source_mode.update(available=True, hold=True)
    thread_source.click()
    expect(page.get_by_role("status")).to_have_text("Opening the original conversation…")
    expect(thread_source).to_have_attribute("aria-disabled", "true")
    page.screenshot(path=EVIDENCE / f"{width}-submitted-loading.png", animations="disabled")
    page.get_by_role("button", name="Back", exact=True).click()
    expect(thread_source).to_have_count(0)
    assert len(held_source) == 1
    source_socket, source_reply = held_source.pop()
    source_socket.send(json.dumps(source_reply))
    source_mode["hold"] = False
    expect(page.get_by_text("No pending proposals.", exact=True)).to_be_visible()
    expect(page.get_by_text("Exact source message", exact=True)).to_have_count(0)
    updates.extend({"role": "assistant", "content": f"Checked delivery item {index}."} for index in range(1, 21))
    page.goto(f"{base}&proposal_key={key}")
    transcript = page.locator(".av-messages")
    expect(transcript.get_by_text("Checked delivery item 20.", exact=True)).to_be_attached()
    transcript.evaluate("(element) => { element.scrollTop = 160; }")
    assert transcript.evaluate("(element) => element.scrollTop") == 160
    thread_source.click()
    expect(original_message).to_be_focused()
    expect(original_message.get_by_text("Exact source message", exact=True)).to_be_visible()
    expect(page.get_by_text("Unrelated newer request", exact=True)).to_have_count(0)
    page.screenshot(path=EVIDENCE / f"{width}-submitted-original.png", animations="disabled")
    page.get_by_role("button", name="Back", exact=True).click()
    expect(transcript.get_by_text("Review started on this thread.")).to_be_attached()
    expect(thread_source).to_be_focused()
    assert transcript.evaluate("(element) => element.scrollTop") == 160
    page.screenshot(path=EVIDENCE / f"{width}-submitted-return.png", animations="disabled")
