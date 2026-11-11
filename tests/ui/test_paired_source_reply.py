"""The remote browser uses the desktop's reply review and durable send state."""
import json
import os
from pathlib import Path

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient
from playwright.sync_api import expect

from distr.core.db import Chat
from distr.core.incoming import source_reply as replies
from distr.gui.web.routes.development.thread_tools import register_routes
from tests.core.test_proposal_continuity_integration import intake  # noqa: F401

pytestmark = pytest.mark.e2e_playwright
EVIDENCE = Path("/tmp/decisions-paired-source-reply")


@pytest.mark.parametrize("width", [390, 768, 1280, 1440])
def test_paired_reply_keeps_definite_no_send_and_unknown_outcomes_distinct(page, monkeypatch, intake, width):
    app_view = os.environ.get("DECISIONS_REMOTE_APP_TEST_URL")
    if not app_view:
        pytest.skip("Set DECISIONS_REMOTE_APP_TEST_URL to the local App View development server.")
    session, proposal, projects = intake
    with session() as db:
        root = Chat(title="Linked Development thread", project_id=projects[0],
                    params=json.dumps({"incoming_proposal_handoff": {"proposal": proposal}}))
        db.add(root)
        db.flush()
        response = Chat(parent_id=root.id, response="The delivery check is complete.")
        next_response = Chat(parent_id=root.id, response="The next delivery check is complete.")
        db.add_all([response, next_response])
        db.commit()
        chat_id, response_id, next_response_id = root.id, response.id, next_response.id

    monkeypatch.setattr(replies, "resolve_target", lambda source: {
        "channel": "gmail", "from": "delivery@example.test", "to": ["operations@example.test"],
        "cc": ["dispatch@example.test"], "subject": "Re: Delivery window",
        "thread_id": source["thread_id"], "message_id": "message-a",
        "source_message_ids": ["message-a"],
    })
    generations = []

    def generate(*args, **kwargs):
        generations.append(args)
        return "The delivery check is complete.", {}

    monkeypatch.setattr(replies, "generate_draft", generate)
    mode = {"value": "preflight", "response_id": response_id}
    attempts = []
    deliveries = []

    def send(target, text, draft_id):
        attempts.append((target, text, draft_id))
        if mode["value"] == "preflight":
            raise replies.ReplyPreflightError("Nothing was sent. Reconnect Gmail before trying again.")
        if mode["value"] == "unknown":
            raise TimeoutError("Provider confirmation unavailable")
        deliveries.append((target, text, draft_id))

    monkeypatch.setattr(replies, "send_reply", send)
    api_app, router = FastAPI(), APIRouter()
    register_routes(router, None)
    api_app.include_router(router, prefix="/api")
    api = TestClient(api_app)
    calls = []

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
                calls.append((data["method"], path))
                status = 200
                if path.startswith("/api/workflows/studio/incoming/proposals/review?"):
                    result = {"status": "thread", "chat_id": chat_id}
                elif path == f"/api/chats/{chat_id}":
                    result = {"id": chat_id, "title": "Linked Development thread", "has_reply_source": True,
                              "messages": [{"role": "assistant", "chat_row_id": mode["response_id"],
                                            "content": "The delivery check is complete."}]}
                else:
                    native = api.request(data["method"], path, json=data.get("body"))
                    status, result = native.status_code, native.json()
                    if width == 390 and mode["value"] == "unknown" and path.endswith("/approve"):
                        socket.close()
                        return
                reply["data"] = {"status": status, "response": result}
            socket.send(json.dumps(reply))
        socket.on_message(handle)

    page.route_web_socket("**/ws/telegram?*", relay)
    page.set_default_timeout(7000)
    page.set_viewport_size({"width": width, "height": 850})
    page.goto(f"{app_view}?channel=fixture&proposal_key={'a' * 64}")
    EVIDENCE.mkdir(exist_ok=True)
    opener = page.get_by_role("button", name="Message Client", exact=True)
    expect(opener).to_be_visible()
    page.screenshot(path=EVIDENCE / f"{width}-thread.png", animations="disabled")
    opener.click()
    dialog = page.frame_locator('iframe[title="Development source reply"]').get_by_role("dialog", name="Message Client")
    edit = dialog.get_by_label("Reply", exact=True)
    approve = dialog.get_by_role("button", name="Approve", exact=True)
    expect(dialog.get_by_text("To: operations@example.test", exact=True)).to_be_visible()
    expect(dialog.get_by_text("Cc: dispatch@example.test", exact=True)).to_be_visible()
    expect(edit).to_have_value("The delivery check is complete.")
    assert len(generations) == 1 and not attempts
    page.screenshot(path=EVIDENCE / f"{width}-draft.png", animations="disabled")
    edit.fill("Please confirm the 14:00 delivery window.")
    approve.click()
    expect(dialog.get_by_text("Nothing was sent. Reconnect Gmail before trying again.", exact=True)).to_be_visible()
    expect(edit).to_have_value("Please confirm the 14:00 delivery window.")
    expect(approve).to_be_enabled()
    assert len(attempts) == 1 and not deliveries
    page.screenshot(path=EVIDENCE / f"{width}-blocked.png", animations="disabled")
    mode["value"] = "sent"
    approve.click()
    expect(page.get_by_role("status").get_by_text("Reply sent", exact=True)).to_be_visible()
    expect(page.locator('iframe[title="Development source reply"]')).to_have_count(0)
    assert len(deliveries) == 1 and deliveries[0][1] == "Please confirm the 14:00 delivery window."
    page.screenshot(path=EVIDENCE / f"{width}-sent.png", animations="disabled")
    expect(opener).to_be_focused()
    opener.click()
    expect(dialog.get_by_text("Previously sent.", exact=True)).to_be_visible()
    expect(approve).to_be_disabled()
    page.screenshot(path=EVIDENCE / f"{width}-previously-sent.png", animations="disabled")
    assert len(generations) == 1 and len(deliveries) == 1
    dialog.get_by_role("button", name="Close message", exact=True).click()

    mode.update(value="unknown", response_id=next_response_id)
    page.reload()
    opener.click()
    expect(edit).to_have_value("The delivery check is complete.")
    edit.fill("The second delivery update.")
    approve.click()
    expect(dialog.get_by_role("status")).to_contain_text("Send was not confirmed.")
    expect(edit).to_have_value("The second delivery update.")
    expect(approve).to_be_disabled()
    page.screenshot(path=EVIDENCE / f"{width}-unconfirmed.png", animations="disabled")
    dialog.get_by_role("button", name="Close message", exact=True).click()
    if width == 390:
        expect(page.get_by_text("Your draft is kept.", exact=False)).to_have_count(0, timeout=10000)
    opener.click()
    expect(dialog.get_by_role("status")).to_contain_text("Send was not confirmed.")
    expect(approve).to_be_disabled()
    assert len(generations) == 2 and len(attempts) == 3 and len(deliveries) == 1
    assert sum(path.endswith("/approve") for method, path in calls if method == "POST") == 3
