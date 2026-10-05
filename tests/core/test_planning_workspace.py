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
    assert detail["item_count"] == 1
    assert detail["summary"]["total"] == 1


def test_wireframe_plan_item_uses_wire_source_and_managed_file(plan_workspace_db):
    workspace = planning_workspace.ensure_workspace(
        board_key="decisions:12",
        board_provider="decisions",
        board_name="Wireframe source",
        project_id=plan_workspace_db["project_id"],
    )
    item = planning_workspace.apply_instruction(
        workspace["id"],
        instruction='Create a wireframe named "Sign in"',
    )["item"]

    assert item["content_format"] == "wire"
    assert item["file_path"].endswith(".wire")
    assert "screen \"New screen\"" in Path(item["file_path"]).read_text(encoding="utf-8")


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
    frontend = plan_workspace_db["folder"] / "frontend" / "src"
    frontend.mkdir(parents=True)
    (frontend / "routes.jsx").write_text(
        'export const routes = [{ url: "/", name: "home" }, { url: "/shipments", name: "shipments" }];\n',
        encoding="utf-8",
    )
    workspace = planning_workspace.ensure_workspace(
        board_key="decisions:11",
        board_provider="decisions",
        board_name="Existing project",
        project_id=plan_workspace_db["project_id"],
    )
    planning_workspace.ensure_plan_scaffold(int(workspace["id"]))

    first = planning_workspace.apply_instruction(workspace["id"], instruction="Generate from project")
    second = planning_workspace.apply_instruction(workspace["id"], instruction="Scan project")
    frac = planning_workspace.apply_instruction(workspace["id"], instruction="Create a frack document")["item"]

    assert first["action"] == "scanned"
    assert second["action"] == "scanned"
    assert first["summary"]["route_count"] == second["summary"]["route_count"] >= 2
    brief = next(item for item in second["workspace"]["items"] if item["item_type"] == "brief")
    assert "Existing product" in brief["content"]
    assert "/shipments" in next(item for item in second["workspace"]["items"] if item["item_type"] == "prd")["content"]
    assert frac["item_type"] == "frac"


def test_plan_home_lists_sidebar_boards_that_have_a_project():
    plan_js = (ROOT / "distr/gui/web/static/development/planning/index.js").read_text(encoding="utf-8")
    studio_js = (ROOT / "distr/gui/web/static/development/app.js").read_text(encoding="utf-8")
    plan_css = (ROOT / "distr/gui/web/static/development/planning/styles.css").read_text(encoding="utf-8")

    assert '<div class="plan-home">' in plan_js
    assert "Every named board shows its plan" not in plan_js
    assert "Boards with no project are marked" not in plan_js
    assert "Archived boards stay hidden" not in plan_js
    assert "plan-unlinked-badge" not in plan_js
    assert ">Unlinked</span>" not in plan_js
    assert "No plan yet" not in plan_js
    assert "section${count === 1 ? '' : 's'}" not in plan_js
    assert "const actionLabel = count ? 'Edit' : 'Build plan';" in plan_js
    assert "plan-board-action" in plan_js
    assert "if (!projectFor(board)) return false;" in plan_js
    assert "data-plan-board-open" in plan_js
    assert "Link a project to make planning available." not in plan_js
    assert "if (!projectFor(board))" in plan_js
    assert "if (!board.project_id || !projectById(board.project_id))" in studio_js
    assert "Archived or unnamed boards are not shown as plans." not in studio_js
    assert 'id="plan-language-mic"' not in plan_js
    assert 'id="plan-voice-status"' not in plan_js
    assert ".plan-home { width: min(1040px, calc(100% - 48px)); margin: 0 auto; padding: 16px 0 32px; height: 100%; overflow: auto; }" in plan_css
    assert ".plan-workspace { padding: 0; overflow: hidden; background: var(--studio-bg); }" in plan_css


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


