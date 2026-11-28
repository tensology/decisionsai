"""Step narration: one editable Telegram message per job, remote status line when live."""
import queue
from types import SimpleNamespace

import pytest

from distr.core.initiative import job_progress as jp


class Sinks:
    def __init__(self):
        self.sent, self.edited, self.pushed = [], [], []
        self.remote = False
        self.remote_ok = True
        self.telegram_ok = True
        self.clock = 1_000_000.0


@pytest.fixture
def sinks(monkeypatch):
    jp.reset_for_tests()
    s = Sinks()
    monkeypatch.setattr(jp, "_now", lambda: s.clock)
    monkeypatch.setattr(jp, "enabled", lambda: True)
    monkeypatch.setattr(jp, "_remote_active", lambda: s.remote)
    monkeypatch.setattr(jp, "_remote_push", lambda payload: (s.pushed.append(payload), s.remote_ok)[1])
    monkeypatch.setattr(jp, "_telegram_send", lambda text, token: (s.sent.append((text, token)), s.telegram_ok)[1])
    monkeypatch.setattr(jp, "_telegram_edit",
                        lambda text, mid, chat: (s.edited.append((text, mid, chat)), s.telegram_ok)[1])
    monkeypatch.setattr(jp, "_ticket_title", lambda _i: "Merrypak EFT banking details")
    monkeypatch.setattr(jp, "_step_name", lambda _i: "Update EFT documents")
    yield s
    jp.reset_for_tests()


def _ack(sinks, key, mid=77):
    job = jp.get_job(key)
    assert jp.record_message(job.token, mid, 5551)


def test_first_step_sends_one_message_and_later_steps_edit_it(sinks):
    assert jp.report("t:1", "picked_up", "Julie's EFT mail", title="Julie EFT")
    assert len(sinks.sent) == 1 and sinks.sent[0][1].startswith("job-status:")
    assert "Julie EFT - working" in sinks.sent[0][0]
    assert "Picked up: Julie's EFT mail" in sinks.sent[0][0]
    _ack(sinks, "t:1")
    sinks.clock += jp.MIN_EDIT_INTERVAL_S + 0.1
    jp.report("t:1", "proposal", "problem, cause, fix plan")
    assert len(sinks.sent) == 1 and len(sinks.edited) == 1
    text, mid, chat = sinks.edited[0]
    assert (mid, chat) == (77, 5551)
    assert "Created proposal" in text and "Picked up" in text


def test_updates_inside_the_interval_are_coalesced_not_sent(sinks):
    jp.report("t:2", "picked_up", "x")
    _ack(sinks, "t:2")
    sinks.clock += 1
    jp.report("t:2", "step", "one")
    jp.report("t:2", "step", "two")
    job = jp.get_job("t:2")
    assert sinks.edited == [] and job.dirty and job.timer is not None
    job.timer.cancel()
    sinks.clock += jp.MIN_EDIT_INTERVAL_S
    assert jp.flush("t:2") == "telegram"
    assert len(sinks.edited) == 1 and "one" in sinks.edited[0][0] and "two" in sinks.edited[0][0]


def test_blocked_waiting_and_done_go_out_quickly(sinks):
    jp.report("t:3", "picked_up", "x")
    _ack(sinks, "t:3")
    sinks.clock += jp.URGENT_EDIT_INTERVAL_S + 0.1
    jp.report("t:3", "waiting", "your approval of the fix plan")
    assert len(sinks.edited) == 1
    assert "waiting for you" in sinks.edited[0][0] and "Waiting for you: your approval" in sinks.edited[0][0]


def test_identical_steps_collapse_and_identical_milestones_dedupe(sinks):
    jp.report("t:4", "step", "Ran helper code")
    sinks.clock += 10
    jp.report("t:4", "step", "Ran helper code")
    assert jp.get_job("t:4").lines[-1].count == 2 and len(jp.get_job("t:4").lines) == 1
    assert jp.report("t:4", "thread", "#9") is True
    assert jp.report("t:4", "thread", "#9") is False
    assert "x2" in jp.render(jp.get_job("t:4"))


