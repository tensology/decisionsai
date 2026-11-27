"""Scenario tests replaying real Decisions anomalies (personal data scrubbed)."""

from __future__ import annotations

import asyncio
import shutil
from dataclasses import replace
from types import SimpleNamespace
from datetime import datetime, timedelta

import pytest


# --- negation / scope in read-only and deploy intent ------------------------------

SNUZA_BRIEF = (
    "Where the work lives: the V3 API checkout is /Users/dev/WORK/CLIENT/api3.example.com. "
    "The v2 reference is the legacy API in /Users/dev/WORK/CLIENT/api.example.com/webapp "
    "(urls.py, views.py) - read-only. Add missing fields to the v3 responses, add tests. "
    "Never write to the legacy databases. Do not commit, push or deploy - that is done after review."
)


@pytest.mark.parametrize(
    "text, expected",
    [
        (SNUZA_BRIEF, False),
        ("Investigate the 500s, keep production read-only and fix it locally", False),
        ("keep production read-only", False),
        ("This isn't read-only anymore, implement it", False),
        ("Use a read-only database connection to check counts", False),
        ("Do a read-only audit", True),
        ("strictly read-only", True),
        ("Keep it read-only", True),
        ("Read-only: check the logs and tell me", True),
        ("Investigate read-only", True),
    ],
)
def test_read_only_applies_to_the_run_only_when_meant(text, expected):
    from distr.core.intent_scope import requests_read_only_run

    assert requests_read_only_run(text) is expected


def test_snuza_brief_is_not_review_only_and_not_a_deploy():
    from distr.core.work_intake.execution_policy import compile_requested_execution_policy
    from distr.core.workflow.builtin import deploy_intent

    assert "read_only" not in compile_requested_execution_policy(SNUZA_BRIEF)
    assert deploy_intent(SNUZA_BRIEF) is None


@pytest.mark.parametrize(
    "text, expected",
    [
        ("Don't commit, push or deploy.", None),
        ("No commit, push or deployment without my go", None),
        ("Fix it but don't deploy", None),
        ("deploy to dev", "development"),
        ("Deploy it, don't wait", "development"),
    ],
)
def test_deploy_intent_handles_negated_lists(text, expected):
    from distr.core.workflow.builtin import deploy_intent

    assert deploy_intent(text) == expected


def test_steer_keep_production_read_only_keeps_write_tools():
    from distr.core.workflow.development_harness import _audit_only_request

    assert _audit_only_request("Fix the catalogue 500 locally, keep production read-only") is False
    assert _audit_only_request("audit only, read-only please") is True


# --- planner credentials and model ids ----------------------------------------------

def test_planner_passes_saved_key_and_skips_uninstalled_ollama(monkeypatch):
    from distr.core.initiative import planners
    from distr.core import litellm_utils

    settings = {"openrouter_key": "sk-test-not-real", "ollama_url": "http://127.0.0.1:11434"}
    monkeypatch.setattr(planners, "_llm_candidates", lambda _s: [
        ("ollama", "deepseek/deepseek-v4.1-flash"), ("ollama", "llama3.2"), ("openrouter", "deepseek/deepseek-v4.1-flash"),
    ])
    monkeypatch.setattr(litellm_utils, "ollama_installed_models", lambda *_a, **_k: frozenset({"deepseek-v4.1-flash:cloud"}))
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    calls = []

    def fake_completion(*_args, **kwargs):
        calls.append(kwargs)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="## Outcome for Today\nShip it"))])

    monkeypatch.setattr(litellm_utils, "litellm_completion", fake_completion)
    bundle = SimpleNamespace()
    monkeypatch.setattr(planners, "_user_payload", lambda *_a, **_k: "{}")
    markdown, _ = planners.generate_planner_markdown("day", settings, bundle, "plan")

    assert markdown.startswith("## Outcome")
    assert [call["model"] for call in calls] == ["openrouter/deepseek/deepseek-v4.1-flash"]
    assert calls[0]["api_key"] == "sk-test-not-real"


