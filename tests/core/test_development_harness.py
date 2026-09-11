from __future__ import annotations

import contextlib
import json
import subprocess
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
        assert turn.response == "Implemented and verified the feature."


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


def test_completed_turn_can_start_a_later_turn_in_the_same_thread(tmp_path, monkeypatch):
    get_session, chat_id, _ = _database(tmp_path, monkeypatch)
    dispatches = []

    async def fake_dispatch(context):
        dispatches.append(context)
        session_id = 90 + len(dispatches)
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


def test_large_multi_phase_thread_request_launches_owned_orchestrator(tmp_path, monkeypatch):
    _, chat_id, _ = _database(tmp_path, monkeypatch)
    captured = {}
    monkeypatch.setattr(
        "distr.core.workflow.developer_workflow.resolve_development_workflow",
        lambda **_kwargs: 44,
    )

    def dispatch(**kwargs):
        captured.update(kwargs)
        return {"run_id": 91, "chat_id": chat_id, "status": "started"}

    monkeypatch.setattr("distr.core.workflow.work_dispatch.dispatch_work_item", dispatch)

    result = harness.dispatch_development_prompt(
        chat_id,
        "Implement and test this end-to-end workflow across the project with independent review.",
        routing_assessment={
            "complexity": "high",
            "operational_state": "risk",
            "route": {"backend": "codex", "model": "gpt-test"},
        },
        dispatch_async=True,
    )

    assert result["direct"] is False
    assert result["execution_mode"] == "workflow"
    assert result["orchestrator_agent"] == "workflow_orchestrator"
    assert captured["chat_id"] == chat_id
    assert captured["workflow_id"] == 44
    assert captured["run_metadata"]["execution_mode_decision"]["mode"] == "workflow"


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
