"""Plan chat tools (N1 item 7): typed edit ops inside the existing strict-JSON contract.

The model may tag each edit with ``"tool"``. Every op still goes through
``service.apply_agent_edits`` (workspace revision check, allowlist, validate_wireframe, the
``#|`` / decisions-flow carry, validate_flow_sidecar, validate_plan_references). Nothing here writes
directly. Undo is ``restore_revision``: the old content becomes a NEW revision (source "restore") via
``service.update_item``; undoing a create archives the item (never a row delete).
"""
from __future__ import annotations

from typing import Any

INTERFACE_TYPES = {"wireframe", "flows"}
ERD_TYPES = {"erd"}
DIAGRAM_TYPES = {"architecture", "diagram"}
DOCUMENT_TYPES = {"brief", "prd", "frac", "decision", "document", "file_structure"}  # handover/skills retired 2026-11-11
FAMILIES = {"interface": INTERFACE_TYPES, "erd": ERD_TYPES, "diagram": DIAGRAM_TYPES, "document": DOCUMENT_TYPES}
TOOLS = {f"{verb}_{family}": family for family in FAMILIES for verb in ("create", "update")}
AI_SOURCES = {"ai", "agent"}          # "agent" = rows written before the rename; both read as AI


def family_of(item_type: str) -> str | None:
    return next((name for name, kinds in FAMILIES.items() if item_type in kinds), None)


def normalise_tools(edits: list[dict]) -> list[str]:
    """Check/derive each edit's tool name and strip the ``tool`` key (apply_agent_edits rejects it)."""
    names = []
    for edit in edits:
        family = family_of(edit.get("item_type", ""))
        derived = f"{'update' if 'id' in edit else 'create'}_{family}" if family else None
        named = edit.pop("tool", None)
        if named is not None and (named not in TOOLS or named != derived):
            raise ValueError(f"Tool {str(named)[:40]!r} does not match the edit (expected {derived}).")
        if derived is None:
            raise ValueError("This item type has no chat tool.")
        names.append(derived)
    return names


def split_dirty(edits: list[dict], names: list[str], drafts: list[dict] | None):
    """N1 Q3 default: an item with an unsaved draft in the open editor is never written by the AI.
    Returns (edits to apply, their names, conflict cards)."""
    dirty = {}
    for draft in drafts or []:
        if isinstance(draft, dict) and type(draft.get("id")) is int:
            dirty[draft["id"]] = draft.get("base_revision")
    keep, keep_names, conflicts = [], [], []
    for edit, name in zip(edits, names):
        if edit.get("id") in dirty:
            conflicts.append({"tool": name, "id": edit["id"], "item_type": edit["item_type"], "title": edit["title"],
                              "status": "conflict", "base_revision": dirty[edit["id"]],
                              "current_revision": edit.get("expected_revision"),
                              "content_format": edit["content_format"], "proposed_content": edit["content"]})
        else:
            keep.append(edit)
            keep_names.append(name)
    return keep, keep_names, conflicts


def tool_cards(names: list[str], results: list[dict], revisions: dict, conflicts: list[dict]) -> list[dict[str, Any]]:
    cards = []
    for name, result in zip(names, results):
        to_revision = int(result.get("revision_count") or result.get("revision") or 0)
        from_revision = int(revisions.get(result["id"], 0)) if name.startswith("update_") else 0
        cards.append({"tool": name, "id": result["id"], "item_type": result.get("item_type"), "title": result.get("title"),
                      "status": "applied", "from_revision": from_revision, "to_revision": to_revision,
                      "warnings": list(result.get("validation_warnings") or [])[:5]})
    return cards + conflicts


def restore_revision(workspace_id: int, item_id: int, *, revision: int, expected_revision: int) -> dict[str, Any]:
    """Undo for a tool card. revision >= 1: write that revision's content as a new revision.
    revision == 0 (undo a create): archive the item, only when the AI created it."""
    from distr.core.planning import service
    from distr.core.db.workflow import PlanItemRevision
    if type(revision) is not int or revision < 0 or type(expected_revision) is not int or expected_revision < 1:
        raise ValueError("revision and expected_revision are required.")
    if revision == 0:
        return {"archived": True, **service.archive_item(workspace_id, item_id, expected_revision=expected_revision,
                                                           allow_ai_created=True)}
    if revision >= expected_revision:
        raise ValueError("Pick an earlier revision to restore.")
    with service.get_session() as db:
        row = db.query(PlanItemRevision).filter_by(item_id=int(item_id), revision=int(revision)).one_or_none()
        if row is None:
            raise LookupError("That revision does not exist.")
        title, content = row.title, row.content
    updated = service.update_item(item_id, title=title, content=content, source="restore",
                                  instruction=f"Restored revision {revision} (undo)", expected_revision=expected_revision,
                                  workspace_id=workspace_id)
    if updated is None:
        raise LookupError("Plan item not found in this workspace.")
    return updated