def test_litellm_completion_injects_key_without_overriding_explicit(monkeypatch):
    from distr.core import litellm_utils

    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setattr(litellm_utils, "_load_settings", lambda: {"openrouter_key": "from-settings"})
    seen = []
    import litellm

    monkeypatch.setattr(litellm, "completion", lambda *a, **k: seen.append(k) or "ok")
    litellm_utils.litellm_completion(model="openrouter/x/y", messages=[])
    litellm_utils.litellm_completion(model="openrouter/x/y", messages=[], api_key="explicit")
    assert seen[0]["api_key"] == "from-settings"
    assert seen[1]["api_key"] == "explicit"


# --- rg missing --------------------------------------------------------------------

def test_search_files_falls_back_when_ripgrep_is_missing(tmp_path, monkeypatch):
    from distr.core.turn_runtime.project_tools import ProjectToolExecutor

    (tmp_path / "app.py").write_text("def ble_address():\n    return 1\n")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "x.js").write_text("ble_address")
    real_which = shutil.which
    monkeypatch.setattr(shutil, "which", lambda name, *a, **k: None if name == "rg" else real_which(name, *a, **k))
    tools = ProjectToolExecutor(str(tmp_path))

    observation = tools._search_files({"query": "ble_address"})
    assert observation.status == "success"
    assert "app.py:1:" in observation.output
    assert "node_modules" not in observation.output

    blocked = tools._run_command({"command": "rg ble_address ."})
    assert blocked.status == "error"
    assert "search_files" in " ".join(blocked.next_actions)


# --- step limit checkpoint and honest interrupted message ---------------------------

def test_native_step_limit_returns_a_progress_checkpoint():
    from distr.core.turn_runtime.contracts import (
        ModelResponse, ModelToolCall, ToolObservation,
    )
    from distr.core.turn_runtime.native import NativeTurnRuntime
    from tests.core.test_turn_runtime import _request

    class Model:
        adapter_id = "loop"

        async def complete(self, _messages, tools, *, on_delta=None):
            if not tools:
                return ModelResponse(text="Done: read the views. Left: add fields. Next: edit schemas.py.")
            return ModelResponse(tool_calls=(ModelToolCall(call_id="again", name="noop"),))

    class Tools:
        def definitions(self):
            return [{"name": "noop", "description": "noop", "parameters": {"type": "object", "properties": {}}}]

        async def execute(self, _name, _arguments):
            return ToolObservation(status="success", summary="No operation.")

    result = asyncio.run(NativeTurnRuntime(model=Model(), tools=Tools(), max_iterations=2).execute(_request()))
    assert result.success is False
    assert "2-step limit" in result.error and "continue" in result.error
    assert "Next: edit schemas.py" in result.output
    assert result.evidence["checkpoint"] is True


def test_interrupted_turn_message_uses_the_real_reason():
    from distr.core.workflow.development_harness import _interrupted_turn_message

    assert "25-step limit" in _interrupted_turn_message("The native Development turn reached its 25-iteration safety limit.")
    message = _interrupted_turn_message("100% done. The restore finished and every check passed.")
    assert "Last thing it reported: 100% done" in message and "continue" in message


# --- one clear stuck message, self-serve first -------------------------------------

def test_stuck_message_says_what_why_tried_and_need():
    from distr.core.kanban.ticket_workflow_engagement import build_workflow_waiting_nudge

    text, voice = build_workflow_waiting_nudge(
        workflow_name="Development",
        ticket_title="Investigate PostgreSQL connection failures on catalogue page",
        waiting_kind="worker_needs_input",
        project_name="Jackcat",
        why="server-side evidence is not reachable from this context",
        tried="Repo scripts that already connect to the server: load_latest_db.py",
        need="the pgbouncer and PostgreSQL logs for 09:01 on the 27th",
    )
    assert text.startswith("Jackcat: Investigate PostgreSQL")
    assert "Why:" in text and "Already tried:" in text and "I need: the pgbouncer" in text
    assert "Development is stuck" not in text
    assert "needs: the pgbouncer" in voice


