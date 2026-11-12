"""Built-in workflows, selection fallback, generated planning provenance and the Requirements Pack (2026-11-11)."""
import contextlib
import json
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import distr.core.db as core_db
from distr.core.db import Base, Chat
from distr.core.db.kanban import KanbanBoard, KanbanLane, KanbanTicket
from distr.core.db.projects import Project
from distr.core.db.workflow import AutoWorkflow, AutoWorkflowStep, PlanItem, PlanItemRevision
from distr.core.workflow import builtin

WIRE = (
    'screen "Home" route="/" id=home device=desktop\n'
    '#| Landing page that explains the offer.\n'
    '  stack\n'
    '    heading "Welcome"\n'
    '    button "Get started" variant=primary\n'
    'screen "Sign up" route="/signup" id=signup device=desktop\n'
    '#| Collect the customer email.\n'
    '  stack\n'
    '    input "Email" bind=customer.email\n'
    '    button "Create account"\n'
)


@pytest.fixture
def env(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'builtin.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)

    @contextlib.contextmanager
    def get_session():
        db = factory()
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    import distr.core.planning.conversation as conversation
    import distr.core.workflow.loop_presets as loop_presets
    import distr.core.planning.build as plan_build
    import distr.core.planning.service as planning_workspace
    import distr.core.workflow.service as workflow_service
    import distr.core.workspace_memory.lifecycle as lifecycle
    import distr.core.workspace_memory.provision as provision
    import distr.gui.web.workflow_events as workflow_events
    for module in (core_db, workflow_service, loop_presets, planning_workspace, conversation, plan_build):
        monkeypatch.setattr(module, "get_session", get_session)
    monkeypatch.setattr(provision, "bootstrap_workflow", lambda *a, **k: "")
    monkeypatch.setattr(lifecycle, "hook_remove_workspace", lambda *a, **k: True)
    monkeypatch.setattr(workflow_events, "increment_workflow_updated", lambda: None)
    monkeypatch.setattr(builtin, "_SEEDED", False)
    code = tmp_path / "shop"
    code.mkdir()
    (code / "manage.py").write_text("print('django')\n")
    (code / "README.md").write_text("# Shop\n\nSells carrier bags online.\n")
    empty = tmp_path / "fresh"
    empty.mkdir()
    with get_session() as db:
        shop = Project(name="Shop", folder_location=str(code))
        fresh = Project(name="Fresh", folder_location=str(empty))
        db.add_all([shop, fresh])
        db.flush()
        board = KanbanBoard(name="Shop board", default_project_id=shop.id)
        db.add(board)
        db.flush()
        lane = KanbanLane(board_id=board.id, name="Backlog", position=0)
        db.add(lane)
        db.flush()
        db.add(KanbanTicket(lane_id=lane.id, title="Add gift wrap option", description="Customers asked for gift wrap.",
                            linked_project_id=shop.id))
        db.add(Chat(project_id=shop.id, title="Checkout copy", input="Please make the checkout button say Pay now"))
        ids = {"shop": shop.id, "fresh": fresh.id, "board": board.id, "lane": lane.id}
    return SimpleNamespace(session=get_session, code=code, empty=empty, ids=ids, tmp=tmp_path)


def _seed():
    return builtin.ensure_builtin_workflows(force=True)


# ---------------------------------------------------------------- workflows

def test_builtin_bundles_have_expected_shape():
    from distr.core.workflow.loop_preset_loader import normalize_bundle_steps

    from distr.core.workflow.loop_preset_loader import load_bundle_by_slug

    counts = {slug: normalize_bundle_steps(builtin.load_builtin_bundle(slug)) for slug in builtin.BUILTIN_SLUGS}
    assert {slug: len(steps) for slug, steps in counts.items()} == {
        builtin.NEW_WORK: 7, builtin.EXISTING_PROJECT: 7, builtin.DEVELOPMENT_SLIM: 7,
        builtin.DEPLOY_DEV: 5, builtin.DEPLOY_PROD: 5}
    # The Development bundle stays available as a "+ Add workflow" preset, but is not seeded.
    dev_preset = normalize_bundle_steps(load_bundle_by_slug(builtin.DEVELOPMENT))
    assert len(dev_preset) == 11 and builtin.DEVELOPMENT in builtin.RETIRED_BUILTIN_SLUGS
    new_work = counts[builtin.NEW_WORK]
    assert new_work[3]["require_approval"] and new_work[3]["wait_for_continue"]  # design approval gate
    assert counts[builtin.EXISTING_PROJECT][5]["require_approval"]
    for slug in (builtin.NEW_WORK, builtin.EXISTING_PROJECT):
        assert all(step["config"].get("read_only") for step in counts[slug])  # noqa: E501  # never edits the repo
        assert all("plan_" in (step["config"].get("other_tool") or "") for step in counts[slug])


def test_seeding_is_idempotent_and_rows_pass_the_selection_audit(env):
    from distr.core.workflow.selection import audit_workflow_contract, classify_work_request
    from distr.core.workflow.service import get_workflow, list_workflows

    first = _seed()
    second = builtin.ensure_builtin_workflows(force=True)
    assert first == second and set(first) == set(builtin.BUILTIN_SLUGS)
    assert builtin.DEVELOPMENT not in first
    with env.session() as db:
        assert db.query(AutoWorkflow).count() == len(builtin.BUILTIN_SLUGS)
        assert db.query(KanbanBoard).filter(KanbanBoard.default_workflow_id.isnot(None)).count() == 0  # boards untouched
    profile = classify_work_request("Design the pages for a new website with a signup form")
    for slug, wf_id in first.items():  # all five built-ins, including both deploys
        audit = audit_workflow_contract(get_workflow(wf_id), request_profile=profile)
        assert audit["viable"], (slug, audit["missing"])
        assert get_workflow(wf_id)["builtin_slug"] == slug
    assert {row["id"] for row in list_workflows(limit=20)} == set(first.values())  # visible rows


def test_retired_development_builtin_is_never_reseeded(env):
    """Force-seeding must not recreate the retired Development built-in, even if no row exists."""
    ids = _seed()
    assert builtin.DEVELOPMENT not in ids
    with env.session() as db:
        assert db.query(AutoWorkflow).filter(
            AutoWorkflow.workflow_input.like("%development-ticket-to-implementation%")
        ).count() == 0
        assert db.query(AutoWorkflow).count() == len(builtin.BUILTIN_SLUGS)
    again = builtin.ensure_builtin_workflows(force=True)
    assert again == ids and builtin.DEVELOPMENT not in again
    slug, reason = builtin.choose_builtin_slug("Fix the checkout bug in the payment API", project_folder=str(env.code))
    assert slug == builtin.DEVELOPMENT_SLIM and "never deploys" in reason  # slim Development, not the legacy 11-step


def test_selection_picks_the_builtin_for_each_scenario(env):
    from distr.core.workflow.selection import select_workflow_for_request

    ids = _seed()
    pick = lambda text, folder: (select_workflow_for_request(text, project_folder=folder)["selected"] or {}).get("workflow_id")
    assert pick("Fix the checkout bug in the payment API", str(env.code)) == ids[builtin.DEVELOPMENT_SLIM]
    assert pick("Start a new website project and design the pages", None) == ids[builtin.NEW_WORK]
    assert pick("Build the frontend landing page", str(env.empty)) == ids[builtin.NEW_WORK]  # folder without code
    assert pick("Reverse-engineer the existing project and build the ERD", str(env.code)) == ids[builtin.EXISTING_PROJECT]
    ordinary = select_workflow_for_request("Fix the checkout bug in the payment API", project_folder=str(env.code))
    assert ordinary["selected"]["builtin_slug"] == builtin.DEVELOPMENT_SLIM and ordinary["create_required"] is False
    by_slug = {row["builtin_slug"]: row for row in ordinary["candidates"]}
    assert builtin.DEVELOPMENT not in by_slug
    assert not any(by_slug[slug]["viable"] for slug in (builtin.NEW_WORK, builtin.EXISTING_PROJECT, *builtin.DEPLOY_SLUGS))


def test_orchestrator_assignment_falls_back_to_builtins_conservatively(env):
    from distr.core.orchestrator_proactive import _workflow_assignment

    ids = _seed()
    board = SimpleNamespace(send_to_cli=False, default_workflow_id=None, default_project_id=env.ids["shop"])
    ticket = lambda **kw: SimpleNamespace(**{"linked_workflow_id": None, "send_to_cli": False, "complexity": "medium",
                                             "title": "Tweak button colour", "description": "", "linked_project_id": None, **kw})
    assert _workflow_assignment(ticket(), board) is None  # ordinary work keeps running direct
    high = _workflow_assignment(ticket(complexity="high"), board)  # slim Development (never the legacy 11-step)
    assert high[0] == ids[builtin.DEVELOPMENT_SLIM] and high[1] == "builtin"
    assert _workflow_assignment(ticket(title="Reverse-engineer the existing project into the plan"), board) == (
        ids[builtin.EXISTING_PROJECT], "builtin", "The request asks to map an existing project into the plan.")
    assert _workflow_assignment(ticket(complexity="high", send_to_cli=True), board) is None
    assert _workflow_assignment(ticket(linked_workflow_id=7), board) == (7, "ticket", "Assigned to this ticket.")
    assert _workflow_assignment(ticket(), SimpleNamespace(send_to_cli=False, default_workflow_id=9)) == (
        9, "board", "Assigned as the board default.")


def test_builtin_recommendation_explains_its_selection_and_preserves_pending_approval(env, monkeypatch):
    from distr.core import orchestrator_proactive as proactive
    from distr.core.db.orchestrator import OrchestratorEvent

    monkeypatch.setattr(proactive, "get_session", env.session)
    monkeypatch.setattr("distr.core.orchestrator.get_session", env.session)
    monkeypatch.setattr(proactive, "_external_context", lambda: {})
    monkeypatch.setattr(proactive, "_resolve_route", lambda _: {"backend": "pi", "model": "fixture-model"})
    ids = _seed()
    with env.session() as db:
        ticket = db.query(KanbanTicket).one()
        ticket.title = "Reverse-engineer the existing project into the plan"
        ticket_id = ticket.id
    reason = "The request asks to map an existing project into the plan."
    candidate = proactive.run_proactive_check(board_id=env.ids["board"])["candidates"][0]
    draft = candidate["workflow_draft"]
    assert draft["workflow_id"] == ids[builtin.EXISTING_PROJECT]
    assert draft["assigned_by"] == "builtin" and draft["reason"] == reason
    assert f"I recommend {draft['name']}" in candidate["approval_question"]
    assert candidate["recommended_action"] == "review_workflow_draft"
    candidate_id = candidate["candidate_id"]
    direct = proactive.dispatch_proactive_candidate(candidate_id)
    assert not direct["success"] and "Open Workflow review" in direct["error"]
    with env.session() as db:
        event = db.get(OrchestratorEvent, candidate_id)
        saved = json.loads(event.payload)
        saved["candidate"]["workflow_draft"]["reason"] = "Assigned as the board default."
        event.payload = json.dumps(saved)
    review = proactive.review_workflow_draft(board_id=env.ids["board"], ticket_id=ticket_id)
    assert review["status"] == "awaiting_review" and review["candidate_id"] == candidate_id
    assert review["draft"] == draft
    rescanned = proactive.run_proactive_check(board_id=env.ids["board"])["candidates"][0]
    assert rescanned["candidate_id"] == candidate_id and rescanned["workflow_draft"] == draft
    with env.session() as db:
        db.get(AutoWorkflow, draft["workflow_id"]).steps[-1].name = "Unreviewed planning change"
    assert proactive.review_workflow_draft(board_id=env.ids["board"], ticket_id=ticket_id) == {"status": "stale"}


def test_presets_route_and_builtin_reset_instead_of_delete(env):
    from distr.gui.web.routes.development.workflows import register_routes

    ids = _seed()
    app = FastAPI()
    router = APIRouter(prefix="/api")
    register_routes(router, None)
    app.include_router(router)
    with TestClient(app) as client:
        presets = {p["slug"]: p for p in client.get("/api/workflows/development-presets").json()["presets"]}
        assert builtin.DEVELOPMENT not in presets  # legacy 11-step preset replaced by the split
        assert presets[builtin.DEVELOPMENT_SLIM]["step_count"] == 7
        assert presets[builtin.DEPLOY_DEV]["step_count"] == 5 and presets[builtin.DEPLOY_PROD]["step_count"] == 5
        assert presets[builtin.NEW_WORK]["step_count"] == 7 and presets[builtin.EXISTING_PROJECT]["step_count"] == 7
        listed = {row["id"]: row for row in client.get("/api/workflows").json()}
        assert set(ids.values()) <= set(listed) and builtin.DEVELOPMENT not in ids
        wf_id = ids[builtin.NEW_WORK]
        with env.session() as db:
            db.query(AutoWorkflowStep).filter_by(workflow_id=wf_id, position=6).delete()
        response = client.delete(f"/api/workflows/{wf_id}")
        assert response.status_code == 200 and response.json()["reset"] is True
        with env.session() as db:
            assert db.get(AutoWorkflow, wf_id) is not None
            assert db.query(AutoWorkflowStep).filter_by(workflow_id=wf_id).count() == 7
        assert {row["id"] for row in client.get("/api/workflows").json()} >= set(ids.values())


# ---------------------------------------------------------------- planning

def _workspace(env, project="shop"):
    from distr.core.planning import service

    return service.ensure_workspace(board_key=f"decisions:{env.ids['board']}", board_provider="decisions",
                                    board_name="Shop board", project_id=env.ids[project])["id"]


def test_requirements_ledger_separates_derived_new_and_conflicts():
    from distr.core.planning.requirements_pack import ledger_from_items

    ledger = ledger_from_items([
        {"id": 1, "item_type": "frac", "last_source": "discovery", "content": "## FR-001 Checkout\n## FR-002 Search"},
        {"id": 2, "item_type": "prd", "last_source": "design", "content": "- FR-002: Search by colour\n- FR-003: Gift wrap"},
        {"id": 3, "item_type": "requirements_pack", "content": "<!-- decisions:new-instructions -->\n- NEW-001: Dark mode\n<!-- /decisions:new-instructions -->"},
    ])
    origins = {row["key"]: row["origin"] for row in ledger["requirements"]}
    assert origins == {"FR-001": "derived", "FR-002": "derived", "FR-003": "new", "NEW-001": "new"}
    assert ledger["conflict_keys"] == ["FR-002"] and ledger["counts"] == {"derived": 2, "new": 2, "conflicts": 1}


def test_workspace_items_carry_provenance_and_retired_rows_do_not_block_edits(env):
    from distr.core.planning import service

    ws = _workspace(env)
    service.ensure_plan_scaffold(ws)
    with env.session() as db:
        legacy = PlanItem(workspace_id=ws, item_type="handover", title="Handover", content="# Handover")
        db.add(legacy)
        db.flush()
        db.add(PlanItemRevision(item_id=legacy.id, revision=1, title="Handover", content="# Handover", source="scaffold"))
    detail = service.get_workspace(ws)
    brief = next(i for i in detail["items"] if i["item_type"] == "brief")
    assert brief["origin"] == "scaffold" and brief["is_starter"] and brief["last_generated_at"]
    revisions = {str(i["id"]): i["revision_count"] for i in detail["items"]}
    # Regression: a stored retired row must not make every agent edit look stale.
    result = service.apply_agent_edits(ws, edits=[{"id": brief["id"], "content": "# Brief\n\nSell bags."}],
                                       expected_revisions=revisions, instruction="t", source="workflow_new")
    assert result[0]["revision_count"] == 2
    assert next(i for i in service.get_workspace(ws)["items"] if i["id"] == brief["id"])["origin"] == "new"
    with pytest.raises(ValueError):
        service.apply_agent_edits(ws, edits=[{"item_type": "handover", "title": "H", "content": "x", "content_format": "markdown"}],
                                  expected_revisions={str(i["id"]): i["revision_count"] for i in service.get_workspace(ws)["items"]},
                                  instruction="t", source="ai")


def test_derive_from_design_builds_stable_frac_and_skips_manual_edits(env):
    from distr.core.planning import service
    from distr.core.planning.derive import derive_from_design

    from distr.core.planning.derive import build_design_erd, design_screens

    ws = _workspace(env, "fresh")
    erd = "erDiagram\n    customer {\n        string id PK\n        string email\n    }\n"
    service.apply_agent_edits(ws, edits=[
        {"item_type": "architecture", "title": "Data model", "content": erd, "content_format": "mermaid"},
        {"item_type": "wireframe", "title": "Site", "content": WIRE, "content_format": "wire"}],
        expected_revisions={}, instruction="design", source="design")
    first = derive_from_design(ws)
    assert first["requirement_keys"] == ["FR-001", "FR-002"] and set(first["updated"]) == {"frac", "prd"}
    items = {i["item_type"]: i for i in service.get_workspace(ws)["items"]}
    assert "## FR-002 Sign up (`/signup`)" in items["frac"]["content"] and items["frac"]["origin"] == "new"
    assert items["architecture"]["content"] == erd  # existing data model is never replaced
    assert "customer {" in build_design_erd(design_screens(list(items.values())))
    service.update_item(items["prd"]["id"], content="# PRD\n\nPaul's own words", source="user", workspace_id=ws)
    second = derive_from_design(ws)
    assert second["requirement_keys"] == ["FR-001", "FR-002"] and second["skipped_manual"] == ["prd"]


def test_requirements_pack_is_db_only_and_keeps_new_instructions(env):
    from distr.core.planning import service
    from distr.core.planning.requirements_pack import generate_requirements_pack

    ws = _workspace(env)
    result = generate_requirements_pack(ws, new_instructions="- NEW-001: Offer gift wrap at checkout")
    pack = result["item"]
    assert result["written_to_repo"] is False and result["proposed_path"] == "planning/REQUIREMENTS.md"
    assert not pack.get("file_path") and not (env.code / "planning" / "REQUIREMENTS.md").exists()
    assert not any(p.name.endswith("requirements-pack.md") for p in env.code.rglob("*"))
    content = pack["content"]
    for heading in ("## 1. What this project is (derived)", "## 2. What has been asked so far (derived)",
                    "## 4. Requirements ledger", "## 5. New instructions", "## 6. Conflicts"):
        assert heading in content
    assert "Sells carrier bags" in content and "Add gift wrap option" in content and "Pay now" in content
    assert result["ledger"]["counts"]["new"] == 1
    again = generate_requirements_pack(ws)
    assert "NEW-001: Offer gift wrap at checkout" in again["item"]["content"]  # kept on regeneration


def test_planning_snapshot_excludes_retired_and_reports_origin_and_requirements(env):
    from distr.core.planning import service
    from distr.core.planning.context import planning_snapshot

    ws = _workspace(env)
    with env.session() as db:
        for kind, source, content in (("handover", "user", "# Handover secret"), ("frac", "discovery", "## FR-001 Checkout")):
            item = PlanItem(workspace_id=ws, item_type=kind, title=kind, content=content)
            db.add(item)
            db.flush()
            db.add(PlanItemRevision(item_id=item.id, revision=1, title=kind, content=content, source=source))
    snapshot = planning_snapshot(env.ids["shop"])
    workspace = snapshot["workspaces"][0]
    assert {i["type"] for i in workspace["items"]} == {"frac"}
    assert workspace["items"][0]["origin"] == "derived"
    assert workspace["requirements"]["derived"] == 1
    assert "Handover secret" not in json.dumps(snapshot)
    assert service.get_workspace(ws) is not None


def test_plan_tools_resolve_project_and_protect_manual_edits(env):
    from distr.core.planning import service
    from distr.core.planning import plan_tools
    from distr.core.workflow import dev_agent_tools

    ws = _workspace(env)
    item = service.create_item(workspace_id=ws, item_type="brief", content="# Brief", source="user")
    snap = plan_tools.plan_snapshot(project_folder=str(env.code / "apps"))  # nested folder resolves to the project
    assert snap["ok"] and snap["workspace_id"] == ws
    refused = plan_tools.plan_write_artifacts([{"id": item["id"], "content": "# Overwrite"}], project_folder=str(env.code))
    assert not refused["ok"] and "edited by Paul" in refused["summary"]
    saved = plan_tools.plan_write_artifacts([{"item_type": "decision", "title": "Use Stripe", "content": "# Decision\n\nStripe."}],
                                            origin="derived", project_folder=str(env.code))
    assert saved["ok"] and saved["saved"][0]["item_type"] == "decision", saved
    assert not plan_tools.plan_snapshot(project_folder=str(env.tmp / "elsewhere"))["ok"]
    assert {"plan_snapshot", "plan_write_artifacts", "plan_requirements_pack"} <= set(dev_agent_tools.TOOL_SCHEMAS)
    assert all(spec["parameters"]["required"] for name, spec in dev_agent_tools.TOOL_SCHEMAS.items() if name.startswith("plan_"))


def test_planning_ui_static_contract():
    root = Path(__file__).resolve().parents[2] / "distr" / "gui" / "web" / "static" / "development"
    documents = (root / "planning" / "documents.js").read_text()
    order = next(line for line in documents.splitlines() if line.startswith("export const DOCUMENT_ORDER"))
    assert "'requirements_pack'" in order and "project_overview" not in documents and "discovery_proposal" not in documents
    assert "data-plan-provenance" in (root / "planning" / "conversation.js").read_text()
    workflows = (root / "workflows" / "index.js").read_text()
    assert "data-workflow-reset" in workflows and "builtinSlugOf" in workflows

