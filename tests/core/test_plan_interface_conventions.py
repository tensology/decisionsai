"""N6 phase 3: "#|" page descriptions and the decisions-flow sidecar survive AI edits and project scans,
and build tickets get the deterministic "Interface context (generated)" block. Temp DBs only, no LLM."""
import json

import pytest

from distr.core.db.kanban import KanbanTicket
from distr.core.db.workflow import PlanItem
from distr.core.planning import build, service
from distr.core.planning import interface_conventions as ic
import distr.core.planning.conversation  # noqa: F401  (prompt module loads)
from tests.core.test_plan_build import store, task  # noqa: F401
from tests.core.test_planning_workspace import plan_workspace_db  # noqa: F401


def side(nodes, prefix="# decisions-flow:"):
    return f"{prefix} {json.dumps({'nodes': nodes, 'v': 1}, sort_keys=True, separators=(',', ':'))}\n"


PAGE = ('screen "Customers" id=customers route=/customers\n  #| Lists every **customer**.\n  #| Sorted by name.\n'
        '  heading "Customers"\nscreen "Saved" id=saved route=/saved\n  text "Done"\n')
ANNOTATED = PAGE + side({"customers": {"shape": "diamond", "note": "Admins only"}, "saved": {"note": "After save"}})


# ---- pure helpers --------------------------------------------------------------------------------------
def test_carry_restores_dropped_description_sidecar_and_ids():
    dropped = 'screen "Customers" route=/customers\n  heading "All customers"\nscreen "Saved" id=saved route=/saved\n  text "Done"\n'
    out, warnings = ic.carry_annotations(ANNOTATED, dropped, "wire")
    assert out.startswith('screen "Customers" id=customers route=/customers\n  #| Lists every **customer**.\n  #| Sorted by name.\n  heading "All customers"\n')
    assert out.rstrip("\n").split("\n")[-1] == side({"customers": {"shape": "diamond", "note": "Admins only"}, "saved": {"note": "After save"}}).strip()
    assert warnings == []
    assert ic.carry_annotations(ANNOTATED, ANNOTATED, "wire") == (ANNOTATED, [])         # unchanged input, unchanged output


def test_carry_keeps_explicit_changes_and_flags_vanished_keys():
    changed = PAGE.replace("Lists every **customer**.", "Shows VIP customers.").replace('screen "Saved" id=saved route=/saved\n  text "Done"\n', "") + side({"customers": {"note": "new"}})
    out, warnings = ic.carry_annotations(ANNOTATED, changed, "wire")
    assert "Shows VIP customers." in out and "Lists every" not in out                    # model's explicit edit kept
    assert ic.sidecar_nodes(out, "wire") == {"customers": {"note": "new"}}
    gone = ic.carry_annotations(ANNOTATED, 'screen "Customers" id=customers route=/customers\n  heading "C"\n', "wire")
    assert "saved" in ic.sidecar_nodes(gone[0], "wire")                                  # vanished key kept
    assert 'Flow metadata for "saved" has no matching node; it was kept.' in gone[1]


def test_mermaid_sidecar_reattached():
    flow = "flowchart LR\n  A{Ok?} --> B\n"
    old = flow + side({"A": {"note": "check"}}, "%% decisions-flow:")
    out, warnings = ic.carry_annotations(old, "flowchart LR\n  A{Ok?} --> C\n", "mermaid")
    assert out.endswith(side({"A": {"note": "check"}}, "%% decisions-flow:")) and warnings == []


# ---- AI chat edits -------------------------------------------------------------------------------------
@pytest.fixture
def flows_item(plan_workspace_db):
    ws = service.ensure_workspace(board_key="decisions:81", board_provider="decisions", board_name="Ifaces", project_id=plan_workspace_db["project_id"])
    item = service.create_item(workspace_id=ws["id"], item_type="flows", title="Pages", content=ANNOTATED)
    return ws, item


def _revisions(ws):
    return {str(i["id"]): i["revision_count"] for i in service.get_workspace(ws["id"])["items"]}


def test_chat_edit_that_keeps_both_is_stored_verbatim(flows_item):
    ws, item = flows_item
    edited = ANNOTATED.replace('heading "Customers"', 'heading "Our customers"')
    result = service.apply_agent_edits(ws["id"], edits=[{"id": item["id"], "content": edited}], expected_revisions=_revisions(ws), instruction="rename")
    assert result[0]["content"] == edited and "validation_warnings" not in result[0]