def test_self_serve_finds_repo_paths_and_ssh_routes(tmp_path):
    from distr.core.workflow.unstick import find_self_serve_facts, self_serve_answer

    project = tmp_path / "www.example.com"
    project.mkdir()
    other = tmp_path / "api3.example.com"
    other.mkdir()
    (project / "deploy.sh").write_text("ssh paul@example-server 'tail -n 50 /var/log/app.log'\n")
    ssh_config = tmp_path / "ssh_config"
    ssh_config.write_text("Host jackcat-prod\n  HostName www.jackcat.example\n  User paul\n")

    facts = find_self_serve_facts(
        question="The repositories it needs are cut off; the v3 code is not here.",
        brief=f"The V3 checkout is {other}.",
        project_name="Example",
        project_folder=str(project),
        ssh_config=ssh_config,
    )
    assert any(str(other) in fact and "exists" in fact for fact in facts)

    facts = find_self_serve_facts(
        question="server-side evidence not reachable: pgbouncer log and journalctl -u jackcat",
        project_name="Jackcat",
        project_folder=str(project),
        ssh_config=ssh_config,
    )
    assert any("jackcat-prod" in fact for fact in facts)
    assert any("deploy.sh" in fact for fact in facts)
    assert not any("id_" in fact or "BEGIN" in fact for fact in facts)

    assert self_serve_answer({"self_serve_attempts": 1}, question="repo cut off", project_folder=str(project)) is None


# --- duplicate incident proposals ---------------------------------------------------

JACKCAT_TITLES = [
    "Investigate PostgreSQL connection failures on /catalogue/",
    "Fix database connection error on users API request",
    "Investigate database connection failures on Jackcat catalogue page",
    "Investigate PostgreSQL connection failures on catalogue part page",
    "Investigate database connection failures on catalogue item page",
    "Fix database connection error and secure production error responses",
    "Investigate PostgreSQL connection failures causing catalogue 500 errors",
    "Investigate recurring database connection failures on catalogue page",
    "Investigate PostgreSQL connection failures on vehicle search",
    "Investigate recurring database connection failures on catalogue pages",
    "Investigate database connection failures on vehicle search type endpoint",
    "Investigate PostgreSQL connection failures on catalogue page",
]


def test_one_alert_per_incident(monkeypatch):
    from distr.core import tensology_stream as ts

    monkeypatch.setattr(ts, "_recent_incidents", [])
    start = 1_000_000.0
    alerts = [
        title for index, title in enumerate(JACKCAT_TITLES)
        if not ts.is_repeat_incident({"project": "Jackcat", "title": title}, now=start + index * 5)
    ]
    assert len(alerts) == 1
    # Different problems for the same client on the same day still alert.
    assert not ts.is_repeat_incident({"project": "Merrypak", "title": "Investigate incorrect date shown for today's order"}, now=start)
    assert not ts.is_repeat_incident({"project": "Merrypak", "title": "Investigate missing funds for today's order"}, now=start + 1900)


# --- client voice --------------------------------------------------------------------

def test_client_drafts_speak_as_paul_short_and_to_the_point():
    from distr.core.client_voice import apply_client_voice, unverified_claims
    from distr.core.kanban.client_message_humanize import humanize_client_message
    from distr.core.workflow.reply_channel import find_promise_claims, humanize_reply

    draft = (
        "Hi Sibo, hope you're doing well! We've added ble_address and feature_bits to v3. "
        "Your office network blocks port 22, so you need the VPN for that. Let us know if anything looks off."
    )
    for shaped in (apply_client_voice(draft), humanize_reply(draft), humanize_client_message(draft)):
        assert "hope you" not in shaped.lower()
        assert not any(word in shaped.lower().split() for word in ("we", "we've", "us", "our"))
        assert "I've added" in shaped
        assert "network" not in shaped.lower()
        assert "if anything looks off" not in shaped.lower() or "let me know" in shaped.lower()

    claim = "Hi Julie, we've reproduced the checkout error and it's sorted."
    assert unverified_claims(claim, evidence="Root cause found in middleware; fix applied locally")
    assert find_promise_claims(claim)  # held for Paul's approval before sending


