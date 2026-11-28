"""Whole path with real components: ledger events -> EventBus -> narration -> real Telegram sender
frames (with the relay's message_sent ack fed through the real socket handler) and remote relay frames.
Run with ``-s`` to print the timestamped message sequence."""
import json
import time
from datetime import datetime

import pytest

from distr.core.initiative import job_progress as jp
from tests.telegram.test_telegram_delivery_stability import DummyTelegramSender


class Relay(DummyTelegramSender):
    """Real sender queue + socket framing; the 'relay' answers every send with message_sent."""

    def __init__(self):
        super().__init__()
        self.log = []          # (hh:mm:ss, route, text)
        self.next_id = 500
        self.remote_frames = []

    def _mark_socket_heartbeat_seen(self):
        pass

    def pump(self):
        from distr.core.integrations.telegram.manager import TelegramWebSocketManager

        before = len(self.websocket_attempts)
        self._process_message_queue()
        for frame in self.websocket_attempts[before:]:
            stamp = datetime.now().strftime("%H:%M:%S")
            if frame["type"] == "send_message":
                self.next_id += 1
                self.log.append((stamp, f"telegram send   #{self.next_id}", frame["text"]))
                TelegramWebSocketManager._on_message(self, json.dumps({
                    "type": "message_sent", "client_message_id": frame["client_message_id"],
                    "telegram_message_id": self.next_id, "chat_id": 12345}))
            elif frame["type"] == "edit_message":
                self.log.append((stamp, f"telegram edit   #{frame['message_id']}", frame["text"]))


