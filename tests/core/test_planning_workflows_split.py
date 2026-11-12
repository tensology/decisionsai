"""Planning + workflows split (2026-11-12).

Proves: New Work / Existing Project / slim Development / Deploy to Development /
Deploy to Production are registered and selectable; an implement ticket no longer
dead-ends at "No existing workflow safely covers this ticket"; the Plan's
Interfaces, Data and Documents reach selection and the run (not a Brief-first
~1,500-char slice); slim Development never deploys; each deploy has its own
approval and nothing auto-chains; and selection discerns implement vs deploy.
"""
import contextlib
import json
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import distr.core.db as core_db
from distr.core.db import Base
from distr.core.db.kanban import KanbanBoard, KanbanLane, KanbanTicket
from distr.core.db.projects import Project
from distr.core.db.workflow import AutoWorkflow, AutoWorkflowStep, PlanItem, PlanWorkspace
from distr.core.workflow import builtin

WIRE = ('screen "Checkout" route="/checkout" id=checkout device=desktop\n'
        '#| Customer reviews the bag and pays.\n'
        '  stack\n    heading "Your bag"\n    button "Pay now" variant=primary\n')
ERD = "erDiagram\n    CUSTOMER ||--o{ ORDER : places\n    ORDER ||--o{ GIFT_WRAP : adds\n"


@pytest.fixture
def env(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'split.db'}")
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

    import distr.core.workflow.loop_presets as loop_presets
    import distr.core.workflow.service as workflow_service
    import distr.core.workflow.spawn_workflow as spawn_workflow
    import distr.core.workspace_memory.lifecycle as lifecycle
    import distr.core.workspace_memory.provision as provision
    import distr.gui.web.workflow_events as workflow_events
    for module in (core_db, workflow_service, loop_presets, spawn_workflow):
        monkeypatch.setattr(module, "get_session", get_session)
    monkeypatch.setattr(provision, "bootstrap_workflow", lambda *a, **k: "")
    monkeypatch.setattr(lifecycle, "hook_remove_workspace", lambda *a, **k: True)
    monkeypatch.setattr(workflow_events, "increment_workflow_updated", lambda: None)
    monkeypatch.setattr(builtin, "_SEEDED", False)
    code = tmp_path / "shop"
    code.mkdir()
    (code / "manage.py").write_text("print('django')\n")
    fresh = tmp_path / "fresh"
    fresh.mkdir()
    old = datetime(2026, 1, 1)
    with get_session() as db:
        shop = Project(name="Shop", folder_location=str(code))
        empty = Project(name="Fresh", folder_location=str(fresh))
        db.add_all([shop, empty])
        db.flush()
        board = KanbanBoard(name="Shop board", default_project_id=shop.id)
        db.add(board)
        db.flush()
        lane = KanbanLane(board_id=board.id, name="Backlog", position=0)
        db.add(lane)
        db.flush()
        ticket = KanbanTicket(lane_id=lane.id, title="Add gift wrap option",
                              description="Customers asked for gift wrap at checkout.", linked_project_id=shop.id)
        db.add(ticket)
        ws = PlanWorkspace(project_id=shop.id, board_key="decisions:1", board_name="Shop board")
        db.add(ws)
        db.flush()
        # Interfaces and Data are OLDER than four long Documents: the old Brief-first,
        # top-4, ~1,500-char context dropped them entirely.
        db.add_all([
            PlanItem(workspace_id=ws.id, item_type="wireframe", title="Checkout page", content=WIRE, content_format="wire", modified_at=old),
            PlanItem(workspace_id=ws.id, item_type="erd", title="Orders data model", content=ERD, content_format="mermaid", modified_at=old),
            PlanItem(workspace_id=ws.id, item_type="brief", title="Outcome brief", content="BRIEF: sell carrier bags online. " * 300),
            PlanItem(workspace_id=ws.id, item_type="frac", title="Functional requirements", content="FR-001 gift wrap. " * 300),
            PlanItem(workspace_id=ws.id, item_type="prd", title="Product requirements", content="PRD text. " * 300),
            PlanItem(workspace_id=ws.id, item_type="document", title="Supplier notes", content="Supplier notes. " * 300),
        ])
        ids = {"shop": shop.id, "fresh": empty.id, "board": board.id, "lane": lane.id, "ticket": ticket.id}
    return SimpleNamespace(session=get_session, code=code, fresh=fresh, ids=ids)