def test_source_reply_prompt_carries_the_voice_rules_and_revises_claims(monkeypatch):
    from distr.core.incoming import source_reply

    prompts = []
    replies = iter([
        "Hi Sibo, hope you're well. We've reproduced it and fixed the devices list.",
        "Hi Sibo, I've added the missing device fields on v3. I'm checking the rest next.",
    ])

    def complete(_provider, _model, prompt, _settings):
        prompts.append(prompt)
        return next(replies)

    monkeypatch.setattr("distr.core.read_response.resolve_oracle_model", lambda _s: ("openrouter", "m"))
    monkeypatch.setattr("distr.core.settings.load_settings_from_db", lambda: {})
    monkeypatch.setattr("distr.core.incoming.source_context.safe_source_body", lambda r: "Added ble_address and feature_bits to v3 devices.")
    source = {"channel": "whatsapp", "label": "WhatsApp", "message_ids": [], "recipient_names": ["Sibo"]}
    text, _meta = source_reply.generate_draft({"evidence": []}, source, {"body": "x"}, complete=complete)

    system = prompts[0][0]["content"]
    assert "never we" in system.lower() and "hope you're well" in system
    assert len(prompts) == 2  # one revision for the unverified "reproduced" claim
    assert text.startswith("Hi Sibo, I've added")


# --- setup self-check ------------------------------------------------------------------

def test_setup_check_speaks_once_and_only_when_nothing_works(monkeypatch):
    from distr.core import setup_check, litellm_utils

    monkeypatch.setattr(litellm_utils, "ollama_installed_models", lambda *_a, **_k: frozenset({"deepseek-v4.1-flash:cloud"}))
    monkeypatch.setattr(setup_check, "_ollama_probe", lambda *_a: "credits")
    working = setup_check.run_setup_check(
        {"llm_provider": "openrouter", "llm_model": "deepseek/deepseek-v4.1-flash", "openrouter_key": "k",
         "coding_llm_provider": "ollama", "coding_llm_model": "deepseek/deepseek-v4.1-flash"},
        probe_cloud=True, automation_routes=[("ollama", "deepseek-v4.1-flash:cloud")],
    )
    keys = {issue.key for issue in working.issues}
    assert "ollama-missing:deepseek/deepseek-v4.1-flash" in keys and "ollama-cloud-credits" in keys
    assert working.one_message() == ""  # routed around through OpenRouter

    broken = setup_check.run_setup_check(
        {"llm_provider": "openrouter", "llm_model": "deepseek/deepseek-v4.1-flash"},
        probe_cloud=True, automation_routes=[("ollama", "deepseek-v4.1-flash:cloud")],
    )
    message = broken.one_message()
    assert message.count(".") <= 3 and "Settings" in message


# --- a recovered provider failure is not reported as a problem -----------------------

def test_failed_route_that_failed_over_is_not_announced(monkeypatch):
    from distr.core.initiative.service import InitiativeService

    monkeypatch.setattr("distr.core.provider_failover.next_failover", lambda *_a, **_k: object())
    row = SimpleNamespace(
        id=1, project_id=2, ticket_id=None, origin="development", selected_model="deepseek-v4.1-flash:cloud",
        started_at=datetime(2026, 11, 27, 7, 3), input_packet='{"model_provider": "ollama"}',
        error='402: {"message":"Add usage credits or subscribe"}',
    )
    service = InitiativeService.__new__(InitiativeService)
    assert service._superseded_by_failover(row, {}, [(row, None)]) is True
    task_failure = replace(row, error="Tests failed: 3 errors") if hasattr(row, "__dataclass_fields__") else SimpleNamespace(**{**row.__dict__, "error": "Tests failed: 3 errors"})
    assert service._superseded_by_failover(task_failure, {}, [(task_failure, None)]) is False


# --- read-only keeps the shell; secrets never reach the model ----------------------

def test_read_only_run_keeps_shell_but_blocks_writes(tmp_path):
    from distr.core.turn_runtime.project_tools import ProjectToolExecutor

    (tmp_path / "app.log").write_text("ERROR pgbouncer: no more connections\n")
    tools = ProjectToolExecutor(str(tmp_path), read_only=True)
    names = {d["function"]["name"] for d in tools.definitions()}
    assert "run_command" in names and "write_file" not in names and "replace_text" not in names

    ok = asyncio.run(tools.execute("run_command", {"command": "head -1 app.log"}))
    assert ok.status == "success" and "no more connections" in ok.output
    for command in ("echo x > app.log", "rm app.log", "git commit -m x", "sed -i s/a/b/ app.log", "python manage.py migrate"):
        refused = asyncio.run(tools.execute("run_command", {"command": command}))
        assert refused.status == "error" and "Read-only" in refused.summary, command
    assert (tmp_path / "app.log").exists()


