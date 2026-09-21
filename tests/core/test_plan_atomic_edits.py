from pathlib import Path

import pytest

from tests.core.test_planning_workspace import plan_workspace_db
from distr.core.planning import service


def _plan(project):
    return service.ensure_workspace(board_key="decisions:54", board_provider="decisions",
                                    board_name="Atomic plan", project_id=project["project_id"])


def _edit(kind, content, title=None, **extra):
    return dict(item_type=kind, title=title or kind, content=content,
                content_format="mermaid" if kind == "erd" else "markdown", **extra)


def test_linked_edits_commit_and_project_together(plan_workspace_db):
    plan = _plan(plan_workspace_db)
    result = service.apply_agent_edits(plan["id"], edits=[_edit("prd", "Customer records"),
        _edit("frac", "FR-001: Save customer"), _edit("erd", "erDiagram\n CUSTOMER {\n string name\n }")],
        expected_revisions={}, instruction="Add customers")
    assert len(result) == 3
    for item in result:
        assert Path(item["file_path"]).read_text() == item["content"]
        assert item["revision_count"] == 1
        assert service.list_revisions(item["id"])[0]["instruction"] == "Add customers"


def test_image_manifest_is_scoped_and_foreign_image_rolls_back_linked_edits(plan_workspace_db):
    from distr.core.db.projects import Project, ProjectFile
    plan = _plan(plan_workspace_db)
    with service.get_session() as db:
        foreign = Project(name='Other project')
        db.add(foreign)
        db.flush()
        own = ProjectFile(project_id=plan_workspace_db['project_id'], filename='logo.png', file_path='/unused/logo.png')
        other = ProjectFile(project_id=foreign.id, filename='private.png', file_path='/unused/private.png')
        pdf = ProjectFile(project_id=plan_workspace_db['project_id'], filename='brief.pdf', file_path='/unused/brief.pdf')
        db.add_all([own, other, pdf])
        db.commit()
        own_id, other_id, pdf_id = own.id, other.id, pdf.id
    for invalid_id in (other_id, pdf_id):
        with pytest.raises(ValueError, match=f'asset={invalid_id}'):
            service.apply_agent_edits(plan['id'], edits=[_edit('prd', 'Must not persist'),
                {'item_type':'wireframe','title':'Brand','content_format':'wire',
                 'content':f'screen Brand\n  image Logo asset={invalid_id}'}], expected_revisions={}, instruction='Use logo')
        assert service.get_workspace(plan['id'])['items'] == []
    result = service.apply_agent_edits(plan['id'], edits=[
        {'item_type':'wireframe','title':'Brand','content_format':'wire',
         'content':f'screen Brand\n  image Logo asset={own_id}'}], expected_revisions={}, instruction='Use project logo')
    assert len(result) == 1


def test_invalid_late_edit_rolls_back_everything(plan_workspace_db):
    plan = _plan(plan_workspace_db)
    with pytest.raises(ValueError, match="Unsupported"):
        service.apply_agent_edits(plan["id"], edits=[_edit("prd", "Valid"), _edit("executable", "bad")],
                                  expected_revisions={}, instruction="Invalid")
    assert service.get_workspace(plan["id"])["items"] == []
    assert not (plan_workspace_db["folder"] / "planning").exists()


def test_external_file_edit_preserves_entire_linked_plan(plan_workspace_db):
    plan = _plan(plan_workspace_db)
    first, second = service.apply_agent_edits(plan["id"], edits=[_edit("prd", "Original"), _edit("frac", "Original")],
                                             expected_revisions={}, instruction="Initial")
    Path(second["file_path"]).write_text("User changed this")
    with pytest.raises(ValueError, match="outside Decisions"):
        service.apply_agent_edits(plan["id"], edits=[_edit("prd", "Updated", id=first["id"]),
            _edit("frac", "Updated", id=second["id"])],
            expected_revisions={str(first["id"]): 1, str(second["id"]): 1}, instruction="Update both")
    assert [item["content"] for item in service.get_workspace(plan["id"])["items"]] == ["Original", "Original"]
    assert Path(first["file_path"]).read_text() == "Original"
    assert Path(second["file_path"]).read_text() == "User changed this"


def test_changed_unselected_artifact_invalidates_context(plan_workspace_db):
    plan = _plan(plan_workspace_db)
    first = service.create_item(workspace_id=plan["id"], item_type="prd", content="Original")
    service.create_item(workspace_id=plan["id"], item_type="frac", content="New context")
    with pytest.raises(ValueError, match="plan changed"):
        service.apply_agent_edits(plan["id"], edits=[_edit("prd", "Wrong", id=first["id"])],
                                  expected_revisions={str(first["id"]): 1}, instruction="Stale")
    assert Path(first["file_path"]).read_text() == "Original"


def test_existing_starter_is_preserved_and_identified(plan_workspace_db):
    plan = _plan(plan_workspace_db)
    item = service.create_item(workspace_id=plan["id"], item_type="brief", source="scaffold")
    assert service.get_workspace(plan["id"])["items"][0]["is_starter"]
    service.update_item(item["id"], content="Actual project purpose", expected_revision=1)
    assert not service.get_workspace(plan["id"])["items"][0].get("is_starter")


