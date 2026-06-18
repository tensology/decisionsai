"""Harness-agnostic workflow handoff — copy-paste instructions for any IDE/CLI agent."""

from __future__ import annotations

import os
from typing import Any

from distr.core.workflow.step_iteration import HARNESS_REPORT_TEMPLATE

from .learning_guide import workflow_learning_guide_md
from .paths import AGENTS_FILE, CONTEXT_FILE, ROUTER_FILE, companion_root
from .pickup_handoff import read_handoff_preview
from .provision import bootstrap_workflow, build_step_routing_table
from .references import sync_entity_references
from .router import router_chain, workspace_summary
from .stages import sync_workflow_stages


def _expand(path: str) -> str:
    return os.path.expanduser(path) if path else ""


def _workflow_board_and_project(workflow_id: int) -> tuple[int | None, int | None, str, str]:
    """Return board_id, default project_id, workflow_name, project_folder."""
    from distr.core.db import get_session
    from distr.core.db.projects import Project
    from distr.core.db.workflow import AutoWorkflow
    from .provision import _workflow_board_id

    board_id: int | None = None
    project_id: int | None = None
    workflow_name = f"Workflow {workflow_id}"
    project_folder = ""
    try:
        board_id = _workflow_board_id(workflow_id)
        with get_session() as session:
            wf = session.query(AutoWorkflow).filter(AutoWorkflow.id == int(workflow_id)).first()
            if wf:
                workflow_name = wf.name or workflow_name
            if board_id:
                project = (
                    session.query(Project)
                    .filter(Project.kanban_board_id == int(board_id))
                    .order_by(Project.id)
                    .first()
                )
                if project:
                    project_id = project.id
                    project_folder = project.folder_location or ""
    except Exception:
        pass
    return board_id, project_id, workflow_name, project_folder


def _companion_file_tree(workflow_id: int) -> str:
    root = companion_root("workflows", workflow_id)
    if not root.is_dir():
        return f"{_expand(str(root))}/\n  (not provisioned yet — open handoff modal to scaffold)"
    lines = [f"{_expand(str(root))}/"]
    for path in sorted(root.rglob("*")):
        if path.is_file() and path.name.startswith("."):
            continue
        rel = path.relative_to(root)
        depth = len(rel.parts)
        indent = "  " * depth
        lines.append(f"{indent}{rel.as_posix()}")
    return "\n".join(lines[:80]) + ("\n  …" if len(lines) > 80 else "")


def _read_order(
    *,
    workflow_id: int,
    run_id: int | None,
    board_id: int | None,
    project_id: int | None,
    project_folder: str,
) -> list[dict[str, str]]:
    wf_root = _expand(str(companion_root("workflows", workflow_id)))
    items = [
        {"path": f"{wf_root}/{AGENTS_FILE}", "why": "Workflow entry — mission, gates, pick up/handoff"},
        {"path": f"{wf_root}/{ROUTER_FILE}", "why": "Routing table and parent chain"},
        {"path": f"{wf_root}/{CONTEXT_FILE}", "why": "Context rules, variables, step routing"},
        {"path": f"{wf_root}/memory/handoff.md", "why": "Last session continuity"},
        {"path": f"{wf_root}/memory/active.md", "why": "Active notes for this workflow"},
        {"path": f"{wf_root}/references/learning-guide.md", "why": "How to learn standards and update memory"},
    ]
    if run_id:
        run_root = _expand(str(companion_root("runs", run_id)))
        items.append({"path": f"{run_root}/memory/handoff.md", "why": "Active run continuity"})
    if board_id:
        board_root = _expand(str(companion_root("boards", board_id)))
        items.append({"path": f"{board_root}/references/learned-rules.md", "why": "Board learned validation rules"})
    if project_id:
        proj_root = _expand(str(companion_root("projects", project_id)))
        items.append({"path": f"{proj_root}/agents.md", "why": "Linked project memory"})
        if project_folder.strip():
            items.append(
                {"path": f"{project_folder.rstrip('/')}/.decisions/agents.md", "why": "Repo projection (synced from project memory)"}
            )
    return items


def _pickup_prompt(workflow_name: str, entry_path: str) -> str:
    return (
        f"I am continuing DecisionsAI workflow \"{workflow_name}\".\n"
        f"Read this workflow memory before acting: {entry_path}\n"
        "Then read router.md, context.md, memory/handoff.md, and references/learning-guide.md in that folder.\n"
        "Follow the Return Contract when reporting step completion."
    )