def test_chat_edit_that_drops_annotations_is_repaired_with_warning(flows_item):
    ws, item = flows_item
    dropped = 'screen "Customers" route=/customers\n  heading "Our customers"\nscreen "Saved" route=/saved\n  text "Done"\n'
    result = service.apply_agent_edits(ws["id"], edits=[{"id": item["id"], "content": dropped}], expected_revisions=_revisions(ws), instruction="rename")
    content = result[0]["content"]
    assert '  #| Lists every **customer**.\n  #| Sorted by name.\n  heading "Our customers"' in content
    assert 'screen "Customers" id=customers route=/customers' in content and 'screen "Saved" id=saved route=/saved' in content
    assert ic.sidecar_nodes(content, "wire") == ic.sidecar_nodes(ANNOTATED, "wire")
    assert result[0]["validation_warnings"][0] == "Kept the page descriptions / node metadata that the edit left out."
    assert service.get_workspace(ws["id"])["items"][0]["content"] == content


def test_chat_edit_cannot_invent_asset_ids(flows_item):
    ws, item = flows_item
    bad = PAGE + side({"customers": {"image": "999"}})
    with pytest.raises(ValueError, match="Image 999 is not an attachment"):
        service.apply_agent_edits(ws["id"], edits=[{"id": item["id"], "content": bad}], expected_revisions=_revisions(ws), instruction="x")
    assert service.get_workspace(ws["id"])["items"][0]["content"] == ANNOTATED        # nothing applied


def test_prompt_tells_the_model_both_conventions():
    import inspect
    from distr.core.planning import conversation
    src = inspect.getsource(conversation)
    for phrase in ("'#|' directly under a screen line", "'# decisions-flow: {json}'", "'%% decisions-flow: {json}'",
                   "copy both back unchanged", "Return only strict JSON, no fences"):
        assert phrase in src


# ---- project scan --------------------------------------------------------------------------------------
def _scan_project(folder):
    front = folder / "frontend" / "src"
    front.mkdir(parents=True, exist_ok=True)
    (front / "routes.jsx").write_text('export const routes = [\n  { url: "/customers", name: "customers" },\n  { url: "/saved", name: "saved" },\n];\n', encoding="utf-8")


def test_scan_treats_annotated_items_as_hand_made_and_force_carries_them(plan_workspace_db):
    _scan_project(plan_workspace_db["folder"])
    opened = service.open_project_plan(board_key="decisions:scan-n6", board_provider="decisions", board_name="Scan", project_id=plan_workspace_db["project_id"])
    flows = next(i for i in opened["items"] if i["item_type"] == "flows")
    generated = flows["content"]
    screens = ic.screens(generated)
    assert screens and all(s["id"] is None for s in screens)
    # The user describes the customers page and attaches metadata (adds id=customers, like the JS writer).
    customers = next(s for s in screens if s["route"] == "/customers")
    lines = generated.split("\n")
    lines[customers["index"]] = lines[customers["index"]].replace('screen "', 'screen "', 1)
    head = lines[customers["index"]]
    label_end = head.index('"', head.index('"') + 1) + 1
    lines[customers["index"]] = head[:label_end] + " id=customers" + head[label_end:]
    lines.insert(customers["index"] + 1, "  #| Customer list for admins.")
    annotated = "\n".join(lines).rstrip("\n") + "\n" + side({"customers": {"shape": "circle"}, "ghost": {"note": "old page"}})
    flows = service.update_item(flows["id"], content=annotated, expected_revision=flows["revision_count"], workspace_id=opened["id"])
    assert ic.has_annotations(flows["content"], "wire")
    assert not service._item_is_replaceable(flows, force=False)
    # N4 overlay (soft and force): the annotated screen is still "generated" (id= and #| do not count as
    # edits), so it is regenerated in place with its description, id and the sidecar kept.
    for force in (False, True):
        result = service.materialize_project_scan(opened["id"], force=force, instruction="Scan project")
        content = next(i for i in service.get_workspace(opened["id"])["items"] if i["item_type"] == "flows")["content"]
        assert "id=customers" in content and "  #| Customer list for admins." in content
        assert ic.sidecar_nodes(content, "wire") == {"customers": {"shape": "circle"}, "ghost": {"note": "old page"}}   # vanished key kept
        assert content.rstrip("\n").split("\n")[-1].startswith("# decisions-flow:")
        if "flows" in result["updated"]:
            assert any(w.startswith("flows: ") and '"ghost"' in w for w in result["validation_warnings"])                 # ...and flagged