def test_secret_files_are_refused_and_values_redacted(tmp_path):
    from distr.core.turn_runtime.project_tools import ProjectToolExecutor

    (tmp_path / ".env-pass").write_text("hunter2-real-password\n")
    (tmp_path / "settings_local.py").write_text("DATABASES = {'default': {'PASSWORD': 'p4ss'}}\nDB_PASSWORD = 'p4ss'\n")
    tools = ProjectToolExecutor(str(tmp_path))
    for command in ("cat .env-pass", "head -1 ./.env-pass", "grep . .env-pass"):
        refused = asyncio.run(tools.execute("run_command", {"command": command}))
        assert refused.status == "error" and "never read" in refused.summary
        assert "hunter2" not in (refused.output or "") + (refused.error or "")
    read = asyncio.run(tools.execute("read_file", {"path": ".env-pass"}))
    assert read.status == "error" and "hunter2" not in (read.output or "")
    shown = asyncio.run(tools.execute("read_file", {"path": "settings_local.py"}))
    assert "p4ss" not in shown.output and "[redacted]" in shown.output

    from distr.core.chat_turns import redact_text

    pw = "p4ss"
    sample = "DB_PASSWORD=" + pw + " and postgres://app:" + pw + "@db/x"
    assert pw not in redact_text(sample, preserve_paths=True)


# --- rejected proposals stay rejected; duplicates collapse --------------------------

def test_rejected_lane_move_does_not_come_back(tmp_path):
    from datetime import timezone
    from distr.core.initiative.draft_queue import DraftEntry, DraftQueue

    def lane_move(entry_id, note):
        now = datetime.now(tz=timezone.utc)
        return DraftEntry(
            id=entry_id, action_type="ticket_lane_move",
            description=f"Player1Sport has 8 backlog items that should move into Current. [last cycle {note} ago]",
            draft="", reason="", created_at=now.isoformat(), expires_at=(now + timedelta(hours=6)).isoformat(),
            execute_payload={"kind": "initiative_action", "action": {"action_type": "ticket_lane_move", "payload": {
                "board_id": 8, "ticket_ids": [258, 259, 260], "target_lane": "Current"}}},
        )

    path = str(tmp_path / "drafts.json")
    queue = DraftQueue(path)
    assert queue.add(lane_move("a", "51s")) is True
    assert queue.add(lane_move("b", "2m")) is False  # duplicate while pending
    assert queue.reject("a") is True
    assert DraftQueue(path).add(lane_move("c", "9m")) is False  # survives a restart
    assert DraftQueue(path).get_all() == []


# --- approving "run this ticket" from an async route --------------------------------

def test_project_cli_task_runs_when_called_inside_an_event_loop():
    from distr.core.initiative.action_handlers import _run_async

    async def work():
        await asyncio.sleep(0)
        return {"success": True}

    async def route():
        return _run_async(work())

    assert asyncio.run(route()) == {"success": True}


# --- the client-reply confirm card ---------------------------------------------------

def test_confirm_card_shows_recipient_question_and_full_text():
    from distr.core.initiative import outcome_flow as of

    body = "Hi Sibo, I've added ble_address, feature_bits and product_type_id to the v3 devices endpoint. " * 8
    card = of.work_done_text(
        "Sibo", "whatsapp", body,
        recipient=of.recipient_label("Sibo", "whatsapp", {"id": "1203630@g.us"}, {"conversation_label": "Snuza Dev"}),
        answering="The get devices model is missing ble_address and feature bits",
    )
    assert "Snuza Dev (WhatsApp group)" in card
    assert "Answering:" in card and "missing ble_address" in card
    assert body.strip()[-60:] in card  # the full final text, not a 400-char preview


# --- timers follow the real work -----------------------------------------------------