def test_invalid_wireframe_cannot_commit_linked_requirements(plan_workspace_db):
    plan = _plan(plan_workspace_db)
    with pytest.raises(ValueError, match="Invalid wireframe"):
        service.apply_agent_edits(plan["id"], edits=[_edit("prd", "Must not persist"),
            {"item_type": "wireframe", "title": "Broken", "content_format": "wire",
             "content": 'screen "Customers"\n  invented-widget "Not real"'}],
            expected_revisions={}, instruction="Create customer screen")
    assert service.get_workspace(plan["id"])["items"] == []
    assert not (plan_workspace_db["folder"] / "planning").exists()


def test_valid_wireframe_commits_after_real_renderer_validation(plan_workspace_db):
    plan = _plan(plan_workspace_db)
    source = 'screen "Customers"\n  form "New customer"\n    input "Name"\n    button "Save"'
    result = service.apply_agent_edits(plan["id"], edits=[
        {"item_type": "wireframe", "title": "Customers", "content_format": "wire", "content": source}],
        expected_revisions={}, instruction="Create customer screen")
    assert result[0]["content"] == source
    assert Path(result[0]["file_path"]).read_text() == source


def test_inherited_wire_format_is_validated_before_linked_edits_commit(plan_workspace_db):
    plan = _plan(plan_workspace_db)
    source = 'screen "Customers"\n  input "Name"'
    item = service.apply_agent_edits(plan["id"], edits=[
        {"item_type": "wireframe", "title": "Customers", "content_format": "wire", "content": source}],
        expected_revisions={}, instruction="Create screen")[0]
    with pytest.raises(ValueError, match="Invalid wireframe"):
        service.apply_agent_edits(plan["id"], edits=[_edit("prd", "Must not persist"),
            {"id": item["id"], "content": 'screen "Customers"\n  invented-widget "Broken"'}],
            expected_revisions={item["id"]: 1}, instruction="Update screen")
    current = service.get_workspace(plan["id"])["items"]
    assert len(current) == 1
    assert current[0]["revision_count"] == 1
    assert current[0]["content"] == source
    assert Path(item["file_path"]).read_text() == source


def _linked_customer_edits():
    return [
        {"item_type": "wireframe", "title": "Customers", "content_format": "wire",
         "content": 'screen "Customers"\n  input "Name" bind=Customer.name\n  button "Save" requirement=FR-001'},
        _edit("erd", "erDiagram\n Customer {\n string name\n }"),
        _edit("frac", "## FR-001\nSave the customer."),
    ]


def test_explicit_references_resolve_across_same_turn(plan_workspace_db):
    plan = _plan(plan_workspace_db)
    items = service.apply_agent_edits(plan["id"], edits=_linked_customer_edits(),
                                     expected_revisions={}, instruction="Create linked customer plan")
    assert len(items) == 3
    assert all(Path(item["file_path"]).exists() for item in items)


@pytest.mark.parametrize("missing", ["erd", "frac"])
def test_missing_reference_rolls_back_all_artifacts_and_files(plan_workspace_db, missing):
    plan = _plan(plan_workspace_db)
    with pytest.raises(ValueError, match="Unresolved plan references"):
        service.apply_agent_edits(plan["id"], edits=[e for e in _linked_customer_edits() if e["item_type"] != missing],
                                  expected_revisions={}, instruction="Incomplete linked change")
    assert service.get_workspace(plan["id"])["items"] == []
    assert not (plan_workspace_db["folder"] / "planning").exists()


@pytest.mark.parametrize("kind,content", [("erd", "erDiagram\n Customer {\n string surname\n }"),
                                          ("frac", "The old behavior mentioned FR-001, but is no longer defined.")])
def test_removing_target_cannot_break_unchanged_wireframe(plan_workspace_db, kind, content):
    plan = _plan(plan_workspace_db)
    items = service.apply_agent_edits(plan["id"], edits=_linked_customer_edits(),
                                     expected_revisions={}, instruction="Create plan")
    target = next(item for item in items if item["item_type"] == kind)
    with pytest.raises(ValueError, match="Unresolved plan references"):
        service.apply_agent_edits(plan["id"], edits=[{"id": target["id"], "content": content}],
                                  expected_revisions={item["id"]: 1 for item in items}, instruction="Remove target")
    current = service.get_workspace(plan["id"])["items"]
    assert all(item["revision_count"] == 1 for item in current)
    assert Path(target["file_path"]).read_text() == target["content"]


def test_linked_rename_updates_binding_and_schema_together(plan_workspace_db):
    plan = _plan(plan_workspace_db)
    items = service.apply_agent_edits(plan["id"], edits=_linked_customer_edits(),
                                     expected_revisions={}, instruction="Create plan")
    edits = [{"id": item["id"], "content": item["content"].replace("name", "surname")}
             for item in items if item["item_type"] in {"wireframe", "erd"}]
    updated = service.apply_agent_edits(plan["id"], edits=edits,
                                       expected_revisions={item["id"]: 1 for item in items}, instruction="Rename field")
    assert all(item["revision_count"] == 2 for item in updated)