def _seed():
    return builtin.ensure_builtin_workflows(force=True)


def _bundle_steps(slug):
    return builtin.load_builtin_bundle(slug)["steps"]


# --------------------------------------------------------------- 1. registered + selectable

def test_all_five_builtins_are_registered_visible_and_pass_the_audit(env):
    from distr.core.workflow.selection import audit_workflow_contract, classify_work_request
    from distr.core.workflow.service import get_workflow, list_workflows

    ids = _seed()
    assert set(ids) == {builtin.NEW_WORK, builtin.EXISTING_PROJECT, builtin.DEVELOPMENT_SLIM,
                        builtin.DEPLOY_DEV, builtin.DEPLOY_PROD}
    assert builtin.DEVELOPMENT not in ids  # legacy 11-step stays retired
    assert {row["id"] for row in list_workflows(limit=50)} == set(ids.values())
    for slug, wf_id in ids.items():
        workflow = get_workflow(wf_id)
        audit = audit_workflow_contract(workflow, request_profile=classify_work_request("Fix the checkout API bug"))
        assert audit["viable"], (slug, audit["missing"])
        marker = builtin._input_dict(workflow["workflow_input"])
        assert marker["builtin_slug"] == slug and marker["auto_chain"] is False and marker["chains_to"] == []
        assert marker["requires_own_approval"] is (slug in builtin.DEPLOY_SLUGS)
    assert _seed() == ids  # idempotent


def test_selection_chooses_the_right_structure_for_each_ticket(env):
    from distr.core.workflow.selection import select_workflow_for_request

    ids = _seed()
    shop = env.ids["shop"]

    def pick(text, folder=str(env.code), project_id=shop):
        return select_workflow_for_request(text, project_folder=folder, project_id=project_id)

    cases = {
        "Fix the checkout bug in the payment API": builtin.DEVELOPMENT_SLIM,
        "Add a gift wrap option to checkout": builtin.DEVELOPMENT_SLIM,
        "Start a new website project and design the pages": builtin.NEW_WORK,
        "Reverse-engineer the existing project and build the ERD": builtin.EXISTING_PROJECT,
        "Deploy the approved release to production": builtin.DEPLOY_PROD,
        "Deploy the verified commit to the development server": builtin.DEPLOY_DEV,
    }
    for text, slug in cases.items():
        result = pick(text)
        assert result["selected"] and result["selected"]["workflow_id"] == ids[slug], (text, result["reason"])
        assert "No existing workflow safely covers" not in result["reason"]


def test_implement_ticket_no_longer_dead_ends_at_no_existing_workflow(env):
    from distr.core.workflow.spawn_workflow import spawn_workflow_for_ticket

    ids = _seed()
    result = spawn_workflow_for_ticket(env.ids["ticket"], start_run=False)
    assert result["success"], result
    assert result["workflow_id"] == ids[builtin.DEVELOPMENT_SLIM]
    assert "No existing workflow safely covers" not in json.dumps(result)


def test_none_fits_is_explained_instead_of_the_generic_dead_end(env):
    from distr.core.workflow.selection import select_workflow_for_request

    _seed()
    result = select_workflow_for_request("Send a weekly Gmail digest to the team", project_folder=None)
    assert result["selected"] is None and result["create_required"] is True
    assert "None of the built-in workflows fits" in result["reason"]
    assert "No existing workflow safely covers" not in result["reason"]


# --------------------------------------------------------------- 2. Plan sections in context

def test_plan_sections_render_interfaces_data_and_documents_with_fair_budget(env):
    from distr.core.planning.sections import PLAN_SECTIONS_PROMPT_CHARS, plan_sections, plan_sections_prompt_text

    text = plan_sections_prompt_text(plan_sections(env.ids["shop"]))
    assert len(text) <= PLAN_SECTIONS_PROMPT_CHARS
    assert "## Interfaces" in text and 'screen "Checkout"' in text and "Pay now" in text
    assert "## Data" in text and "GIFT_WRAP" in text
    assert "## Documents" in text and "BRIEF: sell carrier bags" in text
    # Brief is long but cannot crowd out the other sections; every section keeps a real share.
    tight = plan_sections_prompt_text(plan_sections(env.ids["shop"]), max_chars=1500)
    assert len(tight) <= 1500 and 'screen "Checkout"' in tight and "GIFT_WRAP" in tight and "BRIEF" in tight
    assert plan_sections_prompt_text(plan_sections(env.ids["fresh"])) == ""  # no Plan, nothing attached


