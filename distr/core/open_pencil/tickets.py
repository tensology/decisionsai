"""Ticket generation from OpenPencil gaps: DEFERRED.

This slice only defines the interface. ``draft_ticket_specs`` turns a gap
report into plain dicts shaped like a kanban ticket (title, description,
priority, external_source/external_id for idempotence). It performs no DB
writes, no workflow dispatch and no outbound sends. ``create_tickets_from_gaps``
deliberately raises until the reader is accepted and the hook into the
existing ticket/workflow pipeline is approved.
"""

from __future__ import annotations

EXTERNAL_SOURCE = "open-pencil"


def draft_ticket_specs(inspection: dict, gaps: dict) -> list[dict]:
    source = (inspection.get("source") or {})
    file_name = str(source.get("path") or "").rsplit("/", 1)[-1]
    specs: list[dict] = []

    def add(kind: str, node_id: str, title: str, description: str, priority: str = "medium") -> None:
        specs.append({
            "title": title[:200],
            "description": description,
            "priority": priority,
            "external_source": EXTERNAL_SOURCE,
            "external_id": f"{source.get('sha256', '')[:12]}:{kind}:{node_id}",
            "gap_kind": kind,
            "dry_run": True,
        })

    for comp in gaps.get("untracked_components") or []:
        add("component", comp["id"], f"Implement component: {comp['name']}",
            f"OpenPencil {comp.get('type', 'COMPONENT').lower()} '{comp['name']}' (node {comp['id']}, page '{comp.get('page')}') in {file_name} has no matching ticket.")
    for frame in gaps.get("untracked_frames") or []:
        add("frame", frame["id"], f"Build screen: {frame['name']}",
            f"Top-level frame '{frame['name']}' (node {frame['id']}, page '{frame.get('page')}') in {file_name} has no matching ticket.")
    for slot in gaps.get("empty_slots") or []:
        if slot.get("severity") == "gap":
            add("slot", slot["id"], f"Fill slot '{slot['name']}' in {slot.get('owner_name')}",
                f"Instance slot '{slot['name']}' (node {slot['id']}) in {file_name} has no content.", "low")
    for item in gaps.get("unbound_behaviour_parts") or []:
        add("behaviour", item["id"], f"Bind behaviour parts for {item['name']}",
            f"Component '{item['name']}' ({item.get('kind')}) still misses: {item.get('missing')}.", "low")
    return specs


def create_tickets_from_gaps(*_args, **_kwargs):
    raise NotImplementedError(
        "OpenPencil ticket generation is deferred: use draft_ticket_specs() for a dry run. "
        "Wiring into the kanban/workflow pipeline needs explicit approval."
    )
