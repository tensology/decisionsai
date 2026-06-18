"""Sync Layer 3 reference material from DB into companion references/."""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

from .paths import companion_root
from .template import write_text

logger = logging.getLogger(__name__)

_REFERENCES_DIR = "references"


def _slugify(value: str, *, max_len: int = 48) -> str:
    text = re.sub(r"[^a-zA-Z0-9]+", "-", (value or "").strip().lower()).strip("-")
    return (text[:max_len] or "item")


def _refs_root(entity_type: str, entity_id: int | str) -> Path:
    return companion_root(entity_type, entity_id) / _REFERENCES_DIR  # type: ignore[arg-type]


def sync_board_references(board_id: int) -> str:
    """Export board-scoped learned rules to references/learned-rules.md."""
    from distr.core.orchestrator import list_learned_rules

    root = _refs_root("boards", board_id)
    root.mkdir(parents=True, exist_ok=True)
    path = root / "learned-rules.md"
    rules = list_learned_rules(board_id=int(board_id), enabled_only=False, limit=200)
    lines = ["# Learned rules", ""]
    if not rules:
        lines.append("_No board learned rules yet._")
    else:
        for rule in rules:
            summary = (rule.get("summary") or "Rule").strip()
            enabled = "enabled" if rule.get("enabled") else "disabled"
            lines.append(f"## {summary} ({enabled})")
            payload = rule.get("payload") or {}
            if isinstance(payload, dict) and payload:
                lines.append(f"```json\n{json.dumps(payload, ensure_ascii=False, indent=2)}\n```")
            lines.append("")
    write_text(path, "\n".join(lines).strip() + "\n")
    return str(path)


def sync_project_references(project_id: int) -> list[str]:
    """Mirror project context items into references/context-items/."""
    from distr.core.db import get_session
    from distr.core.db.projects import ProjectContextItem
    from distr.core.orchestrator import list_learned_rules

    root = _refs_root("projects", project_id)
    items_dir = root / "context-items"
    items_dir.mkdir(parents=True, exist_ok=True)
    written: list[str] = []

    with get_session() as session:
        rows = (
            session.query(ProjectContextItem)
            .filter(ProjectContextItem.project_id == int(project_id))
            .order_by(ProjectContextItem.id)
            .all()
        )
    for row in rows:
        slug = _slugify(row.title or f"item-{row.id}")
        path = items_dir / f"{row.id}_{slug}.md"
        body = (row.content or "").strip() or "(empty)"
        write_text(path, f"# {(row.title or 'Untitled').strip()}\n\n{body}\n")
        written.append(str(path))

    rules = list_learned_rules(scope="project", scope_id=int(project_id), enabled_only=False, limit=200)
    rules_path = root / "learned-rules.md"
    rule_lines = ["# Project learned rules", ""]
    if not rules:
        rule_lines.append("_No project learned rules yet._")
    else:
        for rule in rules:
            rule_lines.append(f"## {(rule.get('summary') or 'Rule').strip()}")
            payload = rule.get("payload") or {}
            if isinstance(payload, dict) and payload:
                rule_lines.append(f"```json\n{json.dumps(payload, ensure_ascii=False, indent=2)}\n```")
            rule_lines.append("")
    write_text(rules_path, "\n".join(rule_lines).strip() + "\n")
    written.append(str(rules_path))
    return written


def sync_workflow_references(workflow_id: int) -> list[str]:
    """Export workflow context_rules and variables to references/."""
    from distr.core.db import get_session
    from distr.core.db.workflow import AutoWorkflow, AutoWorkflowVariable

    root = _refs_root("workflows", workflow_id)
    root.mkdir(parents=True, exist_ok=True)
    ctx_dir = root / "agent-context"
    ctx_dir.mkdir(parents=True, exist_ok=True)
    written: list[str] = []

    with get_session() as session:
        wf = session.query(AutoWorkflow).filter(AutoWorkflow.id == int(workflow_id)).first()
        if wf and (wf.context_rules or "").strip():
            rules_path = root / "context-rules.md"
            write_text(rules_path, f"# Context rules\n\n{wf.context_rules.strip()}\n")
            written.append(str(rules_path))
        rows = (
            session.query(AutoWorkflowVariable)
            .filter(AutoWorkflowVariable.workflow_id == int(workflow_id))
            .order_by(AutoWorkflowVariable.id)
            .all()
        )
    for row in rows:
        slug = _slugify(row.name or f"var-{row.id}")
        path = ctx_dir / f"{row.id}_{slug}.md"
        body = (row.default_value or "").strip() or "(empty)"
        write_text(path, f"# {(row.name or 'Context').strip()}\n\n{body}\n")
        written.append(str(path))

    from .learning_guide import sync_workflow_learning_guide

    written.append(sync_workflow_learning_guide(workflow_id))
    return written


def sync_entity_references(entity_type: str, entity_id: int | str) -> list[str]:
    """Refresh Layer 3 references for a supported entity."""
    if entity_type == "boards":
        return [sync_board_references(int(entity_id))]
    if entity_type == "projects":
        return sync_project_references(int(entity_id))
    if entity_type == "workflows":
        return sync_workflow_references(int(entity_id))
    return []