def test_old_brief_first_1500_char_slice_dropped_interfaces_and_data(env):
    """The legacy context (planning_prompt_text at 1,500) is a regression baseline for this fix."""
    from distr.core.planning.context import planning_prompt_text, planning_snapshot

    snapshot = planning_snapshot(env.ids["shop"])
    types = [item["type"] for item in snapshot["workspaces"][0]["items"]]
    # Section-balanced inventory: Interfaces and Data now make the cut beside Brief/FRAC/PRD.
    assert "wireframe" in types and "erd" in types and types[0] == "brief"
    legacy_slice = planning_prompt_text(snapshot, 1500)
    assert len(legacy_slice) <= 1500
    # The bounded inventory alone never carries the wire or ERD content ...
    assert 'screen "Checkout"' not in legacy_slice and "GIFT_WRAP" not in legacy_slice


def test_developer_prompt_carries_plan_sections_beyond_the_old_1500_cap(env):
    from distr.core.developer_context import DeveloperContextAssembler, DeveloperProjectContext

    project = DeveloperProjectContext(id=env.ids["shop"], name="Shop", folder_location=str(env.code))
    assembler = DeveloperContextAssembler.__new__(DeveloperContextAssembler)
    text = assembler._fetch_plan_sections_text(project, {})
    assert 'screen "Checkout"' in text and "GIFT_WRAP" in text
    assert assembler._fetch_plan_sections_text(project, {"project_id": env.ids["shop"] + 99}) == ""  # no cross-project borrow
    from distr.core.developer_context import DeveloperRuntimeContext, DeveloperWorkContext
    from distr.core.planning.context import planning_snapshot

    context = DeveloperWorkContext(runtime=DeveloperRuntimeContext(cwd=str(env.code)), active_project=project,
                                   planning_snapshot=planning_snapshot(env.ids["shop"]), plan_sections_text=text)
    prompt = context.to_prompt_text(max_chars=2200)
    assert "Persisted planning reference" in prompt
    assert "## Interfaces" in prompt and 'screen "Checkout"' in prompt and "## Data" in prompt and "GIFT_WRAP" in prompt


def test_run_context_and_selection_use_plan_sections(env, monkeypatch):
    import distr.core.workflow.work_dispatch as work_dispatch
    from distr.core.workflow.selection import select_workflow_for_request
    from distr.core.workflow.spawn_workflow import spawn_workflow_for_ticket

    ids = _seed()
    calls = []
    monkeypatch.setattr(work_dispatch, "dispatch_work_item", lambda **kw: calls.append(kw) or {"run_id": 1, "chat_id": 2})
    result = spawn_workflow_for_ticket(env.ids["ticket"], start_run=True)
    assert result["success"] and len(calls) == 1
    context = calls[0]["context"]
    assert "## Interfaces" in context and 'screen "Checkout"' in context
    assert "## Data" in context and "GIFT_WRAP" in context and "## Documents" in context
    plan = calls[0]["run_metadata"]["plan_sections"]
    assert plan["interfaces"] == 1 and plan["data"] == 1 and plan["documents"] == 4 and plan["missing"] == []
    selection = select_workflow_for_request("Add a gift wrap option to checkout", project_folder=str(env.code),
                                            project_id=env.ids["shop"])
    assert selection["discernment"]["signals"]["plan"]["interfaces"] == 1
    assert "Plan read: Interfaces (1), Data (1), Documents (4)" in selection["reason"]
    assert selection["selected"]["workflow_id"] == ids[builtin.DEVELOPMENT_SLIM]


# --------------------------------------------------------------- 3. the split