def _wait(predicate, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        if predicate():
            return True
        time.sleep(0.05)
    return False


@pytest.fixture
def world(monkeypatch):
    from distr.core.events import get_event_bus, reset_event_bus_for_tests

    jp.reset_for_tests()
    reset_event_bus_for_tests()
    relay = Relay()
    relay.telegram_user_id = 12345
    remote = {"live": False}
    monkeypatch.setattr(jp, "enabled", lambda: True)
    monkeypatch.setattr(jp, "_manager", lambda: relay)
    monkeypatch.setattr(jp, "_remote_active", lambda: remote["live"])
    monkeypatch.setattr(jp, "_remote_push", lambda payload: (relay.remote_frames.append(
        (datetime.now().strftime("%H:%M:%S"), payload)), True)[1])
    monkeypatch.setattr(jp, "_ticket_title", lambda _i: "Acme Print EFT banking details")
    monkeypatch.setattr(jp, "_step_name", lambda i: {11: "Update EFT documents", 12: "Verify payment retry"}.get(i, ""))
    monkeypatch.setattr(jp, "MIN_EDIT_INTERVAL_S", 0.4)
    monkeypatch.setattr(jp, "URGENT_EDIT_INTERVAL_S", 0.1)
    assert jp.install(get_event_bus())
    yield relay, remote
    jp.reset_for_tests()
    reset_event_bus_for_tests()


def _emit(**kw):
    """Same entry points the app uses: the canonical emitter, or the raw ledger emitter."""
    from distr.core.orchestration_events import emit_orchestration_event
    from distr.core.orchestrator import emit_event, emit_channel_intake_event, emit_approval_event

    if kw["event_type"] == "channel_intake_ticket_created":
        return emit_channel_intake_event(channel=kw["source"], ticket_id=kw["ticket_id"], summary=kw["summary"])
    if kw["event_type"] == "approval_requested":
        return emit_approval_event(event_type="approval_requested", run_id=kw["run_id"], ticket_id=kw["ticket_id"],
                                   summary=kw["summary"])
    if kw["event_type"] == "workflow_step_completed":
        return emit_event(**kw)
    return emit_orchestration_event(**kw)


def _pump_until(relay, n, timeout=5.0):
    return _wait(lambda: (relay.pump(), len(relay.log) >= n)[1], timeout)


def test_ticket_job_is_narrated_to_telegram_as_one_edited_message(world):
    relay, _ = world
    _emit(source="telegram", event_type="channel_intake_ticket_created", status="created", ticket_id=257, summary="x")
    assert _pump_until(relay, 1)
    _emit(source="workflow", event_type="workflow_run_started", status="running", ticket_id=257, run_id=190,
          summary="Workflow run started.")
    time.sleep(0.5)
    _emit(source="pi", event_type="project_handoff_dispatched", status="completed", ticket_id=257, run_id=190,
          step_id=11, summary="Project handoff dispatched to pi.")
    _emit(source="pi", event_type="execution_message_update", status="running", ticket_id=257, run_id=190,
          step_id=11, summary="noise")   # progress chatter must not be narrated
    time.sleep(0.5)
    _emit(source="workflow", event_type="workflow_step_completed", status="passed", ticket_id=257, run_id=190,
          step_id=11, summary="Update EFT documents: passed")
    time.sleep(0.5)
    _emit(source="approval", event_type="approval_requested", status="waiting", ticket_id=257, run_id=190,
          summary="Release the checkout changes?")
    time.sleep(0.3)
    _emit(source="workflow", event_type="workflow_run_completed", status="completed", ticket_id=257, run_id=190,
          summary="Checkout changes verified")
    assert _pump_until(relay, 6, timeout=8)
    time.sleep(0.3)
    relay.pump()
    print("\n--- Telegram (route: remote app not live) ---")
    for stamp, route, text in relay.log:
        print(f"[{stamp}] {route}\n" + "\n".join("    " + line for line in text.splitlines()))
    sends = [e for e in relay.log if "send" in e[1]]
    edits = [e for e in relay.log if "edit" in e[1]]
    assert len(sends) == 1 and len(edits) >= 4                       # one message, then edits
    assert all(e[1].endswith(sends[0][1].split("#")[1]) for e in edits)  # every edit hits the same message id
    final = edits[-1][2]
    assert final.startswith("Acme Print EFT banking details - done")
    for expected in ("Created thread: from telegram intake", "Picked up: the work run started",
                     "started step: Update EFT documents", "finished step: Update EFT documents: passed",
                     "Waiting for you: your approval: Release the checkout changes?",
                     "Done: Checkout changes verified"):
        assert expected in final, expected
    assert "noise" not in final
    assert len(relay.log) <= 8                                           # no flood


def test_chat_request_from_telegram_gets_step_updates_and_cap_is_reported(world):
    relay, _ = world
    _emit(source="chat", event_type="chat_message_added", status="added", summary="user message",
          payload={"subtype": "chat_user_message", "surface": "telegram", "chat_id": 1381, "chat_row_id": 2472,
                   "content_preview": "Check that Dana's mail is a proposal and push it as a thread"})
    for etype, title, summary in [("turn_started", "Request received", ""), ("acknowledgment", "Working on it.", ""),
                                  ("tool_completed", "Google Workspace", ""), ("tool_failed", "Request Tool", "Invalid arguments"),
                                  ("tool_completed", "Ran helper code", ""), ("tool_completed", "Ran helper code", ""),
                                  ("synthesis_started", "Preparing the answer", ""),
                                  ("turn_completed", "Answer ready", "I hit my limit of 10 tool rounds for one message")]:
        jp.on_chat_turn_event({"chat_id": 1381, "turn_id": 2472, "event_type": etype, "title": title, "summary": summary})
        relay.pump()
        time.sleep(0.15)
    assert _wait(lambda: (relay.pump(), "- blocked" in relay.log[-1][2])[1], 6)
    print("\n--- Telegram: chat request ---")
    for stamp, route, text in relay.log:
        print(f"[{stamp}] {route}\n" + "\n".join("    " + line for line in text.splitlines()))
    assert len([e for e in relay.log if "send" in e[1]]) == 1
    assert "Blocked: hit the tool-round limit" in relay.log[-1][2] and "Ran helper code x2" in relay.log[-1][2]


def test_live_remote_session_gets_the_same_steps_as_status_events(world):
    relay, remote = world
    remote["live"] = True
    _emit(source="workflow", event_type="workflow_run_started", status="running", ticket_id=300, run_id=1,
          summary="Workflow run started.")
    time.sleep(0.5)
    _emit(source="workflow", event_type="workflow_step_completed", status="passed", ticket_id=300, run_id=1,
          step_id=11, summary="Update EFT documents: passed")
    assert _wait(lambda: len(relay.remote_frames) >= 2, 5)
    print("\n--- Remote app (route: live session) ---")
    for stamp, payload in relay.remote_frames:
        print(f"[{stamp}] {payload['state']:8} {payload['title']} · {payload['latest']}")
    relay.pump()
    assert relay.log == []                                     # nothing on Telegram while the remote is live
    assert relay.remote_frames[-1][1]["latest"] == "finished step: Update EFT documents: passed"
    assert relay.remote_frames[-1][1]["topic"] == "job_status"