def _paste_block(
    *,
    workflow_id: int,
    workflow_name: str,
    entry_path: str,
    read_order: list[dict[str, str]],
    file_tree: str,
    handoff_preview: str,
    step_routing_table: str,
    run_id: int | None,
) -> str:
    read_lines = "\n".join(f"{i + 1}. `{item['path']}` — {item['why']}" for i, item in enumerate(read_order))
    harness_note = (
        "Works with Cursor, Codex, Claude Code, Pi, Cline, Hermes Agent, VS Code, Antigravity, Kiro, "
        "or any harness that accepts project instructions. Paste into your agent's first message, "
        "custom instructions, or AGENTS.md rules — DecisionsAI does not need to launch the harness for you."
    )
    run_line = f"\nActive run_id: {run_id}\n" if run_id else ""
    handoff_section = handoff_preview.strip() or "_No handoff recorded yet — you are starting fresh._"
    routing_section = (step_routing_table or "").strip() or "(step routing not generated yet)"
    return f"""# DecisionsAI workflow handoff — {workflow_name}

{harness_note}
{run_line}
## Read order (do this first)

{read_lines}

## Workspace tree

```
{file_tree}
```

## Current handoff preview

{handoff_section}

## Step routing

{routing_section}

## Return contract (paste when step is done)

```
{HARNESS_REPORT_TEMPLATE.strip()}
```

## Memory learning

- End of session: update `memory/handoff.md` and say **handoff** in DecisionsAI.
- Durable standards: `references/learning-guide.md` in the workflow folder.
- Repo learnings: `{{repo}}/.decisions/learnings/learnings.jsonl`
- Planning/UI steps: use decisions-open-design; outputs go in `pipeline/brief/` and `pipeline/output/`.

## Entry file

`{entry_path}`
"""


def build_workflow_harness_handoff(
    workflow_id: int,
    *,
    run_id: int | None = None,
    refresh: bool = True,
) -> dict[str, Any]:
    """Ensure workflow memory and build harness-agnostic copy-paste package."""
    if refresh:
        try:
            bootstrap_workflow(int(workflow_id), force=True)
            sync_entity_references("workflows", workflow_id)
            sync_workflow_stages(workflow_id)
        except Exception:
            bootstrap_workflow(int(workflow_id), force=False)

    board_id, project_id, workflow_name, project_folder = _workflow_board_and_project(workflow_id)
    wf_root = _expand(str(companion_root("workflows", workflow_id)))
    entry_path = f"{wf_root}/{AGENTS_FILE}"

    if run_id:
        from .lifecycle import hook_ensure_workspace

        hook_ensure_workspace(
            "runs",
            int(run_id),
            reason="harness_handoff",
            run_kwargs={"workflow_id": workflow_id, "board_id": board_id, "project_id": project_id},
        )

    read_order = _read_order(
        workflow_id=workflow_id,
        run_id=run_id,
        board_id=board_id,
        project_id=project_id,
        project_folder=project_folder,
    )
    file_tree = _companion_file_tree(workflow_id)
    handoff_preview = read_handoff_preview("runs", run_id) if run_id else read_handoff_preview("workflows", workflow_id)
    step_routing = build_step_routing_table(workflow_id)
    paste_block = _paste_block(
        workflow_id=workflow_id,
        workflow_name=workflow_name,
        entry_path=entry_path,
        read_order=read_order,
        file_tree=file_tree,
        handoff_preview=handoff_preview,
        step_routing_table=step_routing,
        run_id=run_id,
    )
    summary = workspace_summary(
        workflow_id=workflow_id,
        run_id=run_id,
        board_id=board_id,
        project_id=project_id,
        folder_location=project_folder,
    )
    repo_projection = ""
    if project_folder.strip():
        repo_projection = f"{project_folder.rstrip('/')}/.decisions/agents.md"

    return {
        "workflow_id": workflow_id,
        "workflow_name": workflow_name,
        "run_id": run_id,
        "companion_root": wf_root,
        "entry_file": entry_path,
        "read_order": read_order,
        "file_tree": file_tree,
        "paste_block": paste_block,
        "pickup_prompt": _pickup_prompt(workflow_name, entry_path),
        "return_contract": HARNESS_REPORT_TEMPLATE.strip(),
        "learning_guide_excerpt": workflow_learning_guide_md(workflow_id=workflow_id, workflow_name=workflow_name)[:1200],
        "handoff_preview": handoff_preview,
        "step_routing_table": step_routing,
        "repo_projection": repo_projection,
        "linked_project_id": project_id,
        "linked_board_id": board_id,
        "router_chain": router_chain(
            workflow_id=workflow_id,
            run_id=run_id,
            board_id=board_id,
            project_id=project_id,
        ),
        "workspace": summary,
    }