def test_slim_development_has_no_deploy_steps_and_ends_at_verification():
    steps = _bundle_steps(builtin.DEVELOPMENT_SLIM)
    assert len(steps) == 7
    roles = [step["config"].get("step_role") for step in steps]
    assert "deployment" not in roles and "release" not in roles
    assert not any(step.get("require_approval") for step in steps)
    assert not any("environment" in step["config"] for step in steps)
    assert not any(name in step["name"].lower() for step in steps for name in ("deploy", "production promotion"))
    assert roles[-1] == "reporting" and steps[-1].get("validation_pass_action") == "end_loop"
    assert all("Never deploy" in (step.get("guardrail") or "") for step in steps)
    legacy = _bundle_steps(builtin.DEVELOPMENT)  # the 11-step definition stays on disk for existing rows/runs
    assert len(legacy) == 11


@pytest.mark.parametrize("slug,env_name,other", [
    (builtin.DEPLOY_DEV, "development", "production"),
    (builtin.DEPLOY_PROD, "production", "development"),
])
def test_each_deploy_workflow_has_its_own_approval_before_deploying(slug, env_name, other):
    steps = _bundle_steps(slug)
    approvals = [i for i, step in enumerate(steps) if step.get("require_approval")]
    deploys = [i for i, step in enumerate(steps) if step["config"].get("step_role") == "deployment"]
    assert len(approvals) == 1 and len(deploys) == 1 and approvals[0] < deploys[0]
    assert steps[approvals[0]].get("wait_for_continue") is True
    assert steps[deploys[0]]["config"]["environment"] == env_name
    assert all(step["config"].get("environment") in (None, env_name) for step in steps)  # never touches the other env
    assert "its own explicit approval" in builtin.load_builtin_bundle(slug)["description"]


def test_development_never_auto_starts_a_deploy_and_deploys_keep_their_gate(env, monkeypatch):
    import distr.core.workflow.work_dispatch as work_dispatch
    from distr.core.workflow.spawn_workflow import _start_ticket_run, spawn_workflow_for_ticket

    ids = _seed()
    calls = []
    monkeypatch.setattr(work_dispatch, "dispatch_work_item", lambda **kw: calls.append(kw) or {"run_id": 1})
    spawn_workflow_for_ticket(env.ids["ticket"], start_run=True, skip_human_checkpoints=True)
    assert [call["workflow_id"] for call in calls] == [ids[builtin.DEVELOPMENT_SLIM]]  # exactly one run, no deploy
    calls.clear()
    _start_ticket_run(ids[builtin.DEPLOY_PROD], ticket_id=env.ids["ticket"], board_id=env.ids["board"],
                      skip_human_checkpoints=True, run_metadata={"skip_human_checkpoints": True}, dispatch_async=False)
    meta = calls[0]["run_metadata"]
    assert "skip_human_checkpoints" not in meta and meta["deploy_requires_own_approval"] is True
    with env.session() as db:  # the DB rows carry the gates too
        for slug in builtin.DEPLOY_SLUGS:
            gates = db.query(AutoWorkflowStep).filter_by(workflow_id=ids[slug], require_approval=True).count()
            assert gates == 1, slug


def test_in_flight_legacy_11_step_rows_are_left_intact_and_not_selected(env):
    from distr.core.workflow.loop_presets import apply_loop_bundle
    from distr.core.workflow.selection import select_workflow_for_request
    from distr.core.workflow.service import create_workflow

    legacy_id = create_workflow(name="Development", description="legacy")
    assert apply_loop_bundle(legacy_id, builtin.load_builtin_bundle(builtin.DEVELOPMENT), mode="replace")["success"]
    builtin._mark(legacy_id, builtin.DEVELOPMENT)
    ids = _seed()
    with env.session() as db:
        assert db.query(AutoWorkflowStep).filter_by(workflow_id=legacy_id).count() == 11  # untouched
        assert db.get(AutoWorkflow, legacy_id) is not None
    result = select_workflow_for_request("Fix the checkout bug in the payment API", project_folder=str(env.code))
    assert result["selected"]["workflow_id"] == ids[builtin.DEVELOPMENT_SLIM] != legacy_id


# --------------------------------------------------------------- 4. discernment

