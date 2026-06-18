"""ICM stage contracts — per-step CONTEXT.md under workflow companion stores."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from distr.core.db import get_session

from .paths import STAGES_DIRNAME, companion_root
from .template import write_text

_STAGE_CONTEXT = "CONTEXT.md"


def _slugify(value: str, *, max_len: int = 40) -> str:
    text = re.sub(r"[^a-zA-Z0-9]+", "-", (value or "").strip().lower()).strip("-")
    return (text[:max_len] or "step")


def _pipeline_bucket(action_type: str, step_name: str) -> str:
    name = (step_name or "").lower()
    action = (action_type or "").strip().lower()
    if "plan" in name or action == "agent_instruction" and "brief" in name:
        return "brief"
    if action in {"send_to_project_cli", "run_command"}:
        return "build"
    if "validation" in name or action == "http_request":
        return "spec"
    return "output"


def _stage_read_skip_skills(action_type: str, step_name: str) -> tuple[str, str, str]:
    action = (action_type or "agent_instruction").strip()
    step_name_l = (step_name or "").lower()
    read = "memory/handoff.md, memory/active.md, references/"
    skip = "pipeline/output/"
    skills = "—"
    if action in {"send_to_project_cli", "run_command"}:
        read = "memory/handoff.md, ticket context, project router, references/"
        skip = "unrelated board routers"
        skills = "decisions-cursor-worker / decisions-codex-worker"
    elif "validation" in step_name_l or action == "http_request":
        read = "references/learned-rules.md, run steering log"
        skip = "pipeline/brief/"
        skills = "browser-qa"
    elif "plan" in step_name_l:
        read = "pipeline/brief/, references/, board router"
        skip = "pipeline/output/"
        skills = "planning"
    return read, skip, skills


def stage_context_md(
    *,
    step_position: int,
    step_name: str,
    action_type: str,
    instruction: str,
    pre_chain: str = "",
    post_chain: str = "",
    linked_project_id: int | None = None,
    prev_stage: str = "",
) -> str:
    read, skip, skills = _stage_read_skip_skills(action_type, step_name)
    bucket = _pipeline_bucket(action_type, step_name)
    prev_input = f"stages/{prev_stage}/output/" if prev_stage else "pipeline/brief/"
    pre_skills = (pre_chain or "").strip()
    post_skills = (post_chain or "").strip()
    skill_col = skills
    if pre_skills:
        skill_col = f"{pre_skills}" + (f", {skills}" if skills != "—" else "")
    if post_skills:
        skill_col = f"{skill_col}, post: {post_skills}" if skill_col != "—" else f"post: {post_skills}"

    lines = [
        f"# Stage — {step_name}",
        "",
        "## Inputs",
        "",
        f"- Layer 4 (working): `{prev_input}`",
        "- Layer 3 (reference): `references/`",
        "- Layer 2 (routing): parent workflow `context.md`",
        "",
        "## Process",
        "",
        (instruction or "").strip() or f"Execute step: {step_name}.",
        "",
        f"- action_type: `{action_type}`",
    ]
    if linked_project_id:
        lines.append(f"- linked_project_id: {linked_project_id}")
    lines.extend(
        [
            "",
            "## Outputs",
            "",
            f"- stage artifact → `output/`",
            f"- shared pipeline → `pipeline/{bucket}/`",
            "",
            "## Routing",
            "",
            "| Read | Skip | Skills |",
            "|------|------|--------|",
            f"| {read} | {skip} | {skill_col} |",
            "",
        ]
    )
    return "\n".join(lines)


def sync_workflow_stages(workflow_id: int) -> list[str]:
    """Generate stages/{NN}_{slug}/CONTEXT.md for each workflow step."""
    from distr.core.db.workflow import AutoWorkflow, AutoWorkflowStep

    root = companion_root("workflows", workflow_id)
    stages_root = root / STAGES_DIRNAME
    stages_root.mkdir(parents=True, exist_ok=True)
    written: list[str] = []

    with get_session() as session:
        wf = session.query(AutoWorkflow).filter(AutoWorkflow.id == int(workflow_id)).first()
        pre_chain = (wf.pre_chain or "") if wf else ""
        post_chain = (wf.post_chain or "") if wf else ""
        steps = (
            session.query(AutoWorkflowStep)
            .filter(AutoWorkflowStep.workflow_id == int(workflow_id))
            .order_by(AutoWorkflowStep.position)
            .all()
        )

    prev_slug = ""
    for step in steps:
        pos = int(step.position or 0) + 1
        slug = _slugify(step.name or f"step-{step.id}")
        stage_dir = stages_root / f"{pos:02d}_{slug}"
        stage_dir.mkdir(parents=True, exist_ok=True)
        (stage_dir / "output").mkdir(parents=True, exist_ok=True)
        content = stage_context_md(
            step_position=pos,
            step_name=step.name or f"Step {pos}",
            action_type=step.action_type or "agent_instruction",
            instruction=step.instruction or "",
            pre_chain=pre_chain,
            post_chain=post_chain,
            linked_project_id=step.linked_project_id,
            prev_stage=prev_slug,
        )
        path = stage_dir / _STAGE_CONTEXT
        write_text(path, content)
        written.append(str(path))
        prev_slug = f"{pos:02d}_{slug}"
    return written


def build_step_routing_from_stages(workflow_id: int) -> str:
    """Build routing table from stage CONTEXT files when present."""
    from distr.core.db.workflow import AutoWorkflowStep

    stages_root = companion_root("workflows", workflow_id) / STAGES_DIRNAME
    with get_session() as session:
        steps = (
            session.query(AutoWorkflowStep)
            .filter(AutoWorkflowStep.workflow_id == int(workflow_id))
            .order_by(AutoWorkflowStep.position)
            .all()
        )
        step_rows = [
            {
                "position": int(step.position or 0),
                "id": step.id,
                "name": step.name or "",
                "action_type": step.action_type or "agent_instruction",
            }
            for step in steps
        ]
    if not step_rows:
        from .template import default_step_routing_table

        return default_step_routing_table()

    lines = [
        "## Step routing",
        "",
        "| Step | Action | Read | Skip | Skills |",
        "|------|--------|------|------|--------|",
    ]
    for row in step_rows:
        pos = row["position"] + 1
        slug = _slugify(row["name"] or f"step-{row['id']}")
        stage_path = stages_root / f"{pos:02d}_{slug}" / _STAGE_CONTEXT
        read, skip, skills = _stage_read_skip_skills(row["action_type"], row["name"])
        if stage_path.is_file():
            text = stage_path.read_text(encoding="utf-8", errors="replace")
            for row_line in text.splitlines():
                if row_line.startswith("| ") and "Read" not in row_line and "---" not in row_line:
                    parts = [p.strip() for p in row_line.strip("|").split("|")]
                    if len(parts) >= 3:
                        read, skip, skills = parts[0], parts[1], parts[2]
                    break
        lines.append(
            f"| {row['name']} | {row['action_type']} | {read} | {skip} | {skills} |"
        )
    return "\n".join(lines) + "\n"
