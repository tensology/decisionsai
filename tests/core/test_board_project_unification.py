from __future__ import annotations

import contextlib
import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from distr.core.db import Base, Chat
from distr.core.db.automation import Automation, AutomationRun
from distr.core.db.projects import Project
from distr.core.db.kanban import KanbanBoard, KanbanTicket, KanbanTicketFile
from distr.core.db.workflow import AutoWorkflow, DevelopmentWorkItem, PlanItem, PlanItemRevision, PlanWorkspace
from distr.gui.web.routes import kanban


ROOT = Path(__file__).resolve().parents[2]


def test_projects_navigation_is_replaced_by_the_development_board_surface():
    base = (ROOT / "distr/gui/web/templates/base.html").read_text(encoding="utf-8")
    server = (ROOT / "distr/gui/web/server.py").read_text(encoding="utf-8")
    from jinja2 import ChoiceLoader, DictLoader, Environment, FileSystemLoader
    environment = Environment(loader=ChoiceLoader([
        DictLoader({"base.html": "{% block head_styles %}{% endblock %}{% block content %}{% endblock %}{% block body_scripts %}{% endblock %}"}),
        FileSystemLoader(ROOT / "distr/gui/web/templates"),
    ]))
    studio = environment.get_template("development/index.html").render()

    assert 'href="/projects/' not in base
    assert 'href="/tickets/"' not in base
    assert 'RedirectResponse(url="/workflows/", status_code=302)' in server
    assert "Ticket boards</span>" not in studio
    assert ">Boards</span>" in studio
    assert 'id="kanban-frame"' not in studio
    assert '<iframe' not in studio
    assert '<script type="module" src="/static/development/app.js?' in studio
    assert ">Terminals</span>" in studio
    assert "Kanban" in studio
    assert "Project folder" in studio