def test_live_project_run_keeps_the_thread_timer_running(tmp_path, monkeypatch):
    from distr.core.workflow import development_control as dc

    class Query:
        def __init__(self, rows):
            self.rows = rows

        def filter(self, *a, **k):
            return self

        def first(self):
            return None

        def all(self):
            return self.rows

    class DB:
        def query(self, *columns):
            name = str(columns[0]) if columns else ""
            if "project_execution_sessions" in name or "ProjectExecutionSession" in name:
                return Query([('{"chat_id": 2331}',)])
            return Query([])

    assert dc._thread_has_live_run(DB(), 2331, datetime(2026, 11, 27, 1, 50)) is True
    assert dc._thread_has_live_run(DB(), 999, datetime(2026, 11, 27, 1, 50)) is False


def test_auto_matched_skills_skip_order_conversion_billing_work():
    from distr.core.skills.catalog import infer_skills_for_ticket

    picked = infer_skills_for_ticket("The conversion keeps created_at; the tax invoice shows the order date")
    assert "product-marketing" not in picked and "decisions-marketing-skills" not in picked
    assert "product-marketing" in infer_skills_for_ticket("Improve the landing page conversion rate")


# --- 2026-11-27 12:30 batch: thread confirm card, settled drafts, QA move, SSE ticket, timers ---

def test_thread_reply_card_shows_full_context_and_sends_only_on_confirm(monkeypatch, tmp_path):
    from distr.core.initiative import outcome_flow as of
    from distr.core.incoming import source_reply

    monkeypatch.setattr(of, "_state_path", lambda: tmp_path / "flows.json")
    cards = []
    monkeypatch.setattr(of, "send_telegram", lambda text, reply_markup=None, settings=None: cards.append((text, reply_markup)) or True)
    sent = []
    monkeypatch.setattr(source_reply, "act_on_draft",
                        lambda chat_id, draft_id, action, text="", sender=None: sent.append((chat_id, draft_id, text)) or {"status": "sent"})
    monkeypatch.setattr(of, "audit", lambda *a, **k: None)
    body = "I've fixed the invoice total. It shows R1,250 now."
    draft = {"id": "d1", "text": body, "target": {"channel": "whatsapp", "recipient_names": ["Anna"]}}
    key = of.hold_thread_reply(42, draft, answering="Why is the total wrong?", label="Merrypak team")
    assert key and len(cards) == 1
    text, markup = cards[0]
    assert "Ready to send to Merrypak team (WhatsApp chat)" in text and body in text and "Why is the total wrong?" in text
    assert not sent
    assert "Shall I send it?" in of._handle_flow_action(key, "send")["text"]
    assert not sent
    assert "Holding it" in of._handle_flow_action(key, "cancel")["text"]
    of._handle_flow_action(key, "send")
    out = of._handle_flow_action(key, "confirm")
    assert sent == [(42, "d1", body)] and out["text"].startswith("Sent.")
    assert "already went out" in of._handle_flow_action(key, "confirm")["text"]
    assert of.hold_thread_reply(42, draft) == key and len(cards) == 1  # no second card


def test_lane_move_draft_dropped_once_ticket_is_already_there(tmp_path):
    from distr.core.initiative.draft_queue import DraftEntry, DraftQueue

    q = DraftQueue(str(tmp_path / "drafts.json"))
    from datetime import datetime, timedelta, timezone

    def entry(tid, lane):
        now = datetime.now(tz=timezone.utc)
        return DraftEntry(id=f"e{tid}", action_type="ticket_lane_move", description=f"move {tid}", draft="", reason="",
                          created_at=now.isoformat(), expires_at=(now + timedelta(hours=6)).isoformat(),
                          execute_payload={"kind": "initiative_action", "action": {
                              "action_type": "ticket_lane_move",
                              "payload": {"board_id": 2, "ticket_ids": [tid], "target_lane": lane}}})
    q.add(entry(306, "In Progress"))
    q.add(entry(307, "In Progress"))
    lanes = {306: "In Progress", 307: "Backlog"}
    assert q.drop_settled_lane_moves(lanes.get) == 1
    assert [e.description for e in q.get_all()] == ["move 307"]