def test_discernment_separates_implement_from_deploy():
    d = lambda text, **kw: builtin.discern_builtin(text, has_code=True, **kw)
    mixed = d("Fix the login bug and deploy to production")
    assert mixed["slug"] == builtin.DEVELOPMENT_SLIM  # implement first; deploy is NOT auto-picked
    assert "Deploy to Production separately" in mixed["reason"] and "never started automatically" in mixed["reason"]
    assert d("Fix the checkout bug, do not deploy")["slug"] == builtin.DEVELOPMENT_SLIM
    assert d("Implement the export and verify it")["slug"] == builtin.DEVELOPMENT_SLIM
    assert d("Deploy the approved release to production")["slug"] == builtin.DEPLOY_PROD
    assert d("Promote the verified commit to production")["slug"] == builtin.DEPLOY_PROD
    assert d("Deploy to staging")["slug"] == builtin.DEPLOY_DEV
    assert d("Deploy it")["slug"] == builtin.DEPLOY_DEV  # unqualified deploy goes to development first
    assert d("Write the quarterly newsletter copy")["slug"] is None


def test_discernment_reads_the_plan_for_projects_without_code():
    design = {"interfaces": 2, "data": 0, "documents": 1, "missing": ["Data"], "has_content": True}
    assert builtin.discern_builtin("Build the frontend landing page", has_code=False, plan=design)["slug"] == builtin.DEVELOPMENT_SLIM
    assert builtin.discern_builtin("Build the frontend landing page", has_code=False, plan=None)["slug"] == builtin.NEW_WORK
    empty = {"interfaces": 0, "data": 0, "documents": 1, "missing": ["Interfaces", "Data"], "has_content": True}
    hint = builtin.discern_builtin("Fix the checkout API", has_code=True, plan=empty)
    assert hint["slug"] == builtin.DEVELOPMENT_SLIM and "Consider Existing Project" in hint["reason"]


def test_custom_workflow_with_deploy_steps_is_not_picked_for_implement_work():
    from distr.core.workflow.selection import select_workflow_for_request

    def wf(workflow_id, name, roles):
        steps = []
        for position, role in enumerate(roles):
            steps.append({
                "position": position, "name": f"{role} {position}",
                "instruction": "Perform this stage within scope, capture evidence, and stop with a blocker rather than guessing.",
                "validation_type": "llm_judgment", "validation_prompt": "Pass only with objective evidence for the stage.",
                "config": {"step_role": role, "skills": ["verification-loop"], "tools": ["cli", "shell"],
                           "guardrail": "Stay in scope.", "failure_checklist": ["Evidence missing"],
                           "required_context": ["ticket"], "expected_outputs": ["result"], "model_policy": {"mode": "auto"}},
            })
        return {"id": workflow_id, "name": name, "description": f"{name} workflow", "steps": steps,
                "run_settings": {"memory_enabled": True, "load_project_memory": True, "capture_memory_deltas": True}}

    everything = wf(1, "Development", ["planning", "implementation", "review", "implementation", "deployment", "reporting"])
    slim = wf(2, "Backend Development", ["planning", "implementation", "review", "implementation", "reporting"])
    result = select_workflow_for_request("Add a backend API migration", candidates=[everything, slim])
    assert result["selected"]["workflow_id"] == 2
    excluded = next(row for row in result["candidates"] if row["workflow_id"] == 1)
    assert not excluded["viable"] and "must not deploy" in excluded["excluded_reason"]


def test_orchestrator_assignment_discerns_deploy_from_implement(env):
    from distr.core.orchestrator_proactive import _workflow_assignment

    ids = _seed()
    board = SimpleNamespace(send_to_cli=False, default_workflow_id=None, default_project_id=env.ids["shop"])
    ticket = lambda **kw: SimpleNamespace(**{"linked_workflow_id": None, "send_to_cli": False, "complexity": "medium",
                                             "title": "Tweak button colour", "description": "", "linked_project_id": None, **kw})
    deploy = _workflow_assignment(ticket(title="Deploy the approved release to production"), board)
    assert deploy[0] == ids[builtin.DEPLOY_PROD] and deploy[1] == "builtin"
    assert _workflow_assignment(ticket(title="Fix the login bug and deploy to production"), board) is None  # direct, not a deploy
    high = _workflow_assignment(ticket(title="Fix the login bug and deploy to production", complexity="high"), board)
    assert high[0] == ids[builtin.DEVELOPMENT_SLIM]