@pytest.fixture()
def board_api(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'board-project.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)

    @contextlib.contextmanager
    def get_session():
        session = factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    monkeypatch.setattr(kanban, "get_session", get_session)
    monkeypatch.setattr("distr.core.workspace_memory.provision.bootstrap_board", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("distr.core.workspace_memory.lifecycle.hook_ensure_workspace", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("distr.core.workspace_memory.lifecycle.hook_remove_workspace", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("distr.core.workspace_memory.sync.sync_projection_for_project", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("distr.core.chat.remove_chat_transcript_audit_events", lambda *_args, **_kwargs: 0)

    app = FastAPI()
    app.include_router(kanban.create_routes(), prefix="/api")
    return TestClient(app), get_session, tmp_path


def test_board_crud_owns_the_project_folder_and_terminal_commands(board_api):
    client, get_session, tmp_path = board_api
    repository = tmp_path / "scraper"
    repository.mkdir()

    created = client.post("/api/tickets/boards", json={
        "name": "Market Scraper",
        "folder_location": str(repository),
        "startup_instructions": "npm run api\nnpm run worker",
    })
    assert created.status_code == 200
    board_id = created.json()["id"]
    project_id = created.json()["project_id"]

    listed = client.get("/api/tickets/boards").json()
    board = next(item for item in listed if item["id"] == board_id)
    assert board["default_project_id"] == project_id
    assert board["folder_location"] == str(repository)
    assert board["startup_instructions"] == "npm run api\nnpm run worker"

    updated = client.put(f"/api/tickets/boards/{board_id}", json={
        "name": "Trading Scraper",
        "folder_location": str(repository),
        "startup_instructions": "npm run dev",
    })
    assert updated.status_code == 200
    with get_session() as db:
        stored_board = db.get(KanbanBoard, board_id)
        stored_project = db.get(Project, project_id)
        assert stored_board.name == "Trading Scraper"
        assert stored_project.name == "Trading Scraper"
        assert stored_project.kanban_board_id == board_id
        assert stored_project.startup_instructions == "npm run dev"

    deleted = client.delete(f"/api/tickets/boards/{board_id}")
    assert deleted.status_code == 200
    assert deleted.json()["repository_deleted"] is False
    assert repository.is_dir()
    with get_session() as db:
        assert db.get(KanbanBoard, board_id) is None
        assert db.get(Project, project_id) is None


def test_board_delete_can_optionally_remove_only_the_exact_repository(board_api):
    client, _get_session, tmp_path = board_api
    repository = tmp_path / "managed" / "topic-repo"
    repository.mkdir(parents=True)
    (repository / "keep-unless-checked.txt").write_text("fixture", encoding="utf-8")
    created = client.post("/api/tickets/boards", json={
        "name": "Topic Repo",
        "folder_location": str(repository),
    }).json()

    deleted = client.delete(f"/api/tickets/boards/{created['id']}?delete_repository=true")
    assert deleted.status_code == 200
    assert deleted.json()["repository_path"] == str(repository)
    assert deleted.json()["repository_deleted"] is True
    assert not repository.exists()


def test_board_delete_cascades_its_ticket_thread_time_and_snapshot_files(board_api):
    client, get_session, tmp_path = board_api
    repository = tmp_path / "cascade" / "repo"
    intake = repository / ".decisions" / "intake" / "ticket"
    intake.mkdir(parents=True)
    transcript = intake / "transcript.md"
    transcript.write_text("WhatsApp transcript", encoding="utf-8")
    created = client.post("/api/tickets/boards", json={
        "name": "Cascade Board",
        "folder_location": str(repository),
    }).json()

    with get_session() as db:
        board = db.get(KanbanBoard, created["id"])
        workflow = AutoWorkflow(name="Snapshot work")
        root = Chat(title="Snapshot ticket", project_id=created["project_id"], params="{}")
        db.add_all([workflow, root])
        db.flush()
        root.params = f'{{"development":{{"workflow_id":{workflow.id}}}}}'
        child = Chat(parent_id=root.id, input="Snapshot prompt", response="Work complete")
        ticket = KanbanTicket(
            lane_id=board.lanes[0].id,
            title="Snapshot ticket",
            source_chat_id=root.id,
            linked_project_id=created["project_id"],
            linked_workflow_id=workflow.id,
        )
        db.add_all([child, ticket])
        db.flush()
        db.add_all([
            KanbanTicketFile(ticket_id=ticket.id, filename="transcript.md", file_path=str(transcript)),
            DevelopmentWorkItem(
                chat_id=root.id,
                identity_key=f"ticket:local:{ticket.id}",
                local_ticket_id=ticket.id,
                project_id=created["project_id"],
                workflow_id=workflow.id,
                time_accumulated_seconds=300,
            ),
        ])
        db.commit()
        root_id = root.id
        child_id = child.id

    deleted = client.delete(f"/api/tickets/boards/{created['id']}")
    assert deleted.status_code == 200
    assert repository.is_dir()
    assert not transcript.exists()
    with get_session() as db:
        assert db.get(Chat, root_id) is None
        assert db.get(Chat, child_id) is None
        assert db.query(DevelopmentWorkItem).filter_by(chat_id=root_id).first() is None


def test_board_delete_removes_board_automation_thread_and_plan_workspace(board_api):
    client, get_session, _tmp_path = board_api
    created = client.post("/api/tickets/boards", json={"name": "Owned planning"}).json()
    board_id = int(created["id"])
    project_id = int(created["project_id"])
    with get_session() as db:
        thread = Chat(title="Board automation", project_id=project_id, params="{}")
        db.add(thread)
        db.flush()
        db.add(DevelopmentWorkItem(
            chat_id=thread.id,
            identity_key="source:automation:auto_owned",
            source_type="automation",
            board_provider="local",
            board_key=f"decisions:{board_id}",
            project_id=project_id,
        ))
        automation = Automation(
            name="Owned automation",
            board_id=board_id,
            project_id=project_id,
            thread_chat_id=thread.id,
        )
        workspace = PlanWorkspace(
            board_key=f"decisions:{board_id}",
            board_provider="decisions",
            board_name="Owned planning",
            project_id=project_id,
        )
        db.add_all([automation, workspace])
        db.flush()
        db.add(AutomationRun(automation_id=automation.id, status="completed"))
        item = PlanItem(workspace_id=workspace.id, title="PRD", content="Requirements")
        db.add(item)
        db.flush()
        db.add(PlanItemRevision(item_id=item.id, revision=1, title="PRD", content="Requirements"))
        db.commit()
        thread_id = int(thread.id)
        automation_id = int(automation.id)
        workspace_id = int(workspace.id)

    assert client.delete(f"/api/tickets/boards/{board_id}").status_code == 200
    with get_session() as db:
        assert db.get(Chat, thread_id) is None
        assert db.get(Automation, automation_id) is None
        assert db.get(PlanWorkspace, workspace_id) is None


def test_ticket_delete_can_also_delete_its_linked_thread(board_api):
    client, get_session, _tmp_path = board_api
    created = client.post("/api/tickets/boards", json={"name": "Linked Ticket Board"}).json()

    with get_session() as db:
        board = db.get(KanbanBoard, created["id"])
        root = Chat(title="Linked work", project_id=created["project_id"], params="{}")
        db.add(root)
        db.flush()
        child = Chat(parent_id=root.id, input="Build it", response="Done")
        ticket = KanbanTicket(lane_id=board.lanes[0].id, title="Linked work", source_chat_id=root.id)
        db.add_all([child, ticket])
        db.flush()
        root.params = json.dumps({"development": {"ticket_id": ticket.id}})
        db.add(DevelopmentWorkItem(
            chat_id=root.id,
            identity_key=f"ticket:local:{ticket.id}",
            local_ticket_id=ticket.id,
            project_id=created["project_id"],
        ))
        db.commit()
        root_id, child_id, ticket_id = root.id, child.id, ticket.id

    response = client.delete(f"/api/tickets/tickets/{ticket_id}?delete_thread=true")

    assert response.status_code == 200
    assert response.json()["deleted_thread_id"] == root_id
    with get_session() as db:
        assert db.get(KanbanTicket, ticket_id) is None
        assert db.get(Chat, root_id) is None
        assert db.get(Chat, child_id) is None
        assert db.query(DevelopmentWorkItem).filter_by(chat_id=root_id).first() is None


def test_ticket_delete_can_preserve_and_detach_its_linked_thread(board_api):
    client, get_session, _tmp_path = board_api
    created = client.post("/api/tickets/boards", json={"name": "Preserved Thread Board"}).json()

    with get_session() as db:
        board = db.get(KanbanBoard, created["id"])
        root = Chat(title="Keep this thread", project_id=created["project_id"], params="{}")
        db.add(root)
        db.flush()
        ticket = KanbanTicket(lane_id=board.lanes[0].id, title="Disposable ticket", source_chat_id=root.id)
        db.add(ticket)
        db.flush()
        root.params = json.dumps({"development": {
            "ticket_id": ticket.id,
            "board_key": f"decisions:{created['id']}",
            "board_ticket_key": f"decisions:{ticket.id}",
            "board_ticket_title": ticket.title,
        }})
        db.add(DevelopmentWorkItem(
            chat_id=root.id,
            identity_key=f"ticket:local:{ticket.id}",
            board_provider="decisions",
            board_key=f"decisions:{created['id']}",
            ticket_key=f"decisions:{ticket.id}",
            local_ticket_id=ticket.id,
            project_id=created["project_id"],
        ))
        db.commit()
        root_id, ticket_id = root.id, ticket.id

    response = client.delete(f"/api/tickets/tickets/{ticket_id}")

    assert response.status_code == 200
    assert response.json()["deleted_thread_id"] is None
    with get_session() as db:
        assert db.get(KanbanTicket, ticket_id) is None
        preserved = db.get(Chat, root_id)
        assert preserved is not None
        development = json.loads(preserved.params)["development"]
        assert development["ticket_id"] is None
        assert "board_ticket_key" not in development
        item = db.query(DevelopmentWorkItem).filter_by(chat_id=root_id).one()
        assert item.identity_key == f"chat:{root_id}"
        assert item.local_ticket_id is None
        assert item.ticket_key is None


def test_local_ticket_thread_draft_copies_attachments_without_starting_work(board_api, monkeypatch):
    client, get_session, tmp_path = board_api
    monkeypatch.setenv("HOME", str(tmp_path))
    source = tmp_path / "ticket-image.png"
    source.write_bytes(b"image")
    created = client.post("/api/tickets/boards", json={"name": "Draft Board"}).json()

    with get_session() as db:
        board = db.get(KanbanBoard, created["id"])
        ticket = KanbanTicket(
            lane_id=board.lanes[0].id,
            title="Build the settings screen",
            description="Use the attached reference.",
        )
        db.add(ticket)
        db.flush()
        db.add(KanbanTicketFile(ticket_id=ticket.id, filename="reference.png", file_path=str(source)))
        db.commit()
        ticket_id = ticket.id

    response = client.post("/api/tickets/thread-draft", json={
        "provider": "decisions",
        "board_id": str(created["id"]),
        "ticket_id": str(ticket_id),
        "project_id": created["project_id"],
    })
    assert response.status_code == 200
    body = response.json()
    assert body["prompt"] == "Build the settings screen\n\nUse the attached reference."
    assert [item["name"] for item in body["attachments"]] == ["reference.png"]
    prepared = Path(body["attachments"][0]["path"])
    assert prepared.read_bytes() == b"image"
    assert str(prepared).startswith(str(tmp_path / ".decisions" / "workspaces" / "projects"))


def test_jira_ticket_thread_draft_downloads_files_and_writes_comments(tmp_path, monkeypatch):
    import requests

    class FakeResponse:
        def __init__(self, body=None, content=b"", headers=None, status_code=200):
            self._body = body
            self.content = content or (b"json" if body is not None else b"")
            self.headers = headers or {}
            self.status_code = status_code

        def json(self):
            return self._body

        def iter_content(self, chunk_size=1024 * 1024):
            del chunk_size
            yield self.content

    def fake_get(url, **kwargs):
        del kwargs
        if url.endswith("/rest/api/3/issue/DEV-99"):
            return FakeResponse({"fields": {
                "summary": "Build from Jira",
                "description": "<p>Use the supplied <strong>screenshot</strong>.</p>",
                "attachment": [{"id": "7", "filename": "screen.png", "mimeType": "image/png", "content": "https://jira.example/attachment/7"}],
                "comment": {},
            }})
        if url.endswith("/rest/api/3/issue/DEV-99/comment"):
            return FakeResponse({"comments": [{"author": {"displayName": "Paul"}, "created": "2026-08-27T10:00:00Z", "body": "Keep it compact."}], "total": 1})
        if url == "https://jira.example/attachment/7":
            return FakeResponse(content=b"png", headers={"content-type": "image/png", "content-length": "3"})
        raise AssertionError(url)

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(kanban, "_load_json_connected_accounts", lambda: [{
        "provider": "jira", "email": "paul@example.com", "api_token": "secret", "domain": "jira.example",
    }])
    monkeypatch.setattr(requests, "get", fake_get)

    body = kanban._prepare_external_ticket_thread_draft(kanban.TicketThreadDraftRequest(
        provider="jira", board_id="35", ticket_id="DEV-99", project_id=9,
    ))
    assert body["prompt"] == "Build from Jira\n\nUse the supplied screenshot."
    assert [item["name"] for item in body["attachments"]] == ["screen.png", "ticket-comments.txt"]
    assert Path(body["attachments"][0]["path"]).read_bytes() == b"png"
    assert "Keep it compact." in Path(body["attachments"][1]["path"]).read_text(encoding="utf-8")
