"""Generate agents.md, router.md, context.md, and decisions.json from DB state."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from .paths import (
    ACTIVE_FILE,
    AGENTS_FILE,
    CONTEXT_FILE,
    DECISIONS_FILE,
    HANDOFF_FILE,
    LEDGER_FILE,
    PICKUP_FILE,
    ROUTER_FILE,
    companion_memory_file,
    companion_root,
    org_companion_root,
)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _parent_router_line(parent_path: str | None) -> str:
    if not parent_path:
        return "parent: none"
    return f"parent: {parent_path}"


def workflow_agents_md(*, workflow_id: int, name: str, mission: str = "") -> str:
    mission_line = mission.strip() or f"Execute workflow {name}."
    return f"""# DecisionsAI workspace — workflow: {name}

Read `router.md` before acting. If this is the wrong room, follow the parent link in router.md.

## Mission
{mission_line}

## Gates
- Routing ambiguous → read parent router only (do not workspace-wander)
- Risky actions → check board orchestrator policy when a board is linked
- End of session → update memory/handoff.md (handoff protocol)

## Pick up
Say "pick up" or read memory/handoff.md + memory/active.md + references/learning-guide.md.

## Handoff
Say "handoff" to persist session continuity for the next harness.

## Agentic learning (this workflow)
- Standards and memory protocol: `references/learning-guide.md`
- Step contracts: `stages/{{NN}}_{{slug}}/CONTEXT.md` and `context.md` routing table
- Durable rules: `references/context-rules.md`, board `references/learned-rules.md`
- Repo learnings: linked project `.decisions/learnings/learnings.jsonl`
- Planning/UI: use decisions-open-design; write to `pipeline/brief/` and `pipeline/output/`

## Multi-LLM
Claude/Cursor/Codex/Pi/Cline/Hermes: this file is canonical. Paste the workflow companion path into any harness.
workflow_id: {workflow_id}
"""


def agents_md(*, entity_type: str, name: str, mission: str = "") -> str:
    mission_line = mission.strip() or f"Work on {name}."
    naming = ""
    if entity_type == "project":
        naming = """
## Naming conventions
- Ticket work packets: `.tickets/ticket_{id}_{slug}_{timestamp}_s{step}.md`
- Pipeline outputs: `pipeline/{brief|spec|build|output}/YYYY-MM-DD_{step}_{slug}.md`
- Handoff always updates `memory/handoff.md` before switching harnesses
"""
    return f"""# DecisionsAI workspace — {entity_type}: {name}

Read `router.md` before acting. If this is the wrong room, follow the parent link in router.md.

## Mission
{mission_line}

## Gates
- Routing ambiguous → read parent router only (do not workspace-wander)
- Risky actions → check board orchestrator policy when a board is linked
- End of session → update memory/handoff.md (handoff protocol)

## Pick up
Say "pick up" or read memory/handoff.md + memory/active.md.

## Handoff
Say "handoff" to persist session continuity for the next harness.

## Multi-LLM
Claude/Cursor/Codex: this file is canonical. CLAUDE.md points here.
{naming}"""


def projection_agents_md(*, companion_path: str, entity_type: str, name: str, mission: str = "") -> str:
    body = agents_md(entity_type=entity_type, name=name, mission=mission).strip()
    return (
        f"<!-- companion_root: {companion_path} -->\n\n"
        f"{body}\n\n"
        f"## Companion store\n"
        f"Source of truth: `{companion_path}`\n"
    )


def root_agents_redirector() -> str:
    return """# DecisionsAI agent entry

Read `.decisions/agents.md` in this repository before acting.

Claude/Cursor/Codex: this file is the multi-LLM entry point.
"""


def root_claude_redirector() -> str:
    return """# Claude entry