def test_live_remote_session_gets_status_event_and_telegram_stays_quiet(sinks):
    sinks.remote = True
    jp.report("t:5", "picked_up", "mail", title="Mail job")
    assert sinks.sent == [] and len(sinks.pushed) == 1
    payload = sinks.pushed[0]
    assert payload["topic"] == "job_status" and payload["state"] == "running"
    assert payload["title"] == "Mail job" and payload["latest"] == "Picked up: mail"
    assert payload["lines"][-1]["text"] == "Picked up: mail"


def test_failed_remote_push_falls_back_to_telegram(sinks):
    sinks.remote, sinks.remote_ok = True, False
    jp.report("t:6", "picked_up", "mail")
    assert len(sinks.pushed) == 1 and len(sinks.sent) == 1


def test_no_message_id_yet_waits_then_sends_again_after_timeout(sinks):
    jp.report("t:7", "picked_up", "a")
    sinks.clock += jp.MIN_EDIT_INTERVAL_S + 1
    jp.report("t:7", "step", "b")
    job = jp.get_job("t:7")
    assert len(sinks.sent) == 1 and sinks.edited == []
    if job.timer:
        job.timer.cancel()
    sinks.clock += jp.ACK_WAIT_S + 1
    job.dirty = True
    assert jp.flush("t:7") == "telegram"
    assert len(sinks.sent) == 2


def test_ack_after_pending_update_flushes_it(sinks):
    jp.report("t:8", "picked_up", "a")
    sinks.clock += jp.MIN_EDIT_INTERVAL_S + 1
    jp.report("t:8", "step", "b")
    job = jp.get_job("t:8")
    if job.timer:
        job.timer.cancel()
        job.timer = None
    _ack(sinks, "t:8", 12)
    assert len(sinks.edited) == 1 and sinks.edited[0][1] == 12


def test_global_rate_limit_defers_updates(sinks):
    for i in range(jp.GLOBAL_MAX_UPDATES):
        jp.report(f"g:{i}", "picked_up", "x")
    assert len(sinks.sent) == jp.GLOBAL_MAX_UPDATES
    jp.report("g:over", "picked_up", "x")
    assert len(sinks.sent) == jp.GLOBAL_MAX_UPDATES
    over = jp.get_job("g:over")
    assert over.dirty and over.timer is not None
    over.timer.cancel()
    sinks.clock += jp.GLOBAL_WINDOW_S + 1
    assert jp.flush("g:over") == "telegram"


def test_gives_up_after_repeated_delivery_failures(sinks):
    sinks.telegram_ok = False
    jp.report("t:9", "picked_up", "x")
    for _ in range(5):
        job = jp.get_job("t:9")
        if job.timer:
            job.timer.cancel()
            job.timer = None
        sinks.clock += 30
        jp.flush("t:9")
    assert len(sinks.sent) == jp.MAX_DELIVERY_FAILURES
    assert not jp.get_job("t:9").dirty


def test_parent_joins_jobs_into_one_status_message(sinks):
    jp.report("mail:1", "picked_up", "Julie's mail", title="Julie EFT")
    _ack(sinks, "mail:1")
    sinks.clock += jp.MIN_EDIT_INTERVAL_S + 1
    jp.report("ticket:9", "thread", "#2481", parent="mail:1")
    assert jp.get_job("ticket:9") is jp.get_job("mail:1")
    assert len(sinks.sent) == 1 and len(sinks.edited) == 1
    assert "Created thread: #2481" in sinks.edited[0][0]


def test_render_caps_lines_and_never_leaks_secrets(sinks):
    for i in range(12):
        sinks.clock += 1
        jp.report("t:10", "step", f"step {i}")
    jp.report("t:10", "info", "token=abc123supersecretvalue and Bearer abcdefghijklmnop")
    text = jp.render(jp.get_job("t:10"))
    assert "earlier" in text and text.count("\n") <= jp.MAX_LINES + 2
    assert "abc123supersecretvalue" not in text and "abcdefghijklmnop" not in text


