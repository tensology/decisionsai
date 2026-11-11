from __future__ import annotations

import contextlib
import json
import subprocess
import pytest
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from distr.core.db import Base, Chat
from distr.core.db.projects import Project
from distr.core.db.workflow import DevelopmentWorkItem
from distr.core.db.time import utc_now_naive
from distr.core.workflow import development_harness as harness


@pytest.mark.parametrize('finish', ['edited', 'restored', 'deleted'])
def test_existing_dirty_tracked_content_is_not_a_noop(tmp_path, finish):
    subprocess.run(['git', 'init', '-b', 'main', str(tmp_path)], check=True, capture_output=True)
    path = tmp_path / 'existing.txt'
    path.write_text('committed\n')
    subprocess.run(['git', '-C', str(tmp_path), 'add', '.'], check=True)
    subprocess.run(['git', '-C', str(tmp_path), '-c', 'user.name=Test', '-c', 'user.email=test@example.com',
                    'commit', '-m', 'baseline'], check=True, capture_output=True)
    path.write_text('user edit\n')
    before = harness._git_status_snapshot(str(tmp_path))
    fingerprints = harness._git_dirty_fingerprints(str(tmp_path), before)
    assert harness._turn_change_manifest(str(tmp_path), before, before_fingerprints=fingerprints)['files'] == []
    if finish == 'deleted':
        path.unlink()
    else:
        path.write_text('committed\n' if finish == 'restored' else 'agent edit\n')
    changes = harness._turn_change_manifest(str(tmp_path), before, before_fingerprints=fingerprints)
    assert [f['path'] for f in changes['files']] == ['existing.txt']
    assert changes['reversible'] is False


@pytest.mark.parametrize('prompt', [
    'Audit the work and report back. Do not make fixes or deploy.',
    'Please review the implementation, audit only.',
    'Can you check whether the work was done properly?',
])
def test_audit_only_turn_overrides_full_autonomy_and_stops_after_report(tmp_path, monkeypatch, prompt):
    from distr.core.turn_runtime import TurnResult
    _, chat_id, _ = _database(tmp_path, monkeypatch)
    captured = {}
    queued = []
    async def execute(request, **kwargs):
        captured['request'] = request
        return TurnResult(success=True, backend_id='codex', model='fixture', runtime_id='cli_harness', output='Audit: verification is incomplete.')
    monkeypatch.setattr('distr.core.turn_runtime.execute_turn', execute)
    monkeypatch.setattr('distr.core.project_cli_backends.catalog_probe.probe_cli_backend', lambda *a, **k: {'status': {'ready': True}})
    monkeypatch.setattr(harness, '_start_next_queued_instruction', lambda *a: queued.append(a))
    result = harness.dispatch_development_prompt(chat_id, prompt, dispatch_async=False)
    request = captured['request']
    assert request.autonomy_level == 'plan'
    assert request.route.adapter_options['read_only_expected'] is True
    assert 'Stop after the audit report' in request.instruction
    assert result['outcome'].get('work_mode') == 'audit', result.get('error')
    assert queued == []


def test_explicit_audit_and_fix_remains_authorized_implementation():
    assert not harness._audit_only_request('Audit and fix the implementation, then run the tests.')
    assert not harness._audit_only_request('Fix those process defects and add regression tests.')
    assert not harness._audit_only_request("Fix Decisions. Don't change either other site.")
    assert harness._prompt_expects_project_change('Audit and fix the implementation', autonomy_level='full')


@pytest.mark.parametrize('codes,expected', [([], 'blocked'), ([1], 'blocked'), ([1, 0], 'completed'), ([0], 'completed')])
def test_completion_requires_actual_passing_check_receipts(tmp_path, monkeypatch, codes, expected):
    from distr.core.turn_runtime import TurnResult, TurnEvent, TurnEventKind, TurnStatus
    get_session, chat_id, _ = _database(tmp_path, monkeypatch)
    from distr.core.db.kanban import ProjectExecutionSession
    monkeypatch.setattr('distr.core.kanban.project_execution.get_session', get_session)
    monkeypatch.setattr('distr.core.kanban.project_execution.ensure_project_execution_tables', lambda: None)
    with get_session() as db:
        row = ProjectExecutionSession(project_id=1, route_type='cli', route_backend='fixture', status='completed',
                                      output_packet=json.dumps({'original': 'retained'}))
        db.add(row); db.commit(); session_id = row.id
    root = tmp_path / 'project'
    subprocess.run(['git', 'init', '-b', 'main', str(root)], check=True, capture_output=True)
    path = root / 'app.txt'; path.write_text('before\n')
    subprocess.run(['git', '-C', str(root), 'add', '.'], check=True)
    subprocess.run(['git', '-C', str(root), '-c', 'user.name=Test', '-c', 'user.email=test@example.com',
                    'commit', '-m', 'baseline'], check=True, capture_output=True)
    async def execute(request, *, on_event, **kwargs):
        path.write_text('implemented\n')
        for index, code in enumerate(codes):
            on_event(TurnEvent(kind=TurnEventKind.TOOL_COMPLETED, status=TurnStatus.UPDATING, summary='Check finished', runtime_id='cli_harness',
                tool_name='bash', details={'command': 'npm run test', 'exit_code': code, 'call_id': str(index)}))
        return TurnResult(success=True, backend_id='codex', model='fixture', runtime_id='cli_harness',
                          execution_session_id=session_id, output='Implemented and verified. Everything passed.')
    monkeypatch.setattr('distr.core.turn_runtime.execute_turn', execute)
    monkeypatch.setattr('distr.core.project_cli_backends.catalog_probe.probe_cli_backend', lambda *a, **k: {'status': {'ready': True}})
    result = harness.dispatch_development_prompt(chat_id, 'Fix the implementation and verify it', dispatch_async=False)
    assert result['status'] == expected, result.get('error')
    assert result['outcome']['verification']['status'] == ('passed' if expected == 'completed' else 'failed' if codes else 'not_run')
    if expected == 'blocked':
        assert result['summary'].startswith('Verification incomplete:')
    with get_session() as db:
        row = db.get(ProjectExecutionSession, session_id)
        assert row.status == expected
        packet = json.loads(row.output_packet)
        assert packet['original'] == 'retained'
        assert packet['completion_review']['verification'] == result['outcome']['verification']