Read `AGENTS.md` in this repository, then follow its instructions.
"""


def empty_handoff_md() -> str:
    return f"# Handoff\n\n_No handoff recorded yet._\n\n_updated: {_utc_now_iso()}_\n"


def empty_active_md() -> str:
    return f"# Active state\n\n_No active notes._\n\n_updated: {_utc_now_iso()}_\n"


def write_json(path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = dict(data)
    payload.setdefault("updated_at", _utc_now_iso())
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_text(path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content if content.endswith("\n") else content + "\n", encoding="utf-8")


def ensure_memory_scaffold(entity_type: str, entity_id: int | str) -> None:
    root = companion_root(entity_type, entity_id)  # type: ignore[arg-type]
    for name, factory in (
        (HANDOFF_FILE, empty_handoff_md),
        (ACTIVE_FILE, empty_active_md),
        (PICKUP_FILE, "# Pickup brief\n\n"),
    ):
        path = companion_memory_file(entity_type, entity_id, name)  # type: ignore[arg-type]
        if not path.exists():
            write_text(path, factory() if callable(factory) else factory)
    ledger = companion_memory_file(entity_type, entity_id, LEDGER_FILE)  # type: ignore[arg-type]
    ledger.parent.mkdir(parents=True, exist_ok=True)
    if not ledger.exists():
        ledger.write_text("", encoding="utf-8")


def org_router_md(
    *,
    board_count: int = 0,
    project_count: int = 0,
    workflow_count: int = 0,
    board_registry: list[dict[str, Any]] | None = None,
    project_registry: list[dict[str, Any]] | None = None,
    workflow_registry: list[dict[str, Any]] | None = None,
) -> str:
    boards = board_registry or []
    projects = project_registry or []
    workflows = workflow_registry or []

    def _registry_rows(items: list[dict[str, Any]], path_tpl: str) -> str:
        if not items:
            return "| (none) | — |"
        lines = []
        for item in items[:40]:
            name = (item.get("name") or item.get("id") or "?").strip()
            eid = item.get("id")
            lines.append(f"| {name} | `{path_tpl.format(id=eid)}` |")
        return "\n".join(lines)

    return f"""# Org router — DecisionsAI

{_parent_router_line(None)}

## Lobby map
- boards: {board_count} board(s) under `~/.decisions/workspaces/boards/{{id}}/`
- projects: {project_count} project(s) under `~/.decisions/workspaces/projects/{{id}}/`
- workflows: {workflow_count} workflow(s) under `~/.decisions/workspaces/workflows/{{id}}/`

## Entity registry

### Boards
| Name | Companion path |
|------|----------------|
{_registry_rows(boards, "~/.decisions/workspaces/boards/{id}/")}

### Projects
| Name | Companion path |
|------|----------------|
{_registry_rows(projects, "~/.decisions/workspaces/projects/{id}/")}

### Workflows
| Name | Companion path |
|------|----------------|
{_registry_rows(workflows, "~/.decisions/workspaces/workflows/{id}/")}

## Codewords
- pick up → read nearest memory/handoff.md + memory/active.md + decisions.json
- handoff → write memory/handoff.md and append memory/ledger.jsonl
- route law → if not listed here, read parent router for the entity you are in

## If lost
Read this file first. Route to the entity folder, then read that entity's `agents.md`.

## Global memory
- chat personality files: `models/memory/` (AGENT.md, USER.md, MEMORY.md)
- orchestrator API: `/api/orchestrator/memories`
"""


def board_router_md(
    *,
    board_id: int,
    board_name: str,
    default_project_id: int | None,
    default_workflow_id: int | None,
    lane_names: list[str],
    parent_path: str,
) -> str:
    lanes = ", ".join(lane_names) if lane_names else "(none)"
    return f"""# Board router — {board_name}

{_parent_router_line(parent_path)}

## Board
- board_id: {board_id}
- default_project_id: {default_project_id or "none"}
- default_workflow_id: {default_workflow_id or "none"}
- lanes: {lanes}

## Routes
| Intent | Go to |
|--------|-------|
| board notes / scratchpad | memory/active.md |
| ticket work | `~/.decisions/workspaces/tickets/{{ticket_id}}/` |
| default project | `~/.decisions/workspaces/projects/{default_project_id or "{project_id}"}/` |
| default workflow | `~/.decisions/workspaces/workflows/{default_workflow_id or "{workflow_id}"}/` |
| learned rules | orchestrator board scope (board_id={board_id}) |

## If lost
Return to parent router.
"""


def project_router_md(
    *,
    project_id: int,
    project_name: str,
    folder_location: str,
    kanban_board_id: int | None,
    coding_backend: str,
    parent_path: str,
) -> str:
    return f"""# Project router — {project_name}

{_parent_router_line(parent_path)}

## Project
- project_id: {project_id}
- folder: `{folder_location or "(not set)"}`
- kanban_board_id: {kanban_board_id or "none"}
- coding_backend: {coding_backend or "default"}

## Harness targets (when folder exists)
- `.cursor/commands`
- `.codex/commands`
- `.pi/skills`
- `.tickets/` work packets

## Routes
| Intent | Go to |
|--------|-------|
| session continuity | memory/handoff.md, memory/active.md |
| repo projection | `{{folder}}/.decisions/` |
| linked board | `~/.decisions/workspaces/boards/{kanban_board_id or "{board_id}"}/` |

## If lost
Return to parent board router or org router.
"""


def workflow_router_md(
    *,
    workflow_id: int,
    workflow_name: str,
    board_id: int | None,
    step_lines: list[str],
    parent_path: str,
) -> str:
    steps = "\n".join(step_lines) if step_lines else "- (no steps)"
    return f"""# Workflow router — {workflow_name}

{_parent_router_line(parent_path)}

## Workflow
- workflow_id: {workflow_id}
- default_board_id: {board_id or "none"}