def test_disabled_reports_nothing(sinks, monkeypatch):
    monkeypatch.setattr(jp, "enabled", lambda: False)
    assert jp.report("t:11", "picked_up", "x") is False and sinks.sent == []


def test_report_never_raises(sinks, monkeypatch):
    monkeypatch.setattr(jp, "_telegram_send", lambda *a: (_ for _ in ()).throw(RuntimeError("boom")))
    assert jp.report("t:12", "picked_up", "x") is True


# --- chat turns from Telegram / remote ---------------------------------------------------

def _user_msg(chat=1381, turn=2472, surface="telegram"):
    return {"source": "chat", "event_type": "worker_progress", "payload": {
        "subtype": "chat_user_message", "surface": surface, "chat_id": chat, "chat_row_id": turn,
        "content_preview": "Check that Julie's mail is a proposal and push it as a thread"}}


def _turn(etype, title="", summary="", chat=1381, turn=2472):
    return {"chat_id": chat, "turn_id": turn, "event_type": etype, "title": title, "summary": summary}


def test_telegram_turn_with_tools_is_narrated_step_by_step(sinks):
    jp.on_orchestration_event("x", _user_msg())
    jp.on_chat_turn_event(_turn("turn_started", "Request received"))
    jp.on_chat_turn_event(_turn("acknowledgment", "Working on it."))
    assert sinks.sent == []
    jp.on_chat_turn_event(_turn("tool_completed", "Google Workspace"))
    assert len(sinks.sent) == 1
    assert "Check that Julie's mail" in sinks.sent[0][0] and "Google Workspace" in sinks.sent[0][0]
    _ack(sinks, "turn:1381:2472")
    sinks.clock += 10
    jp.on_chat_turn_event(_turn("tool_failed", "Request Tool", "Invalid arguments"))
    sinks.clock += 10
    jp.on_chat_turn_event(_turn("turn_completed", "Answer ready", "Julie says go ahead."))
    last = sinks.edited[-1][0]
    assert "Request Tool failed: Invalid arguments" in last and "Done: answer sent" in last and "- done" in last


def test_round_cap_turn_reports_blocked(sinks):
    jp.on_orchestration_event("x", _user_msg())
    jp.on_chat_turn_event(_turn("tool_completed", "Ran helper code"))
    _ack(sinks, "turn:1381:2472")
    sinks.clock += 10
    jp.on_chat_turn_event(_turn("turn_completed", "Answer ready", "I hit my limit of 10 tool rounds for one message"))
    assert "Blocked: hit the tool-round limit" in sinks.edited[-1][0] and "- blocked" in sinks.edited[-1][0]


def test_plain_answer_and_desktop_turns_create_no_status_message(sinks):
    jp.on_orchestration_event("x", _user_msg())
    jp.on_chat_turn_event(_turn("synthesis_started"))
    jp.on_chat_turn_event(_turn("turn_completed", "Answer ready", "Hello"))
    jp.on_orchestration_event("x", _user_msg(turn=3000, surface="desktop"))
    jp.on_chat_turn_event(_turn("tool_completed", "Google Workspace", turn=3000))
    assert sinks.sent == [] and sinks.pushed == []


# --- orchestrator ledger ------------------------------------------------------------------

def _ev(source, etype, legacy="", status="", summary="", ticket=257, run=190, step=None):
    return {"source": source, "event_type": etype, "status": status, "summary": summary, "ticket_id": ticket,
            "run_id": run, "step_id": step, "payload": {"orchestration": {"legacy_event_type": legacy}}}