def test_changes_committed_during_a_turn_are_detected_and_not_auto_undone(tmp_path):
    subprocess.run(['git', 'init', '-b', 'main', str(tmp_path)], check=True, capture_output=True)
    for name, value in [('user.name', 'Test'), ('user.email', 'test@example.com')]:
        subprocess.run(['git', '-C', str(tmp_path), 'config', name, value], check=True)
    path = tmp_path / 'app.txt'; path.write_text('before\n')
    subprocess.run(['git', '-C', str(tmp_path), 'add', '.'], check=True)
    subprocess.run(['git', '-C', str(tmp_path), 'commit', '-m', 'before'], check=True, capture_output=True)
    before = harness._git_status_snapshot(str(tmp_path)); head = harness._git_head(str(tmp_path))
    path.write_text('after\n')
    subprocess.run(['git', '-C', str(tmp_path), 'commit', '-am', 'after'], check=True, capture_output=True)
    assert harness._git_status_snapshot(str(tmp_path)) == {}
    result = harness._turn_change_manifest(str(tmp_path), before, before_head=head)
    assert [row['path'] for row in result['files']] == ['app.txt']
    assert result['additions'] == 1 and result['deletions'] == 1
    assert result['reversible'] is False


def test_audit_continuation_stays_read_only_until_explicit_repair(tmp_path, monkeypatch):
    from distr.core.turn_runtime import TurnResult
    _, chat_id, _ = _database(tmp_path, monkeypatch)
    requests = []
    async def execute(request, **kwargs):
        requests.append(request)
        return TurnResult(success=True, backend_id='codex', model='fixture', runtime_id='cli_harness', output='Report ready.')
    monkeypatch.setattr('distr.core.turn_runtime.execute_turn', execute)
    monkeypatch.setattr('distr.core.project_cli_backends.catalog_probe.probe_cli_backend', lambda *a, **k: {'status': {'ready': True}})
    for prompt in ['Audit only and report', 'Continue', 'Fix the reported defects and add tests']:
        harness.dispatch_development_prompt(chat_id, prompt, dispatch_async=False)
    assert [r.autonomy_level for r in requests] == ['plan', 'plan', 'full']
    assert 'Stop after the audit report' in requests[1].instruction


def test_audit_cannot_succeed_after_a_backend_changes_existing_content(tmp_path, monkeypatch):
    from distr.core.turn_runtime import TurnResult
    _, chat_id, _ = _database(tmp_path, monkeypatch)
    root = tmp_path / 'project'
    subprocess.run(['git', 'init', '-b', 'main', str(root)], check=True, capture_output=True)
    path = root / 'existing.txt'; path.write_text('user content\n')
    async def execute(request, **kwargs):
        path.write_text('unauthorized change\n')
        return TurnResult(success=True, backend_id='codex', model='fixture', runtime_id='cli_harness', output='Audit done.')
    monkeypatch.setattr('distr.core.turn_runtime.execute_turn', execute)
    monkeypatch.setattr('distr.core.project_cli_backends.catalog_probe.probe_cli_backend', lambda *a, **k: {'status': {'ready': True}})
    result = harness.dispatch_development_prompt(chat_id, 'Audit only, do not change files', dispatch_async=False)
    assert result['status'] == 'failed'
    assert 'Audit-only scope was violated' in result['error']
    assert result['changes']['files'][0]['path'] == 'existing.txt'
    assert result['changes']['reversible'] is False


def test_automation_owned_thread_prompt_names_its_control_target(monkeypatch):
    monkeypatch.setattr(harness.ChatService, "get_chat_history", lambda chat_id: [])

    instruction = harness._agent_instruction(
        73,
        "Pause this schedule.",
        {"source_type": "automation", "source_ref": "auto_9"},
        autonomy_level="full",
    )

    assert "Automation control: auto_9" in instruction
    assert "This thread owns that scheduled task" in instruction


def test_cli_backed_owned_thread_uses_server_bound_schedule_control(tmp_path, monkeypatch):
    _, chat_id, _ = _database(tmp_path, monkeypatch)
    project = SimpleNamespace(
        id=1,
        name="Demo",
        folder_location=str(tmp_path / "project"),
        coding_backend="pi",
        coding_backend_model="auto",
    )
    automation = {
        "id": "auto_9",
        "status": "active",
        "schedule": {"kind": "daily", "time": "09:00"},
    }
    updates = []
    notices = []
    monkeypatch.setattr(
        harness,
        "_project_and_scope",
        lambda selected_chat_id: (
            project,
            {"source_type": "automation", "source_ref": "auto_9", "model_route": {"route_mode": "auto"}},
            None,
            None,
        ),
    )
    monkeypatch.setattr("distr.core.automation.store.get_automation", lambda automation_id: dict(automation))

    def fake_update(automation_id, **fields):
        updates.append((automation_id, fields))
        automation.update(fields)
        return dict(automation)

    monkeypatch.setattr("distr.core.automation.store.update_automation", fake_update)
    monkeypatch.setattr(harness.ChatService, "append_assistant_notice", lambda target, text: notices.append((target, text)))
    dispatched = []
    monkeypatch.setattr(
        "distr.core.automation_orchestrator.dispatch_automation_to_current_chat",
        lambda selected, manual: dispatched.append((selected["id"], manual))
        or {"status": "running", "automation_id": selected["id"]},
    )
    monkeypatch.setattr(
        harness,
        "select_turn_runtime_id",
        lambda request: (_ for _ in ()).throw(AssertionError("CLI runtime should not be needed for schedule control")),
        raising=False,
    )

    paused = harness.dispatch_development_prompt(chat_id, "Pause this schedule", dispatch_async=False)
    changed = harness.dispatch_development_prompt(chat_id, "Change it to weekdays at 08:00", dispatch_async=False)
    run_now = harness.dispatch_development_prompt(chat_id, "Run it now", dispatch_async=False)

    assert paused["execution_mode"] == "automation_control"
    assert updates[0] == ("auto_9", {"status": "paused"})
    assert changed["automation"]["schedule"] == {
        "kind": "weekly",
        "time": "08:00",
        "days": "1,2,3,4,5",
        "timezone": "",
    }
    assert len(notices) == 2
    assert run_now["automation_id"] == "auto_9"
    assert dispatched == [("auto_9", True)]