# ---- ticket building -----------------------------------------------------------------------------------
def _set_item(store, content, item_type="flows", fmt="wire"):
    with store() as db:
        item = db.get(PlanItem, 1)
        item.item_type, item.content_format, item.content = item_type, fmt, content
        db.commit()


EXPECTED_BLOCK = ('---\nInterface context (generated):\n'
                  'Page "Customers" (/customers): Lists every customer. Sorted by name.\n'
                  'Nodes: customers (decision) – Admins only; saved (page) – After save')


def test_ticket_block_exact_text_and_rebuild_identical(store):
    _set_item(store, ANNOTATED)
    first = build.build_tasks(1, tasks=[task()], expected_revisions={"1": 1})
    with store() as db:
        ticket = db.query(KanbanTicket).one()
        description, notes = ticket.description, ticket.context_notes
    assert description == "Implement the wireframe\n\n" + EXPECTED_BLOCK
    assert "{" not in description and "decisions-flow" not in description and "**" not in description
    again = build.build_tasks(1, tasks=[task()], expected_revisions={"1": 1})
    assert again["reused"] == 1 and again["created"] == 0 and again["updated"] == 0
    echoed = build.build_tasks(1, tasks=[task(description=description)], expected_revisions={"1": 1})   # model echoes old text
    assert echoed["reused"] == 1
    with store() as db:
        ticket = db.query(KanbanTicket).one()
        assert (ticket.description, ticket.context_notes) == (description, notes)
    assert first["created"] == 1


def test_ticket_user_edit_still_detected(store):
    _set_item(store, ANNOTATED)
    build.build_tasks(1, tasks=[task()], expected_revisions={"1": 1})
    with store() as db:
        ticket = db.query(KanbanTicket).one()
        ticket.description += "\nUser note"
        db.commit()
    with pytest.raises(build.PlanBuildConflict):
        build.build_tasks(1, tasks=[task()], expected_revisions={"1": 1})


def test_ticket_block_caps_and_only_for_interface_sources(store):
    long_desc = " ".join(["word"] * 200)
    nodes = {f"p{i}": {"note": "n" * 300} for i in range(40)}
    content = "".join(f'screen "P{i}" id=p{i} route=/p{i}\n  #| {long_desc}\n  text "x"\n' for i in range(40)) + side(nodes)
    block = ic.interface_context([{"item_type": "flows", "content_format": "wire", "content": content}])
    assert len(block) <= ic.BLOCK_CHARS and block.endswith("\n…")
    first_page = block.split("\n")[1]
    assert first_page.endswith("…") and len(first_page.split(": ", 1)[1]) <= ic.DESC_CHARS + 1 and not first_page.split(": ", 1)[1][:-1].endswith(" ")
    one = ic.interface_context([{"item_type": "flows", "content_format": "wire", "content": 'screen "A" id=a\n  text "x"\n' + side({"a": {"note": "z" * 300}})}])
    assert one == "Interface context (generated):\nNodes: a (page) – " + "z" * 120 + "…"
    assert ic.interface_context([{"item_type": "flows", "content_format": "wire", "content": content}]) == block   # deterministic
    _set_item(store, "# PRD\n\nPlain doc", item_type="prd", fmt="markdown")
    build.build_tasks(1, tasks=[task()], expected_revisions={"1": 1})
    with store() as db:
        assert db.query(KanbanTicket).one().description == "Implement the wireframe"
    long = ic.with_interface_context("d" * 20000, block)
    assert len(long) <= 20000 and long.endswith(block)


def test_markdown_lite_is_plain_and_orphans_skipped():
    content = ('screen "Home" id=home route=/\n  #| ## Intro\n  #| See [docs](http://x) and `code` *now*.\n  text "x"\n'
               + side({"home": {"note": "**Bold** note"}, "gone": {"note": "orphan"}}))
    block = ic.interface_context([{"item_type": "wireframe", "content_format": "wire", "content": content}])
    assert block == 'Interface context (generated):\nPage "Home" (/): Intro See docs and code now.\nNodes: home (page) – Bold note'