def test_orchestration_events_map_to_steps(sinks):
    assert jp._map_orchestration(_ev("workflow", "run_started", "workflow_run_started"), "workflow",
                                 "run_started", "workflow_run_started", "", "") == ("ticket:257", "picked_up", "the work run started")
    assert jp._map_orchestration(_ev("workflow", "workflow_step_completed", summary="Understand ticket: passed"),
                                 "workflow", "workflow_step_completed", "", "passed",
                                 "Understand ticket: passed")[1:] == ("step", "finished step: Understand ticket: passed")
    assert jp._map_orchestration(_ev("approval", "approval_requested", summary="Release"), "approval",
                                 "approval_requested", "", "waiting", "Release")[1] == "waiting"
    assert jp._map_orchestration(_ev("workflow", "worker_failed", summary="Tests failed"), "workflow", "worker_failed",
                                 "", "failed", "Tests failed")[1:] == ("blocked", "Tests failed")
    assert jp._map_orchestration(_ev("workflow", "worker_failed", "workflow_run_cancelled"), "workflow",
                                 "worker_failed", "workflow_run_cancelled", "cancelled", "")[1] == "failed"
    assert jp._map_orchestration(_ev("workflow", "worker_completed", "workflow_run_completed", summary="Finished"),
                                 "workflow", "worker_completed", "workflow_run_completed", "", "Finished")[1:] == ("done", "Finished")
    assert jp._map_orchestration(_ev("pi", "worker_dispatched", step=3), "pi", "worker_dispatched", "", "", "")[2] == "@step"


def test_noise_and_unscoped_events_are_ignored(sinks):
    assert jp._map_orchestration(_ev("pi", "worker_progress"), "pi", "worker_progress", "", "", "") is None
    assert jp._map_orchestration(_ev("pi", "worker_dispatched", ticket=None, run=None), "pi", "worker_dispatched",
                                 "", "", "") is None
    jp.on_orchestration_event("x", "not a dict")  # never raises


def test_bridge_reports_with_ticket_title_off_thread(sinks):
    ev = _ev("workflow", "run_started", "workflow_run_started")
    jp._report_mapped(ev, "ticket:257", "picked_up", "the work run started")
    job = jp.get_job("ticket:257")
    assert job.title == "Merrypak EFT banking details" and len(sinks.sent) == 1
    jp._report_mapped(_ev("pi", "worker_dispatched", step=3), "ticket:257", "step", "@step")
    assert jp.get_job("ticket:257").lines[-1].text == "started step: Update EFT documents"


def test_install_subscribes_once():
    jp.reset_for_tests()
    subscribed = []
    bus = SimpleNamespace(subscribe=lambda t, h: subscribed.append((t, h)))
    assert jp.install(bus) is True and jp.install(bus) is False
    assert subscribed == [("orchestration.event", jp.on_orchestration_event)]
    jp.reset_for_tests()


# --- the silent round-cap bug ---------------------------------------------------------------

def test_round_cap_notice_is_queued_for_telegram():
    from distr.core.agent.services.llm.openai_compat import OpenAICompatibleLLMService

    fake = SimpleNamespace(event_queue=queue.Queue(), _telegram_fallback_text=None, saved=[])
    fake._save_assistant_message = fake.saved.append
    OpenAICompatibleLLMService._notify_round_cap(fake, 10, [{"function": {"name": "execute_code"}}])
    assert fake._telegram_fallback_text.startswith("I hit my limit of 10 tool rounds")
    assert fake.saved == [fake._telegram_fallback_text]


# --- Telegram plumbing: sender passes the status token through; the ack reaches job_progress ----

def test_sender_transmits_job_status_messages_and_edits_them_in_place(monkeypatch):
    import json
    from tests.telegram.test_telegram_delivery_stability import DummyTelegramSender

    jp.reset_for_tests()
    sender = DummyTelegramSender()
    assert sender.send_to_telegram("Julie EFT - working\n13:00 Picked up: mail", interaction_token="job-status:mail_1")
    sender._process_message_queue()
    assert len(sender.websocket_attempts) == 1
    frame = sender.websocket_attempts[0]
    assert frame["client_message_id"] == "job-status:mail_1" and frame["type"] == "send_message"
    assert sender.edit_telegram_message("Julie EFT - done", message_id=77, chat_id=12345)
    sender._process_message_queue()
    assert sender.websocket_attempts[-1]["type"] == "edit_message" and sender.websocket_attempts[-1]["message_id"] == 77
    json.dumps(sender.websocket_attempts)


