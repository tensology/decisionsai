"""Surface outstanding work in an OpenPencil inspection.

Only fields the format actually carries are used:

* ``untracked_components`` / ``untracked_frames`` - main components, component
  sets and top-level frames (on a page or directly in a section) whose name
  does not appear in any supplied ticket title/description. With no tickets
  supplied every item is untracked; that is reported, not hidden.
* ``empty_slots`` - ``SLOT`` frames with no children. A slot inside a placed
  instance is a content gap; an empty slot on a main component is the
  template default and is reported as ``info`` only.
* ``slot_limit_violations`` - OpenPencil's own ``limitViolations``.
* ``unbound_behaviour_parts`` - ``openpencil.getBehaviour(node).missing``.
* ``unbound_text_in_components`` - text layers inside main components with no
  variable binding. A heuristic hint for data binding, not a format error.
  When the file defines no variables, ``bindings_in_use`` is false and the
  report says so instead of inventing missing bindings.
"""

from __future__ import annotations

import re
from typing import Iterable

_NON_WORD = re.compile(r"[^a-z0-9]+")


def _norm(text: str | None) -> str:
    return _NON_WORD.sub(" ", str(text or "").lower()).strip()


def _ticket_corpus(tickets: Iterable[dict] | None) -> list[tuple[object, str]]:
    corpus = []
    for ticket in tickets or []:
        text = _norm(f"{ticket.get('title') or ''} {ticket.get('description') or ''}")
        if text:
            corpus.append((ticket.get("id"), f" {text} "))
    return corpus


def _match(name: str, corpus: list[tuple[object, str]]) -> list:
    needle = _norm(name)
    if len(needle) < 2:
        return []
    return [tid for tid, text in corpus if f" {needle} " in text]


def find_gaps(inspection: dict, tickets: Iterable[dict] | None = None) -> dict:
    corpus = _ticket_corpus(tickets)
    components = inspection.get("components") or []
    tracked, untracked_components = [], []
    for comp in components:
        if comp.get("isVariant"):
            continue  # a variant is tracked through its component set
        hits = _match(comp.get("name"), corpus)
        entry = {"id": comp.get("id"), "name": comp.get("name"), "type": comp.get("type"), "page": comp.get("page")}
        (tracked if hits else untracked_components).append({**entry, "ticket_ids": hits} if hits else entry)

    untracked_frames = []
    for frame in inspection.get("frames") or []:
        hits = _match(frame.get("name"), corpus)
        if hits:
            tracked.append({"id": frame.get("id"), "name": frame.get("name"), "type": "FRAME", "page": frame.get("page"), "ticket_ids": hits})
        else:
            untracked_frames.append({"id": frame.get("id"), "name": frame.get("name"), "page": frame.get("page")})

    empty_slots, violations = [], []
    for slot in inspection.get("slots") or []:
        if slot.get("limitViolations"):
            violations.append({"id": slot.get("id"), "name": slot.get("name"), "owner": slot.get("ownerName"), "violations": slot["limitViolations"]})
        if slot.get("childCount") == 0:
            in_instance = slot.get("ownerType") == "INSTANCE"
            empty_slots.append({
                "id": slot.get("id"), "name": slot.get("name"), "page": slot.get("page"),
                "owner_id": slot.get("ownerId"), "owner_name": slot.get("ownerName"), "owner_type": slot.get("ownerType"),
                "severity": "gap" if in_instance else "info",
                "reason": "placed instance slot has no content" if in_instance else "template slot is empty by default",
            })

    behaviour_gaps = []
    for comp in components:
        missing = (comp.get("behaviour") or {}).get("missing")
        if missing:
            behaviour_gaps.append({"id": comp.get("id"), "name": comp.get("name"), "kind": comp["behaviour"].get("kind"), "missing": missing})

    variables = inspection.get("variables")
    var_count = variables.get("count") if isinstance(variables, dict) else None
    bindings_in_use = bool(var_count) or bool(inspection.get("bindings"))
    unbound_text = [
        {"id": t.get("id"), "name": t.get("name"), "component": t.get("componentName"), "page": t.get("page"), "sample": t.get("characters")}
        for t in inspection.get("text_in_components") or []
        if "characters" not in (t.get("bound") or [])
    ]
    if not bindings_in_use:
        binding_note = "This file defines no variables and no bound layers; data binding is not in use, so no binding gaps are claimed."
        unbound_text_out: list = []
    else:
        binding_note = "Text layers in main components without a `characters` variable binding (heuristic hint, not a format error)."
        unbound_text_out = unbound_text

    gaps_total = (
        len(untracked_components) + len(untracked_frames)
        + sum(1 for s in empty_slots if s["severity"] == "gap") + len(violations) + len(behaviour_gaps)
    )
    return {
        "tickets_considered": len(corpus),
        "ticket_match": "normalized whole-word name match against ticket title + description",
        "tracked": tracked,
        "untracked_components": untracked_components,
        "untracked_frames": untracked_frames,
        "empty_slots": empty_slots,
        "slot_limit_violations": violations,
        "unbound_behaviour_parts": behaviour_gaps,
        "bindings_in_use": bindings_in_use,
        "variables_defined": var_count,
        "unbound_text_in_components": unbound_text_out,
        "binding_note": binding_note,
        "format_has_slots": bool(inspection.get("slots")),
        "counts": {
            "untracked_components": len(untracked_components),
            "untracked_frames": len(untracked_frames),
            "empty_slots_gap": sum(1 for s in empty_slots if s["severity"] == "gap"),
            "empty_slots_info": sum(1 for s in empty_slots if s["severity"] == "info"),
            "slot_limit_violations": len(violations),
            "unbound_behaviour_parts": len(behaviour_gaps),
            "unbound_text_in_components": len(unbound_text_out),
            "actionable_total": gaps_total,
        },
    }
