"""Plan provenance: where each plan artifact came from (2026-11-11).

Derived from the existing ``plan_item_revisions.source`` column, so no schema change is
needed. ``derived`` = reverse-engineered from the existing project or what has already
been communicated; ``new`` = forward-looking design/AI/workflow output; ``user`` = Paul's
manual edit (an override generation must not overwrite); ``scaffold`` = never generated.
"""
from __future__ import annotations

ORIGIN_BY_SOURCE = {
    "discovery": "derived", "db_link": "derived", "external_file": "derived", "workflow_derived": "derived",
    "requirements_pack": "derived",
    "design": "new", "ai": "new", "agent": "new", "workflow_new": "new", "plan_explicit": "new", "language": "new",
    "user": "user", "restore": "user",
    "scaffold": "scaffold",
}
ORIGIN_LABELS = {
    "derived": "Generated from the project",
    "new": "Generated from new work",
    "user": "Edited by you",
    "scaffold": "Not generated yet",
    "unknown": "Unknown source",
}


def origin_of(source: str | None) -> str:
    return ORIGIN_BY_SOURCE.get(str(source or "").strip().lower(), "unknown")