def test_completed_write_run_qualifies_for_qa_but_read_only_does_not():
    from distr.core.kanban.project_execution import _run_changed_files

    assert _run_changed_files({"success": True, "workspace_state_delta": {"changed": True}})
    assert _run_changed_files({"success": True, "git_status_before": "", "git_status_after": " M a.py"})
    assert not _run_changed_files({"success": True, "read_only_expected": True, "workspace_state_delta": {"changed": True}})
    assert not _run_changed_files({"success": True, "waits_for_human": True, "workspace_state_delta": {"changed": True}})
    assert not _run_changed_files({"success": True, "workspace_state_delta": {"changed": False}})


def test_sse_ticket_is_one_use_and_short_lived():
    from distr.gui.web.routes import events_stream as es

    ticket = es.issue_stream_ticket(now=100.0)
    assert es.redeem_stream_ticket(ticket, now=101.0)
    assert not es.redeem_stream_ticket(ticket, now=101.0)
    late = es.issue_stream_ticket(now=100.0)
    assert not es.redeem_stream_ticket(late, now=100.0 + es.STREAM_TICKET_SECONDS + 1)
    assert not es.redeem_stream_ticket("", now=100.0)


def test_sse_client_uses_ticket_and_token_only_for_a_pre_ticket_server():
    from pathlib import Path

    js = (Path(__file__).resolve().parents[2] / "distr/gui/web/static/shared/js/events_stream.js").read_text()
    # The token is in the URL only when the running server has no ticket route (404).
    assert js.count("internal_token=") == 1
    assert js.index("r.status === 404") < js.index("internal_token=")
    assert "/api/events/stream-ticket" in js and "X-DecisionsAI-Internal-Token" in js


def test_finished_dispatched_turn_does_not_hold_thread_timer(monkeypatch):
    from distr.core.workflow import development_control as dc
    from distr.core.workflow import development_harness as dh

    monkeypatch.setattr(dh, "development_execution_state", lambda chat_id: {"job_id": "j1", "status": "failed"})
    data = {"execution_mode": "development_harness", "development_run_id": "j1", "chat_id": 598}
    assert dc._dispatched_turn_finished(data, 598)
    monkeypatch.setattr(dh, "development_execution_state", lambda chat_id: {"job_id": "j1", "status": "running"})
    assert not dc._dispatched_turn_finished(data, 598)
    monkeypatch.setattr(dh, "development_execution_state", lambda chat_id: {"job_id": "j2", "status": "failed"})
    assert not dc._dispatched_turn_finished(data, 598)


def test_run_waiting_on_review_is_not_billed_as_agent_work(monkeypatch):
    from distr.core.workflow import development_control as dc
    from distr.core.workflow import development_harness as dh

    monkeypatch.setattr(dh, "development_execution_state", lambda chat_id: {"status": "waiting"})
    assert dc._execution_active_or_unknown(2360) is False
    monkeypatch.setattr(dh, "development_execution_state", lambda chat_id: {"status": "running"})
    assert dc._execution_active_or_unknown(2360) is True


def test_setup_check_ignores_removed_projects():
    import inspect
    from distr.core import setup_check

    assert "deleted_at.is_(None)" in inspect.getsource(setup_check.linked_projects)


def test_pi_runs_load_the_secret_guard():
    import inspect
    from pathlib import Path
    from distr.core.project_cli_backends import registry

    assert "secret_guard.ts" in inspect.getsource(registry)
    guard = Path(registry.__file__).resolve().parent / "pi_extensions" / "secret_guard.ts"
    text = guard.read_text()
    assert '"tool_call"' in text and '"tool_result"' in text and "block: true" in text


# --- 12:52 batch: remote voice reply delivery, checkpoint follow-up context ---

class _Relay:
    def __init__(self, connected_after=0):
        self.checks = 0
        self.connected_after = connected_after
        self.sent = []
        self._pending_remote_agent_responses = [{"request_id": "r1", "created_at": __import__("time").time()}]

    def is_connected(self):
        self.checks += 1
        return self.checks > self.connected_after

    def _send_websocket_message(self, message):
        if not self.is_connected():
            return False
        self.sent.append(message)
        return True


