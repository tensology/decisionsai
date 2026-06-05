from fastapi import FastAPI
from fastapi.testclient import TestClient


def test_ide_bridge_creates_project_chat_and_appends_prompt_and_response(tmp_path):
    import distr.core.db.projects  # noqa: F401
    from distr.core.chat import ChatService
    from distr.core.db import get_session
    from distr.core.db.projects import Project
    from distr.gui.web.routes.ide_bridge import create_routes

    project_dir = tmp_path / "demo"
    nested_dir = project_dir / "src"
    nested_dir.mkdir(parents=True)

    with get_session() as session:
        project = Project(name="Demo IDE", folder_location=str(project_dir), coding_backend="cursor")
        session.add(project)
        session.commit()
        project_id = project.id

    app = FastAPI()
    app.include_router(create_routes(), prefix="/api")
    client = TestClient(app)

    create_resp = client.post(
        "/api/ide/sessions",
        json={"source": "cursor", "cwd": str(nested_dir)},
    )
    assert create_resp.status_code == 200
    created = create_resp.json()
    assert created["success"] is True
    assert created["project"]["id"] == project_id
    chat_id = created["chat_id"]
    session_id = created["session"]["id"]
    assert created["session"]["route_type"] == "ide_bridge"
    assert created["session"]["route_backend"] == "cursor"

    prompt_resp = client.post(
        "/api/ide/sessions/event",
        json={
            "source": "cursor",
            "cwd": str(nested_dir),
            "session_id": session_id,
            "event_type": "cursor_prompt_submitted",
            "status": "observed",
            "input": "Please refactor the dashboard.",
        },
    )
    assert prompt_resp.status_code == 200
    assert prompt_resp.json()["chat_id"] == chat_id

    complete_resp = client.post(
        "/api/ide/sessions/event",
        json={
            "source": "cursor",
            "cwd": str(nested_dir),
            "session_id": session_id,
            "event_type": "cursor_completed",
            "output": "Status: completed\nSummary: Dashboard refactored.",
        },
    )
    assert complete_resp.status_code == 200
    assert complete_resp.json()["session"]["status"] == "completed"

    history = ChatService.get_chat_history(chat_id)
    assert {"role": "user", "content": "[Cursor IDE] Please refactor the dashboard."} in history
    assert {
        "role": "assistant",
        "content": "[Cursor IDE] Status: completed\nSummary: Dashboard refactored.",
    } in history

    progress_resp = client.get("/api/ide/sessions/progress", params={"session_id": session_id})
    assert progress_resp.status_code == 200
    progress = progress_resp.json()
    assert progress["success"] is True
    assert progress["session"]["id"] == session_id
    assert progress["session"]["status"] == "completed"
    assert [event["event_type"] for event in progress["session"]["events"]][-2:] == [
        "cursor_prompt_submitted",
        "cursor_completed",
    ]


def test_ide_bridge_returns_404_when_folder_is_not_a_project(tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from distr.gui.web.routes.ide_bridge import create_routes

    app = FastAPI()
    app.include_router(create_routes(), prefix="/api")
    client = TestClient(app)

    resp = client.post(
        "/api/ide/sessions",
        json={"source": "codex", "cwd": str(tmp_path / "unknown")},
    )

    assert resp.status_code == 404
    assert resp.json()["success"] is False


def test_ide_bridge_rejects_session_id_from_other_project_or_source(tmp_path):
    import distr.core.db.projects  # noqa: F401
    from distr.core.db import get_session
    from distr.core.db.projects import Project
    from distr.gui.web.routes.ide_bridge import create_routes

    cursor_dir = tmp_path / "cursor-project"
    codex_dir = tmp_path / "codex-project"
    cursor_dir.mkdir()
    codex_dir.mkdir()

    with get_session() as session:
        cursor_project = Project(name="Cursor Project", folder_location=str(cursor_dir), coding_backend="cursor")
        codex_project = Project(name="Codex Project", folder_location=str(codex_dir), coding_backend="codex")
        session.add(cursor_project)
        session.add(codex_project)
        session.commit()

    app = FastAPI()
    app.include_router(create_routes(), prefix="/api")
    client = TestClient(app)

    created = client.post(
        "/api/ide/sessions",
        json={"source": "cursor", "cwd": str(cursor_dir)},
    ).json()
    session_id = created["session"]["id"]

    wrong_project = client.post(
        "/api/ide/sessions/event",
        json={
            "source": "cursor",
            "cwd": str(codex_dir),
            "session_id": session_id,
            "event_type": "cursor_prompt_submitted",
            "input": "Wrong project should not attach.",
        },
    )
    assert wrong_project.status_code == 404
    assert "does not match" in wrong_project.json()["error"]

    wrong_source = client.post(
        "/api/ide/sessions/event",
        json={
            "source": "codex",
            "cwd": str(cursor_dir),
            "session_id": session_id,
            "event_type": "codex_prompt_submitted",
            "input": "Wrong source should not attach.",
        },
    )
    assert wrong_source.status_code == 404
    assert "does not match" in wrong_source.json()["error"]


def test_conversation_origin_ide_event_reuses_current_chat_without_creating_one(tmp_path):
    import distr.core.db.projects  # noqa: F401
    from distr.core.chat import ChatService
    from distr.core.db import Chat, get_session
    from distr.core.db.projects import Project
    from distr.core.ide_bridge import record_ide_event

    project_dir = tmp_path / "demo"
    project_dir.mkdir()

    with get_session() as session:
        project = Project(name="Demo IDE", folder_location=str(project_dir), coding_backend="codex")
        session.add(project)
        session.commit()
        project_id = project.id

    chat_id, _ = ChatService.create_new_chat(title="Current working chat")
    with get_session() as session:
        root_count_before = session.query(Chat).filter(Chat.parent_id.is_(None)).count()

    result = record_ide_event(
        source="codex",
        cwd=str(project_dir),
        project_id=project_id,
        event_type="codex_prompt_submitted",
        input_text="Let's discuss what needs doing before dispatch.",
        allow_chat_creation=False,
    )

    assert result["chat_id"] == chat_id
    with get_session() as session:
        root_count_after = session.query(Chat).filter(Chat.parent_id.is_(None)).count()
    assert root_count_after == root_count_before