def test_socket_ack_records_the_message_id_for_the_job(monkeypatch):
    import json
    from distr.core.integrations.telegram.manager import TelegramWebSocketManager

    jp.reset_for_tests()
    monkeypatch.setattr(jp, "enabled", lambda: True)
    monkeypatch.setattr(jp, "_remote_active", lambda: False)
    monkeypatch.setattr(jp, "_telegram_send", lambda text, token: True)
    jp.report("ack:1", "picked_up", "x")
    job = jp.get_job("ack:1")
    manager = SimpleNamespace(_mark_socket_heartbeat_seen=lambda: None)
    TelegramWebSocketManager._on_message(manager, json.dumps({
        "type": "message_sent", "client_message_id": job.token, "telegram_message_id": 4242, "chat_id": 12345}))
    assert (job.message_id, job.chat_id, job.awaiting_ack) == (4242, 12345, False)
    jp.reset_for_tests()


# --- initiative outcome audit events (client mail watch) ------------------------------------

def test_client_mail_proposal_is_narrated_then_waits_for_approval(sinks):
    steps = jp._map_outcome("outcome_client_mail_proposal",
                            {"message_id": "m1", "kind": "billing_error", "proposal_id": 5}, {})
    assert [s["kind"] for s in steps] == ["picked_up", "proposal", "waiting"]
    assert steps[0]["title"] == "Client mail: billing error" and {s["job"] for s in steps} == {"mail:m1"}
    jp._report_outcome(steps)
    assert jp.get_job("mail:m1").state == "waiting"
    assert "Created proposal: problem, cause and fix plan" in jp.render(jp.get_job("mail:m1"))
    assert len(sinks.sent) == 1  # the burst is coalesced into the first message, not three
    if jp.get_job("mail:m1").timer:
        jp.get_job("mail:m1").timer.cancel()


def test_card_not_delivered_is_reported_as_blocked(sinks):
    steps = jp._map_outcome("outcome_client_mail_card", {"message_id": "m2", "delivered": False}, {})
    assert steps[0]["kind"] == "blocked"


def test_approval_then_ticket_joins_the_mail_job(sinks):
    jp._report_outcome(jp._map_outcome("outcome_client_mail_card", {"message_id": "m3", "delivered": True, "kind": "x"}, {}))
    _ack(sinks, "mail:m3")
    sinks.clock += 10
    jp._report_outcome(jp._map_outcome("outcome_client_issue_started", {"message_id": "m3", "ticket_id": 400}, {}))
    assert jp.get_job("ticket:400") is jp.get_job("mail:m3")
    assert jp.get_job("mail:m3").state == "running"
    assert "Created thread: ticket and thread opened" in jp.render(jp.get_job("mail:m3"))


def test_started_without_a_ticket_asks_which_project(sinks):
    steps = jp._map_outcome("outcome_client_issue_started", {"message_id": "m4", "ticket_id": None}, {})
    assert steps[-1]["kind"] == "waiting" and "project" in steps[-1]["text"]


def test_dismissed_and_reply_events(sinks):
    assert jp._map_outcome("outcome_client_issue_dismissed", {"message_id": "m5"}, {})[0]["kind"] == "done"
    held = jp._map_outcome("outcome_own_client_reply_held_for_approval", {"ticket_id": 9}, {})
    assert held[0]["kind"] == "waiting" and held[0]["job"] == "ticket:9"
    sent = jp._map_outcome("outcome_own_client_reply_sent", {"ticket_id": 9, "delivered": True}, {})
    assert sent[0]["kind"] == "done"
    assert jp._map_outcome("outcome_own_client_reply_sent", {"ticket_id": 9, "delivered": False}, {}) == []
    assert jp._map_outcome("outcome_something_else", {"message_id": "x"}, {}) == []
    assert jp._map_outcome("worker_progress", {}, {}) == []


def test_new_work_on_a_finished_job_starts_a_fresh_status_message(sinks):
    jp.report("ticket:7", "picked_up", "run 1", title="T")
    first = jp.get_job("ticket:7").token
    jp.report("ticket:7", "done", "finished")
    sinks.clock += 10
    jp.report("ticket:7", "picked_up", "run 2")
    job = jp.get_job("ticket:7")
    assert job.token != first and [l.text for l in job.lines] == ["run 2"]
    assert len(sinks.sent) == 2
