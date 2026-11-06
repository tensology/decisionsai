"""Workflow seeding after managed class retirement.

Managed Ticket: light/standard/heavy/critical workflows are gone. Seeding must
never recreate them. The only remaining startup action is archiving the retired
"Development" pipeline name if it somehow reappears.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

RETIRED_WORKFLOW_NAMES = {
    "development",
    "development: ticket to implementation",
}


def managed_workflow_names() -> list[str]:
    """No managed class workflows remain."""
    return []


def retire_legacy_workflows() -> dict[str, int]:
    """Archive the retired Development pipeline. Reversible, never deleted."""
    from distr.core.db import get_session
    from distr.core.db.workflow import AutoWorkflow

    retired = 0
    with get_session() as db:
        rows = (
            db.query(AutoWorkflow)
            .filter(AutoWorkflow.name.in_(["Development", "Development: Ticket to Implementation"]))
            .all()
        )
        for row in rows:
            if str(row.status or "").lower() != "archived":
                row.status = "archived"
                retired += 1
        db.commit()
    return {"retired": retired}


def seed_workflows(force_reset: bool = False, workflow_names=None):
    """Do not create managed class workflows. Optionally archive legacy names."""
    requested = [str(name) for name in (workflow_names or []) if str(name).strip()]
    only_legacy = bool(requested) and all(
        name.strip().lower() in RETIRED_WORKFLOW_NAMES for name in requested
    )
    retired = 0
    try:
        retired = int(retire_legacy_workflows().get("retired") or 0)
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("Could not retire legacy workflows: %s", exc)
    if force_reset:
        logger.info("seed_workflows(force_reset=True): managed class rebuild is retired; no-op")
    return {
        "seeded_count": 0,
        "skipped_count": 0,
        "seeded_names": [],
        "skipped_names": [],
        "force_reset": force_reset,
        "managed": {},
        "refreshed": {},
        "retired_legacy": retired,
        "converted_legacy_request": only_legacy,
    }
