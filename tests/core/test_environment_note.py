"""Per-run environment note and Project Notes prompt block (reference: Merrypak thread 1284)."""
from types import SimpleNamespace

from distr.core.turn_runtime.environment_note import (
    EnvironmentReceipts,
    build_environment_note,
    is_support_project,
    project_notes_block,
    refresh_commands,
)


def _kind(value):
    return SimpleNamespace(value=value)


def _run(receipts, commands):
    for index, (command, code) in enumerate(commands):
        receipts.observe(SimpleNamespace(kind=_kind("tool_started"), details={"call_id": str(index), "command": command}))
        receipts.observe(SimpleNamespace(kind=_kind("tool_completed"), details={"call_id": str(index), "exit_code": code}))


def _django_project(tmp_path, *, local_host="localhost"):
    webapp = tmp_path / "backend" / "webapp"
    (webapp / "core").mkdir(parents=True)
    (webapp / "manage.py").write_text("os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'core.settings')\n")
    (webapp / "core" / "settings.py").write_text(
        "DATABASES = {'default': {'ENGINE': 'django.db.backends.sqlite3', 'NAME': 'db.sqlite3'}}\n"
        "from .settings_local import *  # settings_local\n"
    )
    (webapp / "core" / "settings_local.py").write_text(
        "DATABASES = {\n    'default': {\n        'ENGINE': 'django.db.backends.postgresql_psycopg2',\n"
        "        'NAME': 'shopdb',\n        'USER': 'postgres',\n        'PASSWORD': 'sekret-value',\n"
        f"        'HOST': '{local_host}',\n        'PORT': 5432,\n    }},\n}}\n"
    )
    return tmp_path


def test_records_local_db_refresh_and_read_only_prod_check(tmp_path):
    folder = _django_project(tmp_path)
    receipts = EnvironmentReceipts()
    _run(receipts, [
        ("cat load_latest_db.sh", 0),  # reading the script is not running it
        ("./load_latest_db.sh --force", 0),
        ("python manage.py test apps.shop --keepdb --noinput", 0),
        ("ssh deploy@www.example.com 'cd site && python manage.py shell -c \"print(1)\"'", 0),
    ])
    note = build_environment_note(str(folder), "Refresh command: ./load_latest_db.sh", receipts)
    db = note["database"]
    assert (db["engine"], db["name"], db["host"], db["port"], db["scope"]) == ("Postgres", "shopdb", "localhost", "5432", "local")
    assert db["settings_module"] == "core.settings" and db["settings_file"].endswith("core/settings_local.py")
    assert note["refresh"]["ran"] is True and note["refresh"]["command"] == "./load_latest_db.sh"
    assert note["production_check"] == {**note["production_check"], "checked": True, "result": "passed", "read_only": True}
    assert "deploy@www.example.com" in note["production_check"]["how"]
    assert note["support_project"] is True
    # Credentials are never read into the note.
    assert "sekret" not in repr(note) and "postgres'" not in repr(note)


def test_cat_only_means_refresh_not_run_and_prod_write_is_flagged(tmp_path):
    folder = _django_project(tmp_path, local_host="db.example.com")
    receipts = EnvironmentReceipts()
    _run(receipts, [("cat load_latest_db.sh", 0), ("ssh deploy@www.example.com 'sudo -S systemctl restart merrypak'", 1)])
    note = build_environment_note(str(folder), "", receipts)
    assert note["refresh"] == {"command": "load_latest_db", "ran": False}
    assert note["database"]["scope"] == "remote"
    assert note["production_check"]["result"] == "failed" and note["production_check"]["read_only"] is False
    assert "not run" in note["summary"] and "possible write" in note["summary"]


def test_non_django_project_has_no_db_and_no_crash(tmp_path):
    note = build_environment_note(str(tmp_path), "", EnvironmentReceipts())
    assert note["database"] == {} and note["production_check"] == {"checked": False}
    assert note["support_project"] is False


def test_support_flag_and_refresh_commands_come_from_notes():
    assert is_support_project("Project type: support")
    assert is_support_project("- Refresh command: make pull-db")
    assert is_support_project("run load_latest_db first")
    assert not is_support_project("Deploy URL: https://example.com")
    assert refresh_commands("Refresh command: `make pull-db`") == ["make pull-db", "load_latest_db"]


