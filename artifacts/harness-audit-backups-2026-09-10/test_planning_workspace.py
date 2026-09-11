from __future__ import annotations

import contextlib
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from distr.core.db import Base
from distr.core.db.kanban import KanbanBoard, KanbanLane, KanbanTicket
from distr.core.db.projects import Project
from distr.core.planning import service as planning_workspace


ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture()
def plan_workspace_db(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'board-plan.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)

    @contextlib.contextmanager
    def get_session():
        db = factory()
        try:
            yield db
        finally:
            db.close()

    monkeypatch.setattr(planning_workspace, "get_session", get_session)
    project_folder = tmp_path / "project"
    project_folder.mkdir()
    with get_session() as db:
        project = Project(name="Compact Plan", folder_location=str(project_folder))
        db.add(project)
        db.commit()
        return {"project_id": project.id, "folder": project_folder}


def test_board_plan_language_flow_persists_versions_and_project_file(plan_workspace_db):
    workspace = planning_workspace.ensure_workspace(
        board_key="decisions:9",
        board_provider="decisions",
        board_name="Compact Plan",
        project_id=plan_workspace_db["project_id"],
    )

    created = planning_workspace.apply_instruction(
        workspace["id"],
        instruction="Create a PRD",
    )
    item = created["item"]
    assert created["action"] == "created"
    assert item["item_type"] == "prd"
    assert Path(item["file_path"]).is_file()
    assert Path(item["file_path"]).parent == plan_workspace_db["folder"] / "planning"

    updated = planning_workspace.apply_instruction(
        workspace["id"],
        item_id=item["id"],
        instruction="append ## Constraints\n\nKeep the UI compact.",
    )["item"]
    assert "Keep the UI compact." in updated["content"]
    assert updated["revision_count"] == 2
    assert len(planning_workspace.list_revisions(item["id"])) == 2
    detail = planning_workspace.get_workspace(workspace["id"])
    assert detail["item_count"] == 9
    assert detail["summary"]["total"] == 9


def test_plan_save_stops_when_the_managed_file_changed_outside_decisions(plan_workspace_db):
    workspace = planning_workspace.ensure_workspace(
        board_key="decisions:10",
        board_provider="decisions",
        board_name="Protected Plan",
        project_id=plan_workspace_db["project_id"],
    )
    item = planning_workspace.create_item(workspace_id=workspace["id"], item_type="brief")
    Path(item["file_path"]).write_text("Edited outside Decisions", encoding="utf-8")

    with pytest.raises(ValueError, match="changed outside Decisions"):
        planning_workspace.update_item(item["id"], content="Replacement")


def test_project_discovery_is_concise_repeatable_and_frack_alias_is_understood(plan_workspace_db):
    (plan_workspace_db["folder"] / "README.md").write_text("# Existing product\n\nCurrent behavior.", encoding="utf-8")
    (plan_workspace_db["folder"] / "app.py").write_text("print('ready')", encoding="utf-8")
    workspace = planning_workspace.ensure_workspace(
        board_key="decisions:11",
        board_provider="decisions",
        board_name="Existing project",
        project_id=plan_workspace_db["project_id"],
    )

    first = planning_workspace.apply_instruction(workspace["id"], instruction="Generate from project")["item"]
    second = planning_workspace.apply_instruction(workspace["id"], instruction="Scan project")["item"]
    frac = planning_workspace.apply_instruction(workspace["id"], instruction="Create a frack document")["item"]

    assert first["id"] == second["id"]
    assert "README.md" in second["content"]
    assert "Existing product" in second["content"]
    assert second["revision_count"] == 1
    assert frac["item_type"] == "frac"


def test_plan_home_only_opens_boards_with_a_real_project():
    plan_js = (ROOT / "distr/gui/web/static/development/planning/index.js").read_text(encoding="utf-8")
    studio_js = (ROOT / "distr/gui/web/static/development/app.js").read_text(encoding="utf-8")

    assert "const workingBoards = boards.filter((board) => Boolean(projectFor(board)))" in plan_js
    assert "const unlinkedBoards = boards.filter((board) => !projectFor(board))" in plan_js
    assert "data-plan-unlinked" in plan_js
    assert "Link a project to make planning available." in plan_js
    assert "if (!projectFor(board))" in plan_js
    assert "if (!board.project_id || !projectById(board.project_id))" in studio_js
    assert 'id="plan-language-mic"' in plan_js
    assert 'id="plan-voice-status"' in plan_js
    assert "Captured. Review, then apply." in plan_js


def test_stale_plan_revision_is_rejected_without_changing_content(plan_workspace_db):
    workspace = planning_workspace.ensure_workspace(board_key="decisions:20", board_provider="decisions", board_name="Revisions", project_id=plan_workspace_db["project_id"])
    item = planning_workspace.create_item(workspace_id=workspace["id"], item_type="brief")
    planning_workspace.update_item(item["id"], content="Newer text", expected_revision=1)
    with pytest.raises(ValueError, match="newer revision"):
        planning_workspace.update_item(item["id"], content="Stale text", expected_revision=1)
    detail = planning_workspace.get_workspace(workspace["id"])
    assert next(row for row in detail["items"] if row["id"] == item["id"])["content"] == "Newer text"
    assert Path(item["file_path"]).read_text() == "Newer text"


@pytest.mark.parametrize("instruction", ["append Foreign text", "replace with Foreign text", "rename to Foreign title"])
def test_all_item_instructions_enforce_workspace_ownership(plan_workspace_db, instruction):
    first = planning_workspace.ensure_workspace(board_key="decisions:21", board_provider="decisions", board_name="First", project_id=plan_workspace_db["project_id"])
    second = planning_workspace.ensure_workspace(board_key="decisions:22", board_provider="decisions", board_name="Second", project_id=plan_workspace_db["project_id"])
    item = planning_workspace.create_item(workspace_id=second["id"], item_type="brief")
    with pytest.raises(LookupError, match="workspace"):
        planning_workspace.apply_instruction(first["id"], item_id=item["id"], instruction=instruction)
    assert next(row for row in planning_workspace.get_workspace(second["id"])["items"] if row["id"] == item["id"]) == item


def test_empty_project_plan_scaffold_is_idempotent(plan_workspace_db):
    first = planning_workspace.ensure_workspace(board_key="decisions:30", board_provider="decisions", board_name="Scaffold", project_id=plan_workspace_db["project_id"])
    second = planning_workspace.ensure_workspace(board_key="decisions:30", board_provider="decisions", board_name="Scaffold", project_id=plan_workspace_db["project_id"])
    detail = planning_workspace.get_workspace(first["id"])
    assert first["id"] == second["id"]
    assert [row["item_type"] for row in detail["items"]] == ["brief", "prd", "frac", "architecture", "flows", "skills", "file_structure", "handover"]
    assert second["item_count"] == 8


def test_file_structure_scaffold_is_bounded_and_visual(plan_workspace_db):
    (plan_workspace_db["folder"] / "src").mkdir()
    (plan_workspace_db["folder"] / "src" / "main.py").write_text("print('ok')", encoding="utf-8")
    (plan_workspace_db["folder"] / ".env").write_text("SECRET=hidden", encoding="utf-8")
    (plan_workspace_db["folder"] / "node_modules").mkdir()
    workspace = planning_workspace.ensure_workspace(board_key="decisions:31", board_provider="decisions", board_name="Tree", project_id=plan_workspace_db["project_id"])
    tree = next(item for item in workspace["items"] if item["item_type"] == "file_structure")
    assert tree["visual_mode"] == "Bounded file tree"
    assert "src/main.py" in tree["content"]
    assert ".env" not in tree["content"]
    assert "node_modules" not in tree["content"]


def test_approved_plan_ticket_build_is_idempotent(plan_workspace_db):
    with planning_workspace.get_session() as db:
        board = KanbanBoard(name="Ticket target", source="database", default_project_id=plan_workspace_db["project_id"])
        db.add(board)
        db.flush()
        lane = KanbanLane(board_id=board.id, name="Backlog", position=0)
        db.add(lane)
        db.commit()
        board_id = board.id
    workspace = planning_workspace.ensure_workspace(
        board_key=f"decisions:{board_id}", board_provider="decisions", board_name="Ticket target", project_id=plan_workspace_db["project_id"]
    )
    item = next(row for row in workspace["items"] if row["item_type"] == "brief")
    planning_workspace.update_item(item["id"], status="approved", expected_revision=item["revision_count"])

    first = planning_workspace.build_approved_tickets(workspace["id"])
    second = planning_workspace.build_approved_tickets(workspace["id"])
    assert first["created"] == ["Outcome brief"]
    assert first["reused"] == []
    assert second["created"] == []
    assert second["reused"] == ["Outcome brief"]
    with planning_workspace.get_session() as db:
        assert db.query(KanbanTicket).filter(KanbanTicket.source_provider == "plan").count() == 1


def test_editing_approved_content_requires_approval_of_the_new_revision(plan_workspace_db):
    workspace = planning_workspace.ensure_workspace(board_key="decisions:23", board_provider="decisions", board_name="Approval", project_id=plan_workspace_db["project_id"])
    item = planning_workspace.create_item(workspace_id=workspace["id"], item_type="brief")
    approved = planning_workspace.update_item(item["id"], status="approved", expected_revision=1)
    revised = planning_workspace.update_item(item["id"], status="approved", content="Changed requirements", expected_revision=approved["revision_count"])
    assert revised["status"] == "draft"
    assert planning_workspace.list_revisions(item["id"])[1]["status"] == "approved"