def test_opening_empty_project_plan_scaffolds_canonical_sections(plan_workspace_db):
    first = planning_workspace.open_project_plan(board_key="decisions:30", board_provider="decisions", board_name="Scaffold", project_id=plan_workspace_db["project_id"])
    second = planning_workspace.open_project_plan(board_key="decisions:30", board_provider="decisions", board_name="Scaffold", project_id=plan_workspace_db["project_id"])
    detail = planning_workspace.get_workspace(first["id"])
    assert first["id"] == second["id"]
    types = [row["item_type"] for row in detail["items"]]
    assert types == list(planning_workspace.SCAFFOLD)
    assert second["item_count"] == len(planning_workspace.SCAFFOLD)
    assert all(row.get("is_starter") for row in detail["items"])
    planning_root = plan_workspace_db["folder"] / "planning"
    assert planning_root.exists()
    assert any(planning_root.iterdir())
    # ensure_workspace alone still does not scaffold (used by low-level helpers).
    empty = planning_workspace.ensure_workspace(board_key="decisions:31-empty", board_provider="decisions", board_name="Empty", project_id=plan_workspace_db["project_id"])
    assert planning_workspace.get_workspace(empty["id"])["items"] == []


def test_open_project_plan_materializes_real_sections_from_any_codebase(plan_workspace_db):
    folder = plan_workspace_db["folder"]
    frontend = folder / "frontend" / "src"
    frontend.mkdir(parents=True)
    (frontend / "routes.jsx").write_text(
        """
export const routes = [
  { url: "/", name: "home" },
  { url: "/auctions", name: "auctions" },
  { url: "/lots/:id", name: "lot_detail" },
  { url: "/login", name: "login" },
];
""",
        encoding="utf-8",
    )
    models = folder / "backend" / "apps" / "auction" / "models.py"
    models.parent.mkdir(parents=True)
    models.write_text(
        """
from django.db import models

class Auction(models.Model):
    title = models.CharField(max_length=120)

class Lot(models.Model):
    auction = models.ForeignKey(Auction, on_delete=models.CASCADE)
""",
        encoding="utf-8",
    )
    opened = planning_workspace.open_project_plan(
        board_key="decisions:scan-any",
        board_provider="decisions",
        board_name="AuctionNow",
        project_id=plan_workspace_db["project_id"],
    )
    by_type = {item["item_type"]: item for item in opened["items"]}
    assert not by_type["brief"].get("is_starter")
    assert "/auctions" in by_type["prd"]["content"]
    assert "Auction" in by_type["architecture"]["content"]
    assert 'route="/auctions"' in by_type["flows"]["content"]
    assert "FR-001" in by_type["frac"]["content"]
    # Soft re-open must not thrash curated content when already filled.
    by_type["prd"] = planning_workspace.update_item(
        int(by_type["prd"]["id"]),
        content="# Curated PRD\n\nKeep this.\n",
        expected_revision=int(by_type["prd"]["revision_count"]),
        source="language",
        instruction="curate",
        workspace_id=int(opened["id"]),
    )
    soft = planning_workspace.materialize_project_scan(int(opened["id"]), force=False)
    assert "prd" in soft["skipped"]
    force = planning_workspace.materialize_project_scan(int(opened["id"]), force=True, instruction="Scan project")
    assert "prd" in force["updated"]
    refreshed = planning_workspace.get_workspace(int(opened["id"]))
    prd = next(item for item in refreshed["items"] if item["item_type"] == "prd")
    assert "/auctions" in prd["content"]
    assert "Keep this." not in prd["content"]


def test_file_structure_scaffold_is_bounded_and_visual(plan_workspace_db):
    (plan_workspace_db["folder"] / "src").mkdir()
    (plan_workspace_db["folder"] / "src" / "main.py").write_text("print('ok')", encoding="utf-8")
    (plan_workspace_db["folder"] / ".env").write_text("SECRET=hidden", encoding="utf-8")
    (plan_workspace_db["folder"] / "node_modules").mkdir()
    workspace = planning_workspace.ensure_workspace(board_key="decisions:31", board_provider="decisions", board_name="Tree", project_id=plan_workspace_db["project_id"])
    tree = planning_workspace.create_item(workspace_id=workspace["id"], item_type="file_structure")
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
    item = planning_workspace.create_item(workspace_id=workspace["id"], item_type="brief")
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