def test_project_notes_block_includes_notes_and_support_standard():
    assert project_notes_block(SimpleNamespace(notes="")) == ""
    block = project_notes_block(SimpleNamespace(notes="Project type: support\nRefresh command: ./load_latest_db.sh"))
    assert block.startswith("[PROJECT NOTES]")
    assert "./load_latest_db.sh" in block and "READ-ONLY" in block
    plain = project_notes_block(SimpleNamespace(notes="Staging is at staging.example.com"))
    assert "staging.example.com" in plain and "Support-project standard" not in plain


def test_agent_instruction_includes_project_notes(monkeypatch):
    from distr.core.workflow import development_harness as harness

    monkeypatch.setattr(harness.ChatService, "get_chat_history", lambda chat_id: [])
    project = SimpleNamespace(notes="Project type: support\nRefresh command: ./load_latest_db.sh")
    instruction = harness._agent_instruction(73, "Investigate missing vouchers", {}, autonomy_level="full", project=project)
    assert "[PROJECT NOTES]" in instruction and "./load_latest_db.sh" in instruction
    assert instruction.index("[PROJECT NOTES]") < instruction.index("Current user instruction:")
    without = harness._agent_instruction(73, "Investigate", {}, autonomy_level="full")
    assert "[PROJECT NOTES]" not in without


def test_dispatch_stores_environment_note_with_the_run(tmp_path, monkeypatch):
    import json
    import subprocess

    from distr.core.db.kanban import ProjectExecutionSession
    from distr.core.db.projects import Project
    from distr.core.turn_runtime import TurnEvent, TurnEventKind, TurnResult, TurnStatus
    from distr.core.workflow import development_harness as harness
    from tests.core.test_development_harness import _database

    get_session, chat_id, _ = _database(tmp_path, monkeypatch)
    monkeypatch.setattr("distr.core.kanban.project_execution.get_session", get_session)
    monkeypatch.setattr("distr.core.kanban.project_execution.ensure_project_execution_tables", lambda: None)
    root = tmp_path / "project"
    _django_project(root)
    subprocess.run(["git", "init", "-b", "main", str(root)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(root), "add", "."], check=True)
    subprocess.run(["git", "-C", str(root), "-c", "user.name=Test", "-c", "user.email=test@example.com",
                    "commit", "-m", "baseline"], check=True, capture_output=True)
    with get_session() as db:
        project = db.query(Project).first()
        project.notes = "Project type: support\nRefresh command: ./load_latest_db.sh"
        row = ProjectExecutionSession(project_id=project.id, route_type="cli", route_backend="fixture", status="completed",
                                      output_packet=json.dumps({}))
        db.add(row)
        db.commit()
        session_id = row.id
    seen = {}

    async def execute(request, *, on_event, **kwargs):
        seen["instruction"] = request.instruction
        (root / "app.txt").write_text("implemented\n")
        for index, (command, code) in enumerate([("./load_latest_db.sh --force", 0), ("pytest -q", 0),
                                                 ("ssh deploy@www.example.com 'python manage.py shell -c \"print(1)\"'", 0)]):
            on_event(TurnEvent(kind=TurnEventKind.TOOL_COMPLETED, status=TurnStatus.UPDATING, summary="done", runtime_id="cli_harness",
                               tool_name="bash", details={"command": command, "exit_code": code, "call_id": str(index)}))
        return TurnResult(success=True, backend_id="codex", model="fixture", runtime_id="cli_harness",
                          execution_session_id=session_id, output="Fixed and verified.")

    monkeypatch.setattr("distr.core.turn_runtime.execute_turn", execute)
    monkeypatch.setattr("distr.core.project_cli_backends.catalog_probe.probe_cli_backend", lambda *a, **k: {"status": {"ready": True}})
    result = harness.dispatch_development_prompt(chat_id, "Fix the voucher import and run the tests", dispatch_async=False)
    assert "[PROJECT NOTES]" in seen["instruction"]
    note = result["outcome"]["environment"]
    assert note["database"]["name"] == "shopdb" and note["database"]["scope"] == "local"
    assert note["refresh"]["ran"] is True
    assert note["production_check"]["checked"] is True and note["production_check"]["read_only"] is True
    with get_session() as db:
        packet = json.loads(db.get(ProjectExecutionSession, session_id).output_packet)
        assert packet["completion_review"]["environment"]["refresh"]["ran"] is True