def test_owned_automation_schedule_parser_covers_supported_edits():
    current = {"kind": "daily", "time": "09:00", "timezone": "Africa/Johannesburg", "days": "1"}

    assert harness._owned_automation_schedule_from_prompt("Change this schedule to 10:00", current)["time"] == "10:00"
    assert harness._owned_automation_schedule_from_prompt("Run monthly on day 15 at 07:30", current) == {
        "kind": "monthly",
        "time": "07:30",
        "days": "15",
        "timezone": "Africa/Johannesburg",
    }
    assert harness._owned_automation_schedule_from_prompt("Run every 20 minutes", current) == {
        "kind": "interval",
        "interval": 20,
        "interval_unit": "minutes",
        "timezone": "Africa/Johannesburg",
    }
    once = harness._owned_automation_schedule_from_prompt("Run once tomorrow at 08:15", current)
    assert once["kind"] == "once"
    assert once["run_at"].endswith("T08:15")


def test_owned_automation_delete_confirmation_expires_rejects_and_clears_on_intervening_turn(tmp_path, monkeypatch):
    get_session, chat_id, _ = _database(tmp_path, monkeypatch)
    metadata = {"source_type": "automation", "source_ref": "auto_9"}
    automation = {"id": "auto_9", "schedule": {"kind": "daily", "time": "09:00"}}
    turn = {"id": 11}
    deleted = []
    notices = []
    monkeypatch.setattr("distr.core.automation.store.get_automation", lambda automation_id: dict(automation))
    monkeypatch.setattr("distr.core.automation.store.delete_automation", lambda automation_id: deleted.append(automation_id) or True)
    monkeypatch.setattr("distr.core.chat_turns.latest_active_turn_id", lambda selected: turn["id"])
    monkeypatch.setattr(harness.ChatService, "append_assistant_notice", lambda target, text: notices.append(text))

    def set_pending(expires_at: str):
        with get_session() as db:
            root = db.get(Chat, chat_id)
            params = json.loads(root.params)
            params["automation_delete_confirmation"] = {
                "automation_id": "auto_9",
                "requested_turn_id": 10,
                "expires_at": expires_at,
            }
            root.params = json.dumps(params)
            db.commit()

    set_pending((utc_now_naive() - timedelta(minutes=1)).isoformat())
    assert harness._handle_owned_automation_control(chat_id, "yes delete it", metadata, assessment={}) is None
    assert deleted == []

    set_pending((utc_now_naive() + timedelta(minutes=5)).isoformat())
    rejected = harness._handle_owned_automation_control(chat_id, "no, keep it", metadata, assessment={})
    assert rejected["automation_control"] == "delete_cancelled"
    assert deleted == []

    set_pending((utc_now_naive() + timedelta(minutes=5)).isoformat())
    assert harness._handle_owned_automation_control(chat_id, "what time does it run?", metadata, assessment={}) is None
    turn["id"] = 12
    assert harness._handle_owned_automation_control(chat_id, "yes delete it", metadata, assessment={}) is None
    assert deleted == []


