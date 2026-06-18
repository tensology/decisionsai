"""Unified workspace context reader — single spine for agent surfaces."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .pickup_handoff import (
    build_pickup_brief,
    load_decisions_json,
    read_handoff_preview,
    read_ledger_tail,
    read_text_file,
)
from .paths import ACTIVE_FILE, HANDOFF_FILE, companion_memory_file, companion_root
from .provision import ensure_workspace
from .router import router_chain, workspace_summary


@dataclass
class WorkspaceContext:
    companion_paths: dict[str, str] = field(default_factory=dict)
    projection_path: str = ""
    router_chain: list[dict[str, str]] = field(default_factory=list)
    handoff_preview: str = ""
    handoff_full: str = ""
    active_notes: str = ""
    pickup_brief: str = ""
    ledger_tail: list[dict[str, Any]] = field(default_factory=list)
    references_index: list[str] = field(default_factory=list)
    org_router: str = ""


def _primary_entity(
    *,
    project_id: int | None,
    board_id: int | None,
    workflow_id: int | None,
    run_id: int | None,
    ticket_id: int | None,
) -> tuple[str, int] | None:
    for entity_type, eid in (
        ("runs", run_id),
        ("tickets", ticket_id),
        ("workflows", workflow_id),
        ("projects", project_id),
        ("boards", board_id),
    ):
        if eid:
            return entity_type, int(eid)  # type: ignore[arg-type]
    return None


def _references_index(entity_type: str, entity_id: int | str) -> list[str]:
    refs = companion_root(entity_type, entity_id) / "references"  # type: ignore[arg-type]
    if not refs.is_dir():
        return []
    return sorted(str(p.relative_to(refs)) for p in refs.rglob("*.md"))


def load_workspace_context(
    *,
    project_id: int | None = None,
    board_id: int | None = None,
    workflow_id: int | None = None,
    run_id: int | None = None,
    ticket_id: int | None = None,
    folder_location: str = "",
    ensure: bool = True,
    include_pickup_brief: bool = False,
) -> WorkspaceContext:
    """Load filesystem workspace context for the most specific linked entities."""
    if ensure:
        for entity_type, eid, kwargs in (
            ("projects", project_id, None),
            ("boards", board_id, None),
            ("workflows", workflow_id, None),
            ("tickets", ticket_id, None),
            ("runs", run_id, {"run_kwargs": {"workflow_id": workflow_id, "board_id": board_id, "ticket_id": ticket_id, "project_id": project_id}}),
        ):
            if eid:
                ensure_workspace(
                    entity_type,
                    int(eid),
                    force=False,
                    run_kwargs=(kwargs or {}).get("run_kwargs"),
                )

    summary = workspace_summary(
        project_id=project_id,
        board_id=board_id,
        workflow_id=workflow_id,
        run_id=run_id,
        ticket_id=ticket_id,
        folder_location=folder_location,
    )
    primary = _primary_entity(
        project_id=project_id,
        board_id=board_id,
        workflow_id=workflow_id,
        run_id=run_id,
        ticket_id=ticket_id,
    )
    handoff_full = active_notes = ""
    references: list[str] = []
    ledger: list[dict[str, Any]] = []
    pickup = ""
    if primary:
        entity_type, entity_id = primary
        handoff_full = read_text_file(companion_memory_file(entity_type, entity_id, HANDOFF_FILE))  # type: ignore[arg-type]
        active_notes = read_text_file(companion_memory_file(entity_type, entity_id, ACTIVE_FILE))  # type: ignore[arg-type]
        references = _references_index(entity_type, entity_id)
        ledger = read_ledger_tail(entity_type, entity_id, limit=20)
        if include_pickup_brief:
            pickup = build_pickup_brief(
                entity_type=entity_type,
                entity_id=entity_id,
                decisions=load_decisions_json(entity_type, entity_id),
            )

    return WorkspaceContext(
        companion_paths=summary.get("companion_paths") or {},
        projection_path=(summary.get("projection_path") or ""),
        router_chain=summary.get("router_chain") or router_chain(
            project_id=project_id,
            board_id=board_id,
            workflow_id=workflow_id,
            run_id=run_id,
            ticket_id=ticket_id,
        ),
        handoff_preview=(summary.get("handoff_preview") or read_handoff_preview(primary[0], primary[1]) if primary else ""),
        handoff_full=handoff_full,
        active_notes=active_notes,
        pickup_brief=pickup,
        ledger_tail=ledger,
        references_index=references,
        org_router=summary.get("org_router") or "",
    )
