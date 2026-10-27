"""Identity and defaults for the retired "Development" pipeline preset.

The single 11-step Development pipeline is retired. Tickets now route to a
managed weight-class workflow (light / standard / heavy / critical) instead of
one canonical mega-pipeline, so the helpers that used to *create* and
*consolidate* Development are gone on purpose.

What remains is only the preset identity, kept because the loop-preset library
still describes the pipeline by name and settings. Nothing here writes to the
database, so it can no longer resurrect the retired workflow.
"""

from __future__ import annotations

from typing import Any


DEVELOPER_WORKFLOW_NAME = "Development"
DEVELOPER_WORKFLOW_SLUG = "development-ticket-to-implementation"
DEVELOPER_WORKFLOW_VERSION = 16

DEVELOPER_WORKFLOW_RUN_SETTINGS: dict[str, Any] = {
    "execution_mode": "sequential",
    "human_checkpoints": True,
    "concurrency_scope": "project",
    "max_parallel_tickets": 1,
    "branch_per_ticket": True,
    "auto_route_models": True,
    "adaptive_multi_model_enabled": True,
    "max_parallel_evaluators": 2,
    "prefer_free_local": True,
    "ui_local_first": True,
    "hosted_ui_escalation_after_failures": 2,
    "independent_validation": True,
    "allow_provider_failover": True,
    "memory_enabled": True,
    "load_workflow_memory": True,
    "load_project_memory": True,
    "capture_memory_deltas": True,
    "capture_failures_and_lessons": True,
}
