import json
from datetime import timedelta


def test_codex_rrule_translation_preserves_time_timezone_and_weekdays():
    from distr.core.automation.imports import schedule_from_rrule

    daily = schedule_from_rrule(
        "DTSTART;TZID=Africa/Johannesburg:20260829T090000\nRRULE:FREQ=DAILY"
    )
    weekdays = schedule_from_rrule(
        "DTSTART;TZID=Africa/Johannesburg:20260829T081500\nRRULE:FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR"
    )

    assert daily == {
        "kind": "daily",
        "time": "09:00",
        "run_at": "",
        "interval": 15,
        "interval_unit": "minutes",
        "days": "1",
        "timezone": "Africa/Johannesburg",
    }
    assert weekdays["kind"] == "weekly"
    assert weekdays["time"] == "08:15"
    assert weekdays["days"] == "1,2,3,4,5"


def test_import_is_idempotent_and_preserves_existing_decisions_edits(monkeypatch, tmp_path):
    from distr.core.automation import imports

    source_dir = tmp_path / "codex" / "morning-check"
    source_dir.mkdir(parents=True)
    (source_dir / "automation.toml").write_text(
        "\n".join(
            [
                'version = 1',
                'id = "morning-check"',
                'kind = "cron"',
                'name = "Morning check"',
                'prompt = "Check the deployment and report failures."',
                'status = "ACTIVE"',
                'rrule = "FREQ=DAILY;BYHOUR=9;BYMINUTE=0"',
                'model = "gpt-test"',
                'reasoning_effort = "low"',
            ]
        ),
        encoding="utf-8",
    )
    records = []

    def fake_list():
        return list(records)

    def fake_create(**payload):
        created = {
            "id": f"auto_{len(records) + 1}",
            "name": payload["name"],
            "instruction": payload["instruction"],
            "schedule": payload["schedule"],
            "status": payload["status"],
            "action_config": payload["action_config"],
        }
        records.append(created)
        return created

    monkeypatch.setitem(imports.SOURCE_DIRECTORIES, "codex", (tmp_path / "codex",))
    monkeypatch.setattr(imports, "list_automations", fake_list)
    monkeypatch.setattr(imports, "create_automation", fake_create)

    first = imports.import_automations(["codex"])
    records[0]["name"] = "Edited inside DecisionsAI"
    second = imports.import_automations(["codex"])

    assert first["imported_count"] == 1
    assert first["skipped_count"] == 0
    assert second["imported_count"] == 0
    assert second["skipped_count"] == 1
    assert records[0]["name"] == "Edited inside DecisionsAI"
    assert records[0]["action_config"]["import_key"] == "codex:morning-check"
    assert records[0]["action_config"]["model_provider"] == "openai"
    assert records[0]["action_config"]["run_in_new_thread"] is True


def test_import_route_can_update_existing_tasks_without_duplicates(monkeypatch, tmp_path):
    from distr.core.automation import imports

    source_dir = tmp_path / "codex" / "morning-check"
    source_dir.mkdir(parents=True)
    (source_dir / "automation.toml").write_text(
        '\n'.join([
            'id = "morning-check"',
            'name = "Morning check"',
            'prompt = "Check the deployment."',
            'model = "gpt-test"',
        ]),
        encoding="utf-8",
    )
    records = []

    def fake_create(**payload):
        created = {"id": "auto_1", **payload}
        records.append(created)
        return created

    def fake_update(automation_id, **fields):
        assert automation_id == "auto_1"
        records[0].update(fields)
        return dict(records[0])

    monkeypatch.setitem(imports.SOURCE_DIRECTORIES, "codex", (tmp_path / "codex",))
    monkeypatch.setattr(imports, "list_automations", lambda: list(records))
    monkeypatch.setattr(imports, "create_automation", fake_create)
    monkeypatch.setattr(imports, "update_automation", fake_update)

    routing = {
        "model_provider": "ollama",
        "model": "muse-glimmer:30b-mlx",
        "complexity": "medium",
        "reasoning_effort": "medium",
        "execution_environment": "local",
        "adaptive_model_routing": True,
        "update_existing": True,
    }
    first = imports.import_automations(["codex"], routing=routing)
    second = imports.import_automations(["codex"], routing=routing)

    assert first["imported_count"] == 1
    assert second["updated_count"] == 1
    assert second["skipped_count"] == 0
    assert len(records) == 1
    config = records[0]["action_config"]
    assert config["backend"] == "pi"
    assert config["model_provider"] == "ollama"
    assert config["model"] == "muse-glimmer:30b-mlx"
    assert config["complexity"] == "medium"
    assert config["adaptive_model_routing"] is True
    assert config["import_original_model"] == "gpt-test"


def test_clock_trigger_creates_independent_thread_and_test_cleans_it_up(monkeypatch):
    from distr.core.automation.scheduler import run_scheduled_automation
    from distr.core.automation.store import (
        create_automation,
        delete_automation,
        get_automation,
        list_automation_runs,
        utc_now,
    )
    from distr.core.db import Chat, get_session
    from distr.core.db.automation import Automation
    from distr.core.chat import cleanup_chat_dependencies, remove_chat_transcript_audit_events
    from distr.core import automation_orchestrator

    automation = create_automation(
        name="Clockwork thread test",
        automation_type="scheduled_instruction",
        status="active",
        instruction="Return a short scheduled test result.",
        preset_id="",
        schedule={"kind": "interval", "interval": 5, "interval_unit": "minutes"},
        action_config={
            "run_in_new_thread": True,
            "model_provider": "openai",
            "model": "gpt-test",
            "reasoning_effort": "low",
            "fallback_model_provider": "anthropic",
            "fallback_model": "claude-test",
        },
    )
    record_id = int(automation["record_id"])
    with get_session() as session:
        row = session.get(Automation, record_id)
        row.next_run_at = utc_now() - timedelta(minutes=1)
        session.commit()
    due = get_automation(automation["id"])
    original_dispatch = automation_orchestrator.dispatch_automation_to_current_chat

    dispatch_results = []
    def dispatch_without_agent_signal(item, **kwargs):
        result = original_dispatch(item, emit_signal=False, speak=False, **kwargs)
        dispatch_results.append(result)
        return result

    monkeypatch.setattr(
        automation_orchestrator,
        "dispatch_automation_to_current_chat",
        dispatch_without_agent_signal,
    )
    run_thread_id = None
    try:
        assert run_scheduled_automation(due) is True, dispatch_results
        runs = list_automation_runs(automation["id"])
        run_thread_id = int(runs[0]["chat_id"])
        with get_session() as session:
            thread = session.get(Chat, run_thread_id)
            assert thread is not None
            assert thread.parent_id is None
            assert thread.provider.lower() == "openai"
            assert thread.model_name == "gpt-test"
            metadata = json.loads(thread.params or "{}")["automation_run"]
            assert metadata["automation_id"] == automation["id"]
            assert metadata["fallback_model"] == "claude-test"
    finally:
        if not run_thread_id and dispatch_results:
            run_thread_id = dispatch_results[-1].get("chat_id")
        if run_thread_id:
            with get_session() as session:
                for child in session.query(Chat).filter(Chat.parent_id == run_thread_id).all():
                    session.delete(child)
                root = session.get(Chat, run_thread_id)
                if root is not None:
                    cleanup_chat_dependencies(session, run_thread_id)
                    session.delete(root)
                session.commit()
            remove_chat_transcript_audit_events(run_thread_id)
        delete_automation(automation["id"])

    with get_session() as session:
        assert session.get(Chat, run_thread_id) is None