def _database(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'development-harness.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)

    @contextlib.contextmanager
    def get_session():
        session = factory()
        try:
            yield session
        finally:
            session.close()

    for target in (
        harness,
        __import__("distr.core.chat", fromlist=["chat"]),
        __import__("distr.core.chat_turns", fromlist=["chat_turns"]),
    ):
        monkeypatch.setattr(target, "get_session", get_session)
    monkeypatch.setattr("distr.gui.web.workflow_events.increment_workflow_updated", lambda: None)

    project_root = tmp_path / "project"
    project_root.mkdir()
    with get_session() as db:
        project = Project(name="Demo", folder_location=str(project_root), coding_backend="pi")
        db.add(project)
        db.flush()
        root = Chat(
            title="Build the feature",
            project_id=project.id,
            params=json.dumps(
                {
                    "development": {
                        "ticket_id": None,
                        "model_route": {
                            "route_mode": "auto",
                            "reasoning_effort": "medium",
                            "service_tier": "standard",
                        },
                    }
                }
            ),
            route_mode="auto",
            execution_profile="code",
            autonomy_level="full",
        )
        db.add(root)
        db.flush()
        turn = Chat(parent_id=root.id, input="Implement the feature")
        db.add(turn)
        db.flush()
        params = json.loads(root.params)
        params["active_turn_chat_row_id"] = turn.id
        root.params = json.dumps(params)
        db.add(
            DevelopmentWorkItem(
                chat_id=root.id,
                identity_key=f"chat:{root.id}",
                project_id=project.id,
                workflow_id=None,
            )
        )
        db.commit()
        return get_session, root.id, turn.id


def test_direct_development_prompt_uses_ide_harness_without_workflow(tmp_path, monkeypatch):
    get_session, chat_id, turn_id = _database(tmp_path, monkeypatch)
    captured = {}

    async def fake_dispatch(context):
        captured["context"] = context
        context.on_event({'type': 'tool_execution_end', 'toolName': 'bash', 'args': {'command': 'npm test'},
                          'result': {'structuredContent': {'exit_code': 0}}})
        context.on_event(
            {
                "type": "message_update",
                "summary": "Updating project files.",
                "execution_session_id": 91,
            }
        )
        return SimpleNamespace(
            execution_session_id=91,
            result=SimpleNamespace(
                success=True,
                waits_for_human=False,
                output="Implemented and verified the feature.",
                error="",
            ),
        )

    monkeypatch.setattr("distr.core.project_cli_backends.harness.dispatch_harness", fake_dispatch)
    monkeypatch.setattr("distr.core.skills.catalog.filter_known_skill_ids", lambda skill_ids: list(skill_ids))
    monkeypatch.setattr(
        "distr.core.project_cli_backends.catalog_probe.probe_cli_backend",
        lambda backend_id, settings=None: {"status": {"ready": True}, "next_step": ""},
    )

    result = harness.dispatch_development_prompt(
        chat_id,
        "Implement the feature",
        routing_assessment={
            "complexity": "medium",
            "route": {"backend": "codex", "model": "auto"},
        },
        skill_ids=["humanizer"],
        dispatch_async=False,
    )

    assert result["direct"] is True
    context = captured["context"]
    assert context.chat_id == chat_id
    assert context.workflow_id is None
    assert context.run_id is None
    assert context.backend_id == "codex"
    assert context.origin == "development"
    assert "This Development thread is the ticket and the current work item" in context.instruction
    assert "Do not create another ticket, issue, or ticket file" in context.instruction
    assert "keep this thread as the owner" in context.instruction
    assert "Provider: openai" in context.instruction
    assert "Model: auto" in context.instruction
    assert "Turn runtime: cli_harness" in context.instruction
    assert "Configured routing backend: codex" in context.instruction
    assert "configured routing backend are not the model provider" in context.instruction
    assert "Skills selected by the user for this turn: humanizer" in context.instruction
    assert "Do not search or list the skill catalog first" in context.instruction
    assert "Current user instruction:\nImplement the feature" in context.instruction

    with get_session() as db:
        root = db.get(Chat, chat_id)
        turn = db.get(Chat, turn_id)
        item = db.query(DevelopmentWorkItem).filter_by(chat_id=chat_id).one()
        execution = json.loads(root.params)["development"]["execution"]
        assert item.workflow_id is None
        assert execution["status"] == "completed"
        assert execution["execution_session_id"] == 91
        assert execution["route_receipt"]["complexity"] == "medium"
        assert execution["route_receipt"]["selected"]["backend"] == "codex"
        assert execution["route_receipt"]["actual"]["runtime"] == "cli_harness"
        assert turn.response == "Implemented and verified the feature."


def test_source_context_reaches_execution_and_blocker_stays_on_same_thread(tmp_path, monkeypatch):
    get_session, chat_id, turn_id = _database(tmp_path, monkeypatch)
    monkeypatch.setattr("distr.core.db.get_session", get_session)
    with get_session() as db:
        chat = db.get(Chat, chat_id)
        params = json.loads(chat.params)
        params["incoming_source_context"] = {"source": "whatsapp", "source_thread_id": "fixture@g.us", "message_ids": ["credential-ref"]}
        chat.params = json.dumps(params); db.commit()
    captured = {}
    async def dispatch(context):
        captured["instruction"] = context.instruction
        return SimpleNamespace(execution_session_id=91, result=SimpleNamespace(
            success=True, waits_for_human=False, output="Status: needs_input", error=""))
    monkeypatch.setattr("distr.core.project_cli_backends.harness.dispatch_harness", dispatch)
    monkeypatch.setattr("distr.core.project_cli_backends.catalog_probe.probe_cli_backend", lambda *a, **kw: {"status": {"ready": True}})
    harness.dispatch_development_prompt(chat_id, "Retry the original quotes", routing_assessment={
        "route": {"backend": "codex", "model": "auto"}}, dispatch_async=False)
    assert "credential-ref" in captured["instruction"] and "source_context" in captured["instruction"]
    with get_session() as db:
        root = db.get(Chat, chat_id)
        execution = json.loads(root.params)["development"]["execution"]
        assert execution["status"] == "blocked"
        assert not execution["outcome"]["success"]
        assert db.query(DevelopmentWorkItem).filter_by(chat_id=chat_id).count() == 1


def test_ticket_snapshot_keeps_more_than_one_hundred_attached_files(tmp_path, monkeypatch):
    home = tmp_path / "home"
    project = tmp_path / "project"
    attachment_root = project / ".decisions" / "intake" / "ticket-230"
    attachment_root.mkdir(parents=True)
    raw = []
    for index in range(118):
        path = attachment_root / f"reference-{index:03d}.png"
        path.write_bytes(b"image")
        raw.append({"name": path.name, "path": str(path), "mime_type": "image/png", "size": 5})
    monkeypatch.setenv("HOME", str(home))

    attachments = harness._safe_turn_attachments(raw, project_folder=str(project))

    assert len(attachments) == 118
    assert attachments[-1]["name"] == "reference-117.png"


def test_turn_change_manifest_reviews_and_safely_undoes_isolated_files(tmp_path, monkeypatch):
    get_session, chat_id, _ = _database(tmp_path, monkeypatch)
    root = tmp_path / "project"
    subprocess.run(["git", "init", str(root)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(root), "config", "user.email", "test@example.com"], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.name", "Test"], check=True)
    tracked = root / "index.html"
    tracked.write_text("<h1>Before</h1>\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(root), "add", "index.html"], check=True)
    subprocess.run(["git", "-C", str(root), "commit", "-m", "initial"], check=True, capture_output=True)

    before = harness._git_status_snapshot(str(root))
    tracked.write_text("<h1>After</h1>\n", encoding="utf-8")
    (root / "new.css").write_text("h1 { color: red; }\n", encoding="utf-8")
    changes = harness._turn_change_manifest(str(root), before)
    assert [item["path"] for item in changes["files"]] == ["index.html", "new.css"]
    assert changes["reversible"] is True
    assert changes["additions"] >= 2

    with get_session() as db:
        chat = db.get(Chat, chat_id)
        params = json.loads(chat.params)
        params["development"]["execution"] = {"job_id": "turn-1", "changes": changes}
        chat.params = json.dumps(params)
        db.commit()

    review = harness.development_change_review(chat_id)
    assert "diff --git" in review["diff"]
    review_by_path = {item["path"]: item for item in review["review_files"]}
    assert review_by_path["index.html"]["hunks"][0]["lines"] == [
        {"kind": "deletion", "old_line": 1, "new_line": None, "content": "<h1>Before</h1>"},
        {"kind": "addition", "old_line": None, "new_line": 1, "content": "<h1>After</h1>"},
    ]
    assert review_by_path["new.css"]["hunks"][0]["lines"] == [
        {"kind": "addition", "old_line": None, "new_line": 1, "content": "h1 { color: red; }"},
    ]
    result = harness.undo_development_changes(chat_id)
    assert result["changes"]["undone"] is True
    assert tracked.read_text(encoding="utf-8") == "<h1>Before</h1>\n"
    assert not (root / "new.css").exists()


def test_turn_change_manifest_counts_edits_to_previously_untracked_files(tmp_path):
    root = tmp_path / "project"
    subprocess.run(["git", "init", str(root)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(root), "config", "user.email", "test@example.com"], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.name", "Test"], check=True)
    tracked = root / "readme.md"
    tracked.write_text("tracked\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(root), "add", "readme.md"], check=True)
    subprocess.run(["git", "-C", str(root), "commit", "-m", "initial"], check=True, capture_output=True)

    scratch = root / "scratch.notes"
    scratch.write_text("alpha\n", encoding="utf-8")
    before = harness._git_status_snapshot(str(root))
    before_untracked = harness._git_untracked_fingerprints(str(root), before)
    assert before.get("scratch.notes") == "??"
    assert "scratch.notes" in before_untracked

    # Status stays ?? — this is the live harness bug: content-only untracked edits
    # were invisible to the change counter and Studio reported noop.
    scratch.write_text("alpha\nbeta\ngamma\n", encoding="utf-8")
    unchanged = harness._turn_change_manifest(str(root), before)
    assert unchanged["files"] == []
    assert unchanged["additions"] == 0

    changes = harness._turn_change_manifest(
        str(root),
        before,
        before_untracked=before_untracked,
    )
    assert [item["path"] for item in changes["files"]] == ["scratch.notes"]
    assert changes["files"][0]["status"] == "??"
    assert changes["additions"] >= 3
    assert changes["additions"] > 0
    assert changes["reversible"] is False
    outcome = harness._turn_outcome(
        backend_id="cli",
        runtime_id="cli_harness",
        success=True,
        result=SimpleNamespace(evidence={}),
        changes=changes,
        summary="edited scratch",
        noop=False,
    )
    assert outcome["git_change_count"] == 1
    assert "noop" not in outcome


def test_turn_change_manifest_still_counts_brand_new_untracked_files(tmp_path):
    root = tmp_path / "project"
    subprocess.run(["git", "init", str(root)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(root), "config", "user.email", "test@example.com"], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.name", "Test"], check=True)
    tracked = root / "readme.md"
    tracked.write_text("tracked\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(root), "add", "readme.md"], check=True)
    subprocess.run(["git", "-C", str(root), "commit", "-m", "initial"], check=True, capture_output=True)

    before = harness._git_status_snapshot(str(root))
    before_untracked = harness._git_untracked_fingerprints(str(root), before)
    assert before == {}

    (root / "fresh.txt").write_text("one\ntwo\n", encoding="utf-8")
    changes = harness._turn_change_manifest(
        str(root),
        before,
        before_untracked=before_untracked,
    )
    assert [item["path"] for item in changes["files"]] == ["fresh.txt"]
    assert changes["additions"] == 2
    assert changes["reversible"] is True


def test_completed_turn_can_start_a_later_turn_in_the_same_thread(tmp_path, monkeypatch):
    from distr.core.db.kanban import ProjectExecutionSession
    from distr.core.kanban import project_execution

    get_session, chat_id, _ = _database(tmp_path, monkeypatch)
    monkeypatch.setattr(project_execution, "get_session", get_session)
    monkeypatch.setattr(project_execution, "ensure_project_execution_tables", lambda: None)
    dispatches = []

    async def fake_dispatch(context):
        dispatches.append(context)
        session_id = 90 + len(dispatches)
        with get_session() as db:
            db.add(ProjectExecutionSession(id=session_id, project_id=1, status="completed",
                                           input_packet=json.dumps({"chat_id": chat_id})))
            db.commit()
        return SimpleNamespace(
            execution_session_id=session_id,
            result=SimpleNamespace(
                success=True,
                waits_for_human=False,
                output=f"Completed turn {len(dispatches)}.",
                error="",
            ),
        )

    monkeypatch.setattr("distr.core.project_cli_backends.harness.dispatch_harness", fake_dispatch)
    monkeypatch.setattr(
        "distr.core.project_cli_backends.catalog_probe.probe_cli_backend",
        lambda backend_id, settings=None: {"status": {"ready": True}, "next_step": ""},
    )

    first = harness.dispatch_development_prompt(chat_id, "First instruction", dispatch_async=False)
    second = harness.dispatch_development_prompt(chat_id, "Follow-up instruction", dispatch_async=False)

    assert len(dispatches) == 2
    assert first["job_id"] != second["job_id"]
    assert second["status"] == "completed"
    assert second["execution_session_id"] == 92
    assert second["summary"] == "Completed turn 2."
    with get_session() as db:
        execution = json.loads(db.get(Chat, chat_id).params)["development"]["execution"]
        assert execution["job_id"] == second["job_id"]
        assert execution["execution_session_id"] == 92
        assert execution["completed_at"]


def test_large_multi_phase_thread_request_stays_direct_by_default(tmp_path, monkeypatch):
    _, chat_id, _ = _database(tmp_path, monkeypatch)
    # Auto-wrap into managed class workflows is retired. Even a large multi-phase
    # prompt stays on the direct harness unless the caller passed a named workflow_id.
    monkeypatch.setattr(
        "distr.core.workflow.work_dispatch.dispatch_work_item",
        lambda **kwargs: (_ for _ in ()).throw(RuntimeError("dispatch_work_item must not auto-wrap")),
    )
    monkeypatch.setattr(
        "distr.core.workflow.phases.route_ticket_plan",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("route_ticket_plan must not auto-wrap")),
    )

    async def fake_dispatch(context):
        return SimpleNamespace(
            execution_session_id=77,
            result=SimpleNamespace(
                success=True,
                waits_for_human=False,
                output="Direct turn completed.",
                error="",
            ),
        )

    monkeypatch.setattr("distr.core.project_cli_backends.harness.dispatch_harness", fake_dispatch)
    monkeypatch.setattr(
        "distr.core.project_cli_backends.catalog_probe.probe_cli_backend",
        lambda backend_id, settings=None: {"status": {"ready": True}, "next_step": ""},
    )

    result = harness.dispatch_development_prompt(
        chat_id,
        "Implement and test this end-to-end workflow across the project with independent review.",
        routing_assessment={
            "complexity": "high",
            "operational_state": "risk",
            "route": {"backend": "codex", "model": "gpt-test"},
        },
        dispatch_async=False,
    )

    assert result.get("direct", True) is not False or result.get("execution_mode") in {None, "direct"}
    assert result.get("execution_mode") != "workflow"
    assert "orchestrator_agent" not in result or result.get("orchestrator_agent") != "workflow_orchestrator"


def test_plan_mode_keeps_the_direct_harness_read_only(tmp_path, monkeypatch):
    get_session, chat_id, _ = _database(tmp_path, monkeypatch)
    with get_session() as db:
        root = db.get(Chat, chat_id)
        root.autonomy_level = "plan"
        db.commit()
    captured = {}

    async def fake_dispatch(context):
        captured["context"] = context
        return SimpleNamespace(
            execution_session_id=None,
            result=SimpleNamespace(success=True, waits_for_human=False, output="Plan ready.", error=""),
        )

    monkeypatch.setattr("distr.core.project_cli_backends.harness.dispatch_harness", fake_dispatch)
    monkeypatch.setattr(
        "distr.core.project_cli_backends.catalog_probe.probe_cli_backend",
        lambda backend_id, settings=None: {"status": {"ready": True}, "next_step": ""},
    )
    harness.dispatch_development_prompt(chat_id, "Plan the feature", dispatch_async=False)

    assert captured["context"].adapter_options["read_only_expected"] is True
    assert "Work in plan-only mode" in captured["context"].instruction


def test_direct_development_can_use_native_runtime_without_workflow_identity(tmp_path, monkeypatch):
    from distr.core.turn_runtime import TurnEvent, TurnEventKind, TurnResult, TurnStatus

    get_session, chat_id, _ = _database(tmp_path, monkeypatch)
    captured = {}

    class FakeNativeRuntime:
        runtime_id = "native"

        async def execute(self, request, *, on_event=None, cancellation=None, steering=None):
            captured["request"] = request
            on_event(
                TurnEvent(
                    kind=TurnEventKind.STATUS,
                    status=TurnStatus.THINKING,
                    summary="Thinking through the request.",
                    runtime_id="native",
                )
            )
            on_event(
                TurnEvent(
                    kind=TurnEventKind.OUTPUT_DELTA,
                    status=TurnStatus.UPDATING,
                    summary="Updating the response.",
                    runtime_id="native",
                    output_delta="Native partial response.",
                )
            )
            captured["streamed_during"] = harness.development_execution_state(chat_id).get("streamed_output")
            on_event(TurnEvent(kind=TurnEventKind.TOOL_COMPLETED, status=TurnStatus.UPDATING,
                runtime_id='native', summary='Tests passed', tool_name='run_command',
                details={'command': 'npm test', 'exit_code': 0}))
            return TurnResult(
                success=True,
                runtime_id="native",
                backend_id="native",
                model=request.route.model,
                output="Native turn complete.",
            )

    monkeypatch.setenv("DECISIONSAI_DEVELOPMENT_TURN_RUNTIME", "native")
    monkeypatch.setattr("distr.core.turn_runtime.service._create_native_execution_session", lambda request: None)
    monkeypatch.setattr("distr.core.turn_runtime.service.get_turn_runtime", lambda runtime_id, request=None: FakeNativeRuntime())
    result = harness.dispatch_development_prompt(
        chat_id,
        "Implement natively",
        routing_assessment={
            "complexity": "medium",
            "route": {"backend": "pi", "model": "free-model", "model_provider": "kilocode"},
        },
        dispatch_async=False,
    )

    request = captured["request"]
    assert request.metadata["source"] == "development"
    assert "workflow_id" not in request.metadata
    assert "run_id" not in request.metadata
    assert result["runtime_id"] == "native"
    assert result["status"] == "completed"
    assert result["summary"] == "Native turn complete."
    assert captured["streamed_during"] == "Native partial response."
    assert result["streamed_output"] == ""


def test_project_without_a_folder_gets_an_organized_development_workspace(tmp_path, monkeypatch):
    get_session, chat_id, _ = _database(tmp_path, monkeypatch)
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    with get_session() as db:
        root = db.get(Chat, chat_id)
        project = db.get(Project, root.project_id)
        project.folder_location = ""
        db.commit()

    project, _, _, _ = harness._project_and_scope(chat_id)

    assert project.folder_location == str(home / ".decisions" / "work" / "demo")
    assert Path(project.folder_location).is_dir()


def test_stale_native_execution_recovers_as_interrupted_after_restart(tmp_path, monkeypatch):
    get_session, chat_id, _ = _database(tmp_path, monkeypatch)
    with get_session() as db:
        root = db.get(Chat, chat_id)
        params = json.loads(root.params)
        params["development"]["execution"] = {
            "job_id": "stale-native-turn",
            "runtime_id": "native",
            "status": "running",
            "started_at": (utc_now_naive() - timedelta(minutes=2)).isoformat(),
        }
        root.params = json.dumps(params)
        db.commit()
    monkeypatch.setattr("distr.core.turn_runtime.active_turn_registered", lambda _chat_id: False)

    state = harness.development_execution_state(chat_id)

    assert state["status"] == "failed"
    assert "interrupted when DecisionsAI stopped" in state["error"]
    with get_session() as db:
        persisted = json.loads(db.get(Chat, chat_id).params)["development"]["execution"]
        assert persisted["status"] == "failed"


@pytest.mark.parametrize("receipt_saved", [True, False])
def test_resume_interrupted_native_turn_does_not_repeat_disk_write(tmp_path, monkeypatch, receipt_saved):
    from distr.core.turn_runtime import ModelResponse, ModelToolCall, NativeTurnRuntime, ProjectToolExecutor
    from distr.core.turn_runtime import recovery, service
    from distr.core.db.kanban import ProjectExecutionEvent
    from distr.core.workflow.dispatcher import _reconcile_terminal_execution_sessions

    class ProcessInterrupted(BaseException):
        pass

    get_session, chat_id, turn_id = _database(tmp_path, monkeypatch)
    folder = tmp_path / "project"
    original = "Write first.txt and second.txt, then run the tests."
    with get_session() as db:
        db.get(Chat, turn_id).input = original
        db.commit()
    monkeypatch.setattr(recovery, "get_session", get_session)
    monkeypatch.setattr("distr.core.kanban.project_execution.get_session", get_session)
    monkeypatch.setattr("distr.core.kanban.project_execution.ensure_project_execution_tables", lambda: None)
    monkeypatch.setattr(service, "_record_native_certification", lambda *args: None)
    monkeypatch.setattr("distr.core.turn_runtime.select_turn_runtime_id", lambda *args, **kwargs: "native")
    monkeypatch.setattr(harness, "_route", lambda *args: ("native", "test-model", {"model_provider": "ollama"}, "medium"))
    first = ModelToolCall("first", "write_file", {"path": "first.txt", "content": "first"})
    repeated = ModelToolCall("new-id", "write_file", {"path": "./first.txt", "content": "first", "overwrite": True})
    second = ModelToolCall("second", "write_file", {"path": "second.txt", "content": "second"})
    requests = []
    writes = []

    class Model:
        def __init__(self, resumed):
            self.resumed = resumed
            self.calls = 0

        async def complete(self, messages, tools, *, on_delta=None):
            requests.append(list(messages))
            self.calls += 1
            if self.calls == 1:
                return ModelResponse(tool_calls=(repeated, second) if self.resumed else (first,))
            if not self.resumed:
                raise ProcessInterrupted()
            return ModelResponse(text="Both files are present.")

    class Tools(ProjectToolExecutor):
        def _atomic_write(self, path, content):
            writes.append(path.name)
            super()._atomic_write(path, content)
            if not receipt_saved:
                raise ProcessInterrupted()

    def runtime(_runtime_id, *, request):
        return NativeTurnRuntime(model=Model(bool(request.metadata.get("resume_session_id"))), tools=Tools(str(folder)))

    monkeypatch.setattr(service, "get_turn_runtime", runtime)
    with pytest.raises(ProcessInterrupted):
        harness.dispatch_development_prompt(chat_id, original, dispatch_async=False)
    assert (folder / "first.txt").read_text() == "first"
    with get_session() as db:
        assert len(_reconcile_terminal_execution_sessions(db)) == 1
        db.commit()

    if not receipt_saved:
        with pytest.raises(ValueError, match="no successful receipt"):
            harness.dispatch_development_prompt(chat_id, "resume", save_prompt=True, dispatch_async=False)
        assert writes == ["first.txt"]
        assert not (folder / "second.txt").exists()
        return

    harness.dispatch_development_prompt(chat_id, "resume", save_prompt=True, dispatch_async=False)

    assert writes == ["first.txt", "second.txt"]
    assert (folder / "second.txt").read_text() == "second"
    assert requests[2][-1]["content"] == original
    assert "Skipped a completed write" in requests[3][-2]["content"]
    with get_session() as db:
        assert db.query(ProjectExecutionEvent).filter_by(event_type="mutation_finished").count() == 2


@pytest.mark.parametrize("scope", ["project", "ticket", "chat", "turn", "malformed", "missing", "matching"])
def test_development_status_only_reconciles_its_own_session(tmp_path, monkeypatch, scope):
    from distr.core.db.kanban import KanbanBoard, KanbanLane, KanbanTicket, ProjectExecutionSession

    get_session, chat_id, turn_id = _database(tmp_path, monkeypatch)
    with get_session() as db:
        board = KanbanBoard(name="Test board", default_project_id=1)
        db.add(board)
        db.flush()
        lane = KanbanLane(board_id=board.id, name="Doing")
        db.add(lane)
        db.flush()
        ticket = KanbanTicket(lane_id=lane.id, title="Own ticket", workflow_status="running")
        db.add(ticket)
        db.flush()
        ticket_id = ticket.id
        db.query(DevelopmentWorkItem).filter_by(chat_id=chat_id).one().local_ticket_id = ticket_id
        packet = {"chat_id": chat_id + 1 if scope == "chat" else chat_id,
                  "turn_id": turn_id + 1 if scope == "turn" else turn_id}
        row = ProjectExecutionSession(project_id=2 if scope == "project" else 1,
                                      ticket_id=ticket_id + 1 if scope == "ticket" else ticket_id,
                                      input_packet=json.dumps(packet), status="failed", error="Fixture provider failed",
                                      output_packet=json.dumps({"result": "Fixture worker output"}))
        db.add(row)
        db.flush()
        reference = {"malformed": f"{row.id}broken", "missing": row.id + 10}.get(scope, row.id)
        root = db.get(Chat, chat_id)
        params = json.loads(root.params)
        params["development"]["execution"] = {
            "status": "running", "job_id": "active-test-job", "turn_id": turn_id,
            "execution_session_id": reference,
        }
        root.params = json.dumps(params)
        db.commit()

    state = harness.development_execution_state(chat_id)
    expected = "failed" if scope == "matching" else "running"
    assert state["status"] == expected
    if scope != "matching":
        assert state["id"] == "active-test-job"
        assert "execution_session_id" not in state
        assert "Fixture provider failed" not in json.dumps(state)
        assert "Fixture worker output" not in json.dumps(state)
    with get_session() as db:
        persisted = json.loads(db.get(Chat, chat_id).params)
        assert persisted["development"]["execution"]["status"] == expected
        assert db.get(KanbanTicket, ticket_id).workflow_status == expected


def test_development_stop_does_not_complete_a_different_chats_session(tmp_path, monkeypatch):
    from distr.core.db.kanban import ProjectExecutionSession
    from distr.core.kanban import project_execution

    get_session, chat_id, _ = _database(tmp_path, monkeypatch)
    monkeypatch.setattr(project_execution, "get_session", get_session)
    monkeypatch.setattr(project_execution, "ensure_project_execution_tables", lambda: None)
    monkeypatch.setattr("distr.core.project_cli_backends.registry.terminate_backend_process", lambda *args, **kwargs: False)
    with get_session() as db:
        row = ProjectExecutionSession(project_id=1, status="running",
                                      input_packet=json.dumps({"chat_id": chat_id + 1}))
        db.add(row)
        db.flush()
        session_id = row.id
        root = db.get(Chat, chat_id)
        params = json.loads(root.params)
        params["development"]["execution"] = {
            "status": "running", "job_id": "own-job", "execution_session_id": session_id,
        }
        root.params = json.dumps(params)
        db.commit()

    stopped = harness.stop_development_execution(chat_id)
    assert stopped["status"] == "cancelled"
    assert "execution_session_id" not in stopped
    with get_session() as db:
        assert db.get(ProjectExecutionSession, session_id).status == "running"


def test_development_workflow_status_and_stop_use_the_same_scope(tmp_path, monkeypatch):
    from distr.core.db.workflow import AutoWorkflowRun

    get_session, chat_id, _ = _database(tmp_path, monkeypatch)
    with get_session() as db:
        own = AutoWorkflowRun(chat_id=chat_id, workflow_id=1, status="waiting",
                              run_data=json.dumps({"project_id": "1"}))
        db.add(own)
        db.flush()
        own_id = own.id
        db.add_all([
            AutoWorkflowRun(chat_id=chat_id, workflow_id=2, status="running",
                            run_data=json.dumps({"project_id": 2})),
            AutoWorkflowRun(chat_id=chat_id, workflow_id=3, status="running", ticket_id=50),
        ])
        db.commit()

    cancelled = []
    monkeypatch.setattr("distr.core.workflow.dispatcher.cancel_run", lambda run_id: cancelled.append(run_id) or True)
    monkeypatch.setattr("distr.core.project_cli_backends.registry.terminate_backend_process", lambda *args, **kwargs: False)
    state = harness.development_execution_state(chat_id)
    assert state["id"] == own_id
    assert state["status"] == "waiting"
    assert harness.stop_development_execution(chat_id)["workflow_cancelled"]
    assert cancelled == [own_id]


def test_old_initiative_notice_does_not_hide_preserved_development_result():
    from distr.gui.web.routes.chat import _restore_development_execution_response

    messages = [
        {"role": "user", "content": "Add vegetarian filters to the menu."},
        {
            "role": "assistant",
            "content": "Pending approval - automation_recommendation: install WhatsApp intake.",
        },
    ]

    _restore_development_execution_response(
        messages,
        {"execution": {"summary": "Implemented the vegetarian filter and verified it in the browser."}},
    )

    assert messages[-1]["content"] == "Implemented the vegetarian filter and verified it in the browser."


def test_direct_visual_change_requires_a_real_project_mutation():
    assert harness._prompt_expects_project_change("give this site a footer", autonomy_level="full") is True
    assert harness._prompt_expects_project_change("can you change the footer colour", autonomy_level="full") is True


def test_questions_and_plan_mode_do_not_require_a_project_mutation():
    assert harness._prompt_expects_project_change("tell me about this project", autonomy_level="full") is False
    assert harness._prompt_expects_project_change("how would you add a footer?", autonomy_level="full") is False
    assert harness._prompt_expects_project_change("add a footer", autonomy_level="plan") is False


def test_auto_development_route_preserves_configured_free_fallback(monkeypatch):
    project = SimpleNamespace(id=2, coding_backend="cursor", coding_backend_model="")
    monkeypatch.setattr(
        "distr.core.pi_preflight.resolve_coding_cli_config",
        lambda project_id=None: ("ollama", "ornith:9b", "/tmp/project"),
    )
    monkeypatch.setattr(
        "distr.core.kanban.ticket_policy.resolve_ticket_cli_route",
        lambda project, complexity, board=None: {
            "complexity": complexity,
            "backend": "claude_code",
            "model": "opus",
            "fallback_backend": "pi",
            "fallback_model": "muse-glimmer:30b-mlx",
        },
    )

    backend, model, options, complexity = harness._route(
        project,
        {"model_route": {"route_mode": "auto"}},
        {
            "complexity": "high",
            "route": {
                "backend": "claude_code",
                "model": "opus",
                "fallback_backend": "pi",
                "fallback_model": "muse-glimmer:30b-mlx",
            },
        },
    )

    assert (backend, model, complexity) == ("claude_code", "opus", "high")
    assert options["model_provider"] == "anthropic"
    assert options["fallback_backend"] == "pi"
    assert options["fallback_model"] == "muse-glimmer:30b-mlx"
    assert options["fallback_model_provider"] == "ollama"


def test_auto_codex_route_passes_complexity_reasoning_and_service_tier(monkeypatch):
    project = SimpleNamespace(id=2, coding_backend="codex", coding_backend_model="auto")
    monkeypatch.setattr(
        "distr.core.kanban.ticket_policy.resolve_ticket_cli_route",
        lambda project, complexity, board=None: {
            "backend": "codex",
            "model": "gpt-5.6-luna",
            "model_provider": "openai",
            "codex_reasoning_effort": "high",
            "codex_service_tier": "fast",
        },
    )

    backend, model, options, complexity = harness._route(
        project,
        {"model_route": {"route_mode": "auto"}},
        {"complexity": "high"},
    )

    assert (backend, model, complexity) == ("codex", "gpt-5.6-luna", "high")
    assert options["reasoning_effort"] == "high"
    assert options["service_tier"] == "fast"


def test_direct_route_receipt_records_the_executor_that_returned_after_fallback(tmp_path, monkeypatch):
    from distr.core.turn_runtime import TurnResult

    _, chat_id, _ = _database(tmp_path, monkeypatch)

    async def fake_execute(request, **_kwargs):
        return TurnResult(
            success=True,
            runtime_id="cli_harness",
            backend_id="pi",
            model="muse-glimmer:30b-mlx",
            output="Fallback completed.",
        )

    monkeypatch.setattr("distr.core.turn_runtime.execute_turn", fake_execute)
    monkeypatch.setattr(
        "distr.core.project_cli_backends.catalog_probe.probe_cli_backend",
        lambda backend_id, settings=None: {"status": {"ready": True}, "next_step": ""},
    )

    result = harness.dispatch_development_prompt(
        chat_id,
        "Explain the current implementation",
        routing_assessment={
            "complexity": "high",
            "decision_engine": "laya",
            "laya": {"mode": "auto", "used": True, "confidence": 0.88},
            "route": {
                "backend": "claude_code",
                "model": "opus",
                "model_provider": "anthropic",
                "fallback_backend": "pi",
                "fallback_model": "muse-glimmer:30b-mlx",
                "fallback_model_provider": "ollama",
            },
        },
        dispatch_async=False,
    )

    actual = result["route_receipt"]["actual"]
    assert actual == {
        "backend": "pi",
        "provider": "ollama",
        "model": "muse-glimmer:30b-mlx",
        "runtime": "cli_harness",
    }


def test_manual_development_route_honors_pinned_harness(monkeypatch):
    project = SimpleNamespace(id=2, coding_backend="pi", coding_backend_model="")
    backend, model, options, complexity = harness._route(
        project,
        {
            "model_route": {
                "route_mode": "manual",
                "backend": "cursor",
                "provider": "",
                "model_name": "auto",
            }
        },
        {"complexity": "low", "route": {"backend": "codex", "model": "gpt-5"}},
    )
    assert (backend, model, complexity) == ("cursor", "auto", "low")
    assert "model_provider" not in options or options.get("model_provider") in ("", None)


def test_manual_development_route_defaults_missing_backend_to_pi(monkeypatch):
    project = SimpleNamespace(id=2, coding_backend="codex", coding_backend_model="gpt-5")
    monkeypatch.setattr(
        "distr.core.pi_preflight.resolve_coding_cli_config",
        lambda project_id=None: ("ollama", "local", "/tmp/project"),
    )
    backend, model, options, complexity = harness._route(
        project,
        {
            "model_route": {
                "route_mode": "manual",
                "backend": "",
                "provider": "ollama",
                "model_name": "local",
            }
        },
        {"complexity": "high", "route": {"backend": "codex", "model": "gpt-5"}},
    )
    assert (backend, model, complexity) == ("pi", "local", "high")
    assert options["model_provider"] == "ollama"


def test_manual_development_route_keeps_cli_pin_over_assessment(monkeypatch):
    project = SimpleNamespace(id=2, coding_backend="pi", coding_backend_model="")
    backend, model, options, complexity = harness._route(
        project,
        {
            "model_route": {
                "route_mode": "manual",
                "backend": "codex",
                "provider": "",
                "model_name": "auto",
            }
        },
        {"complexity": "low", "route": {"backend": "pi", "model": "local"}},
    )
    assert (backend, model, complexity) == ("codex", "auto", "low")
    assert options["model_provider"] == "openai"