## Steps
{steps}

## Routes
| Intent | Go to |
|--------|-------|
| run continuity | `~/.decisions/workspaces/runs/{{run_id}}/memory/handoff.md` |
| agent context | context.md |
| pipeline brief | pipeline/brief/ |
| pipeline output | pipeline/output/ |
| learning / memory protocol | references/learning-guide.md |
| step contracts | stages/{{NN}}_{{slug}}/CONTEXT.md |

## If lost
Return to parent board router or org router.
"""


def workflow_context_md(
    *,
    context_rules: str,
    agent_context_sections: list[tuple[str, str]],
    step_routing_table: str,
) -> str:
    sections = []
    if context_rules.strip():
        sections.append(f"## Context rules\n\n{context_rules.strip()}\n")
    if agent_context_sections:
        sections.append("## Agent context\n")
        for title, body in agent_context_sections:
            sections.append(f"### {title}\n\n{body.strip()}\n")
    if step_routing_table.strip():
        sections.append(step_routing_table.strip() + "\n")
    if not sections:
        sections.append("## Routing\n\n| Task | Read | Skip | Skills |\n|------|------|------|--------|\n| default | memory/handoff.md | pipeline/output/ | — |\n")
    return "\n".join(sections)


def default_step_routing_table() -> str:
    return """## Step routing

| Task / step kind | Read | Skip | Skills |
|------------------|------|------|--------|
| planning | pipeline/brief/, parent board router | pipeline/output/ | planning |
| ide_handoff | memory/handoff.md, active ticket context | pipeline/build | cursor/codex worker |
| validation | board learned rules, last run steering | pipeline/brief | browser-qa |
| default | memory/handoff.md, memory/active.md | pipeline/output/ | — |"""


def board_context_md(*, description: str, orchestrator_policy: dict[str, Any]) -> str:
    policy = json.dumps(orchestrator_policy or {}, ensure_ascii=False, indent=2)
    desc = description.strip() or "(no description)"
    return f"""# Board context

## Description
{desc}

## Orchestrator policy
```json
{policy}
```

## Active notes
See memory/active.md for the board scratchpad.
"""


def project_context_md(*, startup_instructions: str, context_items: list[dict[str, Any]]) -> str:
    lines = ["# Project context\n"]
    if startup_instructions.strip():
        lines.extend(["## Startup instructions\n", startup_instructions.strip(), ""])
    if context_items:
        lines.append("## Context items\n")
        for item in context_items:
            title = (item.get("title") or "Untitled").strip()
            body = (item.get("content") or "").strip()
            lines.append(f"### {title}\n\n{body}\n")
    if len(lines) == 1:
        lines.append("_No project context items yet._\n")
    return "\n".join(lines)


def ticket_router_md(
    *,
    ticket_id: int,
    title: str,
    board_id: int | None,
    linked_project_id: int | None,
    linked_workflow_id: int | None,
    parent_path: str,
) -> str:
    return f"""# Ticket router — {title}

{_parent_router_line(parent_path)}

## Ticket
- ticket_id: {ticket_id}
- board_id: {board_id or "none"}
- linked_project_id: {linked_project_id or "none"}
- linked_workflow_id: {linked_workflow_id or "none"}

## Routes
| Intent | Go to |
|--------|-------|
| session continuity | memory/handoff.md, memory/active.md |
| ticket truth | context.md |
| linked project | `~/.decisions/workspaces/projects/{linked_project_id or "{project_id}"}/` |
| linked workflow | `~/.decisions/workspaces/workflows/{linked_workflow_id or "{workflow_id}"}/` |
| work packet | `{{project_folder}}/.tickets/ticket_{ticket_id}_*.md` |
| board policy | `~/.decisions/workspaces/boards/{board_id or "{board_id}"}/references/learned-rules.md` |

## If lost
Return to parent board router or org router.
"""


def ticket_context_md(*, title: str, description: str, context_notes: str) -> str:
    return f"""# Ticket context — {title}

## Description
{(description or "").strip() or "(none)"}

## Context notes
{(context_notes or "").strip() or "(none)"}
"""


def write_entity_files(
    entity_type: str,
    entity_id: int | str,
    *,
    agents_content: str,
    router_content: str,
    context_content: str,
    decisions: dict[str, Any],
) -> str:
    """Write scaffold files; return companion root path string."""
    from .paths import ensure_companion_dirs

    root = ensure_companion_dirs(entity_type, entity_id)  # type: ignore[arg-type]
    ensure_memory_scaffold(entity_type, entity_id)
    write_text(root / AGENTS_FILE, agents_content)
    write_text(root / ROUTER_FILE, router_content)
    write_text(root / CONTEXT_FILE, context_content)
    write_json(root / DECISIONS_FILE, decisions)
    return str(root)
