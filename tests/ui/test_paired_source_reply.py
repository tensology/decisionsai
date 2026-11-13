"""The remote browser uses the desktop's reply review and durable send state."""
import json
import os
from pathlib import Path
from unittest.mock import Mock

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient
from playwright.sync_api import expect

from distr.core.db import Chat, SourceReplyDraft
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
    resolve_target, send_reply = replies.resolve_target, replies.send_reply
    with session() as db:
        root = Chat(title="Linked Development thread", project_id=projects[0],
                    params=json.dumps({"incoming_proposal_handoff": {"proposal": proposal}}))
        db.add(root)
        db.flush()
        response = Chat(parent_id=root.id, response="The delivery check is complete.")
        next_response = Chat(parent_id=root.id, response="The next delivery check is complete.")
        final_response = Chat(parent_id=root.id, response="The final delivery check is complete.")
        retry_response = Chat(parent_id=root.id, response="The follow-up delivery check is complete.")
        db.add_all([response, next_response, final_response, retry_response])
        db.commit()
        chat_id, response_id, next_response_id, final_response_id = root.id, response.id, next_response.id, final_response.id
        retry_response_id = retry_response.id

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

    def send(target, text, draft_id, *, before_send):
        attempts.append((target, text, draft_id))
        if mode["value"] == "preflight":
            raise replies.ReplyPreflightError("Nothing was sent. Reconnect Gmail before trying again.")
        before_send()
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
                if "/source-replies" in path:
                    calls.append((data["method"], path))
                status = 200
                if path.startswith("/api/workflows/studio/incoming/proposals/review?"):
                    result = {"status": "thread", "chat_id": chat_id}
                elif path == f"/api/chats/{chat_id}?surface=development":
                    result = {"id": chat_id, "title": "Linked Development thread", "has_reply_source": True,
                              "messages": [{"role": "assistant", "chat_row_id": mode["response_id"],
                                            "content": "The delivery check is complete."}]}
                    if mode.get("history"):
                        result["messages"].insert(0, {"role": "assistant", "chat_row_id": final_response_id,
                                                      "content": "The earlier delivery result."})
                    with session() as db:
                        sent = replies.sent_reply_response_ids(db, chat_id)
                    for message in result["messages"]:
                        if message["chat_row_id"] in sent:
                            message["source_reply_sent"] = True
                elif path == f"/api/workflows/studio/tasks/{chat_id}/execution":
                    result = {"status": "completed", "direct": True, "exact_stop": None, "exact_steer": None}
                else:
                    if mode["value"] == "lost_request" and path.endswith("/approve"):
                        socket.send(json.dumps({**reply, "error": "Fixture dropped the approval before desktop receipt"}))
                        return
                    native = api.request(data["method"], path, json=data.get("body"))
                    status, result = native.status_code, native.json()
                    if width == 390 and mode["value"] == "unknown" and path.endswith("/approve"):
                        socket.close()
                        return
                    if mode["value"] == "lost_receipt" and path.endswith("/approve"):
                        assert result["status"] == "sent"
                        if width == 390:
                            socket.close()
                        else:
                            socket.send(json.dumps({**reply, "error": "Fixture lost the reply receipt"}))
                        return
                    if mode.get("foreign_receipts") and data["method"] == "GET" and "/source-replies?" in path:
                        draft = result["drafts"][0]
                        result["drafts"].extend([
                            {**draft, "status": "sent", "response_id": response_id},
                            {**draft, "status": "sent", "source_key": "unlinked-source"},
                        ])
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
    check = dialog.get_by_role("button", name="Check send status")
    before_check = len(calls)
    mode["foreign_receipts"] = True
    check.click()
    expect(dialog.get_by_role("status")).to_contain_text("Send was not confirmed.")
    expect(approve).to_be_disabled()
    assert calls[before_check:] == [("GET", f"/api/workflows/studio/tasks/{chat_id}/source-replies?response_id={next_response_id}")]
    expect(page.get_by_text("Reply sent", exact=True)).to_have_count(0)
    mode["foreign_receipts"] = False
    assert len(generations) == 2 and len(attempts) == 3 and len(deliveries) == 1
    mode.update(value="lost_receipt", response_id=final_response_id)
    page.reload()
    opener.click()
    expect(edit).to_have_value("The delivery check is complete.")
    edit.fill("The final delivery update.")
    approve.click()
    expect(dialog.get_by_role("status")).to_contain_text("Send was not confirmed.")
    expect(check).to_be_enabled()
    page.screenshot(path=EVIDENCE / f"{width}-receipt-lost.png", animations="disabled")
    if width == 390:
        expect(page.get_by_text("Your draft is kept.", exact=False)).to_have_count(0, timeout=10000)
    before_check = len(calls)
    check.click()
    expect(dialog.get_by_text("Previously sent.", exact=True)).to_be_visible()
    expect(edit).to_have_value("The final delivery update.")
    expect(approve).to_be_disabled()
    expect(check).to_be_hidden()
    assert calls[before_check:] == [("GET", f"/api/workflows/studio/tasks/{chat_id}/source-replies?response_id={final_response_id}")]
    page.screenshot(path=EVIDENCE / f"{width}-receipt-recovered.png", animations="disabled")
    dialog.get_by_role("button", name="Close message", exact=True).click()
    expect(page.get_by_role("status").get_by_text("Reply sent", exact=True)).to_be_visible()
    expect(opener).to_be_focused()
    page.screenshot(path=EVIDENCE / f"{width}-recovered-thread.png", animations="disabled")
    before_reload = list(calls)
    page.reload()
    expect(page.get_by_text("Reply sent", exact=True)).to_be_visible()
    assert calls == before_reload
    page.screenshot(path=EVIDENCE / f"{width}-reloaded-receipt.png", animations="disabled")
    opener.click()
    expect(dialog.get_by_text("Previously sent.", exact=True)).to_be_visible()
    dialog.get_by_role("button", name="Close message", exact=True).click()
    expect(page.get_by_role("status").get_by_text("Reply sent", exact=True)).to_be_visible()
    assert len(generations) == 3 and len(attempts) == 4 and len(deliveries) == 2
    assert sum(path.endswith("/approve") for method, path in calls if method == "POST") == 4

    mode.update(value="lost_request", response_id=retry_response_id)
    page.reload()
    opener.click()
    expect(edit).to_have_value("The delivery check is complete.")
    edit.fill("Please use the loading bay entrance.")
    approve.click()
    expect(check).to_be_enabled()
    expect(approve).to_be_disabled()
    before_check = len(calls)
    check.click()
    expect(dialog.get_by_role("status")).to_contain_text("The desktop has no send recorded for this draft.")
    expect(edit).to_have_value("Please use the loading bay entrance.")
    expect(approve).to_be_enabled()
    expect(check).to_be_hidden()
    assert calls[before_check:] == [("GET", f"/api/workflows/studio/tasks/{chat_id}/source-replies?response_id={retry_response_id}")]
    assert len(attempts) == 4 and len(deliveries) == 2
    page.screenshot(path=EVIDENCE / f"{width}-request-recovered.png", animations="disabled")
    mode["value"] = "sent"
    approve.click()
    expect(page.get_by_role("status").get_by_text("Reply sent", exact=True)).to_be_visible()
    assert len(generations) == 4 and len(attempts) == 5 and len(deliveries) == 3
    assert deliveries[-1][1] == "Please use the loading bay entrance."
    approval_paths = [path for method, path in calls if method == "POST" and path.endswith("/approve")]
    assert len(approval_paths) == 6 and approval_paths[-2] == approval_paths[-1]

    mode.update(history=True, response_id=next_response_id)
    calls_before_reload = list(calls)
    page.reload()
    earlier = page.locator(".av-msg.assistant").filter(has_text="The earlier delivery result.")
    latest = page.locator(".av-msg.assistant").filter(has_text="The delivery check is complete.")
    expect(earlier.get_by_role("status")).to_have_text("Reply sent")
    expect(earlier.get_by_role("button", name="Message Client")).to_have_count(0)
    expect(latest.get_by_role("button", name="Message Client")).to_be_visible()
    expect(latest.get_by_text("Reply sent", exact=True)).to_have_count(0)
    assert calls == calls_before_reload
    page.screenshot(path=EVIDENCE / f"{width}-historical-receipt.png", animations="disabled")

    with session() as db:
        db.add(SourceReplyDraft(id="damaged-history", chat_id=chat_id, payload="{broken", status="unconfirmed"))
        db.commit()
    opener.click()
    blocked = dialog.get_by_role("status")
    expect(blocked).to_contain_text("Check the original conversation")
    # Headless Chrome reports hasFocus() false inside the sandboxed srcdoc.
    # Verify both links of the focus chain across the frame boundary.
    assert blocked.evaluate("(element) => element.ownerDocument.activeElement === element")
    assert page.evaluate("document.hasFocus() && document.activeElement.matches('iframe[title=\"Development source reply\"]')")
    expect(dialog.get_by_label("To:", exact=True)).to_be_hidden()
    expect(dialog.locator('label[for="source-reply-source"]')).to_be_hidden()
    expect(approve).to_be_disabled()
    expect(dialog.get_by_role("button", name="Rewrite")).to_be_disabled()
    assert calls[len(calls_before_reload):] == [
        ("GET", f"/api/workflows/studio/tasks/{chat_id}/source-replies?response_id={next_response_id}"),
    ]
    page.screenshot(path=EVIDENCE / f"{width}-damaged-history.png", animations="disabled")
    dialog.get_by_role("button", name="Close message", exact=True).click()
    expect(opener).to_be_focused()
    expect(earlier.get_by_role("status")).to_have_text("Reply sent")

    with session() as db:
        root = db.get(Chat, chat_id)
        params = json.loads(root.params)
        params["incoming_proposal_handoff"]["proposal"]["source_scope"] = [
            {"source": "gmail", "source_thread_id": "source-a", "message_ids": ["message-a"], "participant": "Original sender"},
            {"source": "mailshot", "source_thread_id": "source-b", "message_ids": ["message-b"], "participant": "Independent sender"},
        ]
        root.params = json.dumps(params)
        damaged = db.get(SourceReplyDraft, "damaged-history")
        damaged.claimed_source_key = "gmail:source-a"
        db.commit()

    original = {
        "id": "message-b", "thread_id": "source-b", "from": "independent@example.test",
        "to": "delivery@example.test", "subject": "Delivery window", "messageId": "<message-b@example.test>",
    }
    mailshot = Mock()
    mailshot.get.side_effect = {
        "mail/senders": [{"email": "delivery@example.test", "primary": True}],
        "mail/messages/message-b": {"email": original},
    }.__getitem__
    mailshot.post.return_value = {"id": "confirmed-reply"}
    monkeypatch.setattr("distr.core.tensology_client.configured_tensology_client", lambda **_: mailshot)
    monkeypatch.setattr(replies, "resolve_target", resolve_target)
    monkeypatch.setattr(replies, "send_reply", send_reply)
    mode["value"] = "sent"
    opener.click()
    recipient = dialog.get_by_label("To:", exact=True)
    recipient.select_option("gmail:source-a")
    expect(blocked).to_contain_text("Check the original conversation")
    expect(approve).to_be_disabled()
    expect(recipient).to_be_enabled()
    mailshot.post.assert_not_called()
    page.screenshot(path=EVIDENCE / f"{width}-merged-reply-blocked.png", animations="disabled")
    recipient.select_option("mailshot:source-b")
    expect(edit).to_be_enabled()
    expect(dialog.get_by_text("To: independent@example.test", exact=True)).to_be_visible()
    page.screenshot(path=EVIDENCE / f"{width}-merged-reply-available.png", animations="disabled")
    edit.fill("Reviewed reply for the independent sender.")
    original["reply_to"] = "updated@example.test"
    approve.click()
    expect(blocked).to_contain_text("The Mailshot reply recipients changed.")
    expect(edit).to_have_value("Reviewed reply for the independent sender.")
    expect(approve).to_be_disabled()
    mailshot.post.assert_not_called()
    page.screenshot(path=EVIDENCE / f"{width}-merged-reply-recipient-changed.png", animations="disabled")
    dialog.get_by_role("button", name="Rewrite", exact=True).click()
    expect(dialog.get_by_text("To: updated@example.test", exact=True)).to_be_visible()
    expect(approve).to_be_enabled()
    mailshot.post.assert_not_called()
    edit.fill("Reviewed reply for the independent sender.")
    page.screenshot(path=EVIDENCE / f"{width}-merged-reply-reviewed.png", animations="disabled")
    approve.click()
    expect(latest.get_by_role("status")).to_have_text("Reply sent")
    mailshot.post.assert_called_once()
    assert mailshot.post.call_args.args[1]["to"] == ["updated@example.test"]
    draft_id = mailshot.post.call_args.kwargs["idempotency_key"]
    opener.click()
    recipient.select_option("mailshot:source-b")
    expect(dialog.get_by_text("Previously sent.", exact=True)).to_be_visible()
    expect(approve).to_be_disabled()
    duplicate = api.post(
        f"/api/workflows/studio/tasks/{chat_id}/source-replies/{draft_id}/approve",
        json={"approved": True, "text": "A repeated approval must never be delivered."},
    )
    assert duplicate.status_code == 200 and duplicate.json()["already_sent"]
    assert duplicate.json()["text"] == "Reviewed reply for the independent sender."
    mailshot.post.assert_called_once()
    assert len(deliveries) == 3
    page.screenshot(path=EVIDENCE / f"{width}-merged-reply-reopened.png", animations="disabled")

    from distr.core.db.kanban import KanbanTicket
    from distr.core.db.workflow import AutoWorkflow, AutoWorkflowRun
    from distr.core.workflow import reply_delivery
    from distr.core.workflow.reply_channel import resolve_reply_target
    from tests.core.test_reply_delivery import _ticket

    dialog.get_by_role("button", name="Close message", exact=True).click()
    ticket_id = _ticket(session, linked_project_id=projects[0], source_provider="gmail",
                        source_thread_id="source-a", source_contact="operations@example.test")
    with session() as db:
        db.delete(db.get(SourceReplyDraft, "damaged-history"))
        workflow = AutoWorkflow(name="Closing reply")
        db.add(workflow)
        db.flush()
        run = AutoWorkflowRun(workflow_id=workflow.id, chat_id=chat_id, ticket_id=ticket_id, status="completed")
        launch = Chat(parent_id=chat_id, input="Run the final check.")
        db.add_all([run, launch])
        db.flush()
        result = Chat(parent_id=chat_id, response="The workflow check is complete.", params=json.dumps({
            "workflow_reply": {"run_id": run.id, "turn_id": launch.id},
        }))
        db.add(result)
        db.flush()
        workflow_response_id = result.id
        scope = {"run_id": run.id, "workflow_id": workflow.id, "chat_id": chat_id, "turn_id": launch.id}
        ticket = db.get(KanbanTicket, ticket_id)
        envelope = reply_delivery._delivery_envelope(db, ticket, resolve_reply_target(ticket), "")
        db.commit()
    workflow_send = Mock(return_value={"success": True})
    monkeypatch.setitem(reply_delivery.ADAPTERS, "email", workflow_send)
    outcome = reply_delivery.execute_approved_workflow_reply({
        "ticket_id": ticket_id, "channel": "email", "body": "Workflow closing reply.",
        "delivery": envelope, "workflow": scope,
    })
    assert outcome["delivered"], outcome
    mode["response_id"] = workflow_response_id
    page.reload()
    opener.click()
    recipient.select_option("gmail:source-a")
    expect(blocked).to_contain_text("The workflow already sent a reply to this conversation.")
    expect(approve).to_be_disabled()
    page.screenshot(path=EVIDENCE / f"{width}-workflow-recipient-blocked.png", animations="disabled")
    recipient.select_option("mailshot:source-b")
    expect(edit).to_be_enabled()
    edit.fill("Independent reply for this workflow result.")
    approve.click()
    expect(latest.get_by_role("status")).to_have_text("Reply sent")
    opener.click()
    recipient.select_option("mailshot:source-b")
    expect(dialog.get_by_text("Previously sent.", exact=True)).to_be_visible()
    page.screenshot(path=EVIDENCE / f"{width}-workflow-independent-receipt.png", animations="disabled")
    workflow_send.assert_called_once()
    assert mailshot.post.call_count == 2
