"""Resolve companion and projection paths for agent workspace memory."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

EntityType = Literal["org", "boards", "projects", "workflows", "runs", "tickets"]

WORKSPACES_ROOT = Path(os.path.expanduser("~")) / ".decisions" / "workspaces"
ORG_SLUG = "decisionsai"

PROJECTION_DIRNAME = ".decisions"
MEMORY_DIRNAME = "memory"
PIPELINE_DIRNAME = "pipeline"
REFERENCES_DIRNAME = "references"
STAGES_DIRNAME = "stages"

AGENTS_FILE = "agents.md"
ROUTER_FILE = "router.md"
CONTEXT_FILE = "context.md"
DECISIONS_FILE = "decisions.json"
HANDOFF_FILE = "handoff.md"
PICKUP_FILE = "pickup.md"
ACTIVE_FILE = "active.md"
LEDGER_FILE = "ledger.jsonl"

ROOT_AGENTS_FILE = "AGENTS.md"
ROOT_CLAUDE_FILE = "CLAUDE.md"


def workspaces_root() -> Path:
    return WORKSPACES_ROOT


def org_companion_root() -> Path:
    return WORKSPACES_ROOT / "org" / ORG_SLUG


def companion_root(entity_type: EntityType, entity_id: int | str) -> Path:
    return WORKSPACES_ROOT / entity_type / str(entity_id)


def companion_file(entity_type: EntityType, entity_id: int | str, filename: str) -> Path:
    return companion_root(entity_type, entity_id) / filename


def companion_memory_file(entity_type: EntityType, entity_id: int | str, filename: str) -> Path:
    return companion_root(entity_type, entity_id) / MEMORY_DIRNAME / filename


def projection_root(project_folder: str) -> Path:
    return Path(project_folder).expanduser().resolve() / PROJECTION_DIRNAME


def projection_memory_file(project_folder: str, filename: str) -> Path:
    return projection_root(project_folder) / MEMORY_DIRNAME / filename


def ensure_companion_dirs(entity_type: EntityType, entity_id: int | str) -> Path:
    root = companion_root(entity_type, entity_id)
    (root / MEMORY_DIRNAME).mkdir(parents=True, exist_ok=True)
    (root / REFERENCES_DIRNAME).mkdir(parents=True, exist_ok=True)
    (root / PIPELINE_DIRNAME / "brief").mkdir(parents=True, exist_ok=True)
    (root / PIPELINE_DIRNAME / "spec").mkdir(parents=True, exist_ok=True)
    (root / PIPELINE_DIRNAME / "build").mkdir(parents=True, exist_ok=True)
    (root / PIPELINE_DIRNAME / "output").mkdir(parents=True, exist_ok=True)
    if entity_type == "workflows":
        (root / STAGES_DIRNAME).mkdir(parents=True, exist_ok=True)
    return root


def ensure_projection_dirs(project_folder: str) -> Path:
    root = projection_root(project_folder)
    (root / MEMORY_DIRNAME).mkdir(parents=True, exist_ok=True)
    return root
