"""Seed the managed ticket workflows.

The single 11-step "Development" pipeline is retired. Work is routed by weight
class instead, so seeding now means making sure the four managed policy
workflows exist, and archiving the old pipeline rather than resurrecting it.

Seeding must never recreate the old pipeline: that is exactly what made it come
back after every restart.
"""

from __future__ import annotations

import logging

from distr.core.workflow.phases import (
    WORKFLOW_CLASSES,
    class_workflow_name,
    ensure_class_workflows,
    rebuild_class_workflow,
    refresh_managed_workflow_instructions,
)

logger = logging.getLogger(__name__)

# Names that used to mean "the one true pipeline". Asking for them now returns
# the managed policy workflows instead.
RETIRED_WORKFLOW_NAMES = {
    "development",
    "development: ticket to implementation",
}


def managed_workflow_names() -> list[str]:
    return [class_workflow_name(key) for key in WORKFLOW_CLASSES]


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
    """Ensure the managed ticket workflows exist and the legacy pipeline is retired."""
    requested = [str(name) for name in (workflow_names or []) if str(name).strip()]
    # A caller asking only for the retired pipeline still gets a working model.
    only_legacy = bool(requested) and all(
        name.strip().lower() in RETIRED_WORKFLOW_NAMES for name in requested
    )

    seeded: dict[str, int] = {}
    error = ""
    try:
        seeded = ensure_class_workflows()
    except Exception as exc:  # pragma: no cover - defensive
        error = str(exc)
        logger.warning("Could not seed managed workflows: %s", exc)

    refreshed: dict[str, int] = {}
    if force_reset:
        for class_key, workflow_id in list(seeded.items()):
            try:
                rebuild_class_workflow(int(workflow_id), class_key)
            except Exception as exc:  # pragma: no cover - defensive
                logger.warning("Could not rebuild %s workflow: %s", class_key, exc)
    else:
        # A workflow persists the instruction composed when it was created, so a
        # phase-contract improvement would never reach existing workflows. Bring
        # them forward in place on every startup; step ids and runs are preserved.
        try:
            refreshed = refresh_managed_workflow_instructions()
        except Exception as exc:  # pragma: no cover - defensive
            refreshed = {}
            logger.warning("Could not refresh managed workflow instructions: %s", exc)

    retired = 0
    try:
        retired = int(retire_legacy_workflows().get("retired") or 0)
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("Could not retire legacy workflows: %s", exc)

    return {
        "seeded_count": len(seeded),
        "skipped_count": 0,
        "seeded_names": list(seeded.keys()),
        "skipped_names": [],
        "force_reset": force_reset,
        "managed": seeded,
        "refreshed": refreshed,
        "retired_legacy": retired,
        "converted_legacy_request": only_legacy,
        **({"error": error} if error else {}),
    }