def test_remote_reply_waits_out_relay_blip_and_consumes_context_only_after_delivery():
    from distr.core.integrations.telegram import remote_tts_delivery as rtd

    relay = _Relay(connected_after=2)
    calls = []
    ok = rtd.deliver_remote_with_retry(relay, lambda: calls.append(1) or True, "r1",
                                       delays=(1, 1, 1), sleep=lambda s: None)
    assert ok and calls == [1]
    assert relay._pending_remote_agent_responses == []


def test_remote_reply_not_delivered_keeps_context():
    from distr.core.integrations.telegram import remote_tts_delivery as rtd

    relay = _Relay()
    ok = rtd.deliver_remote_with_retry(relay, lambda: False, "r1", delays=(1, 1), sleep=lambda s: None)
    assert not ok and relay._pending_remote_agent_responses[0]["request_id"] == "r1"


def test_deliver_remote_tts_reports_failure_when_relay_drops_text(tmp_path):
    from distr.core.integrations.telegram import remote_tts_delivery as rtd

    class Dropping(_Relay):
        def _send_websocket_message(self, message):
            return False

    relay = Dropping()
    relay._press_key = lambda *a, **k: None
    ctx = {"request_id": "r1", "press_enter": True}
    ok = rtd.deliver_remote_tts(relay, "Done.", ctx, generate_tts=lambda t: None,
                                convert_wav_to_ogg=lambda p: p, cleanup_files=lambda *a: None)
    assert ok is False
    assert ctx["press_enter"] is False  # a retry won't press Enter twice


def test_remote_session_counts_as_available_during_relay_blip():
    from distr.core.integrations.telegram import remote_tts_delivery as rtd

    relay = _Relay(connected_after=10**6)  # relay down
    assert rtd.is_remote_delivery_available(relay)


def test_remote_origin_reply_goes_to_remote_worker_even_when_relay_is_down(monkeypatch):
    import threading
    from distr.app.events import EventHandlerMixin
    from distr.core.integrations.telegram import remote_tts_delivery as rtd

    relay = _Relay(connected_after=10**6)
    routed = []

    class App(EventHandlerMixin):
        telegram_manager = relay

        def _send_to_remote_worker(self, data, ctx):
            routed.append(("remote", ctx["request_id"]))

        def _send_to_telegram_worker(self, data):
            routed.append(("telegram", None))

    monkeypatch.setattr(threading.Thread, "start", lambda self: self.run())
    App()._evt_send_to_telegram({"text": "Here you go.", "provider": "fishaudio", "origin_surface": "remote",
                                 "origin_request_id": "r1", "is_done": True})
    assert routed == [("remote", "r1")]
    # Context is still there: only a successful delivery consumes it.
    assert relay._pending_remote_agent_responses[0]["request_id"] == "r1"


def test_checkpoint_ping_to_remote_carries_its_run(monkeypatch):
    from distr.core.initiative.service import InitiativeService
    from distr.core.integrations.telegram import remote_tts_delivery as rtd

    captured = {}
    monkeypatch.setattr(rtd, "enqueue_remote_tts_delivery", lambda text, data=None, block=False: captured.update(data or {}) or True)
    svc = InitiativeService.__new__(InitiativeService)
    svc._send_remote_notification("Jackcat paused for a checkpoint.", context={
        "run_id": 190, "ticket_id": 257, "workflow_id": 7, "thread_id": "984897897",
        "requires_response": True, "provider": "tool", "engagement_goal_hint": "Reply about run #190",
    })
    assert captured["run_id"] == 190 and captured["ticket_id"] == 257 and captured["thread_id"] == "984897897"
    assert captured["requires_response"] is True and "provider" not in captured
    assert captured["mode"] == "proactive"


def test_follow_up_preamble_uses_the_run_that_pinged(monkeypatch):
    from distr.core import human_engagement as he

    monkeypatch.setattr(he, "latest_remote_reply_context", lambda **kw: {
        "run_id": 190, "ticket_id": 257, "ticket_title": "Investigate PostgreSQL connection failures",
        "outbound_text": "Jackcat paused for a checkpoint.", "metadata": {"requires_response": True},
    })
    text = he.build_remote_reply_context_preamble("continue", thread_id="984897897")
    assert "Run: #190" in text and "Ticket ID: 257" in text and text.endswith("continue")
