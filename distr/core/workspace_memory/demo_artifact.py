"""Generate demo / user-guide markdown on workflow run completion."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from .paths import companion_root
from .template import write_text


def _slugify(value: str, *, max_len: int = 40) -> str:
    text = re.sub(r"[^a-zA-Z0-9]+", "-", (value or "").strip().lower()).strip("-")
    return (text[:max_len] or "run")


def write_demo_artifact(
    *,
    workflow_id: int,
    run_id: int,
    ticket_title: str = "",
    handoff_summary: str = "",
    result_packet: dict[str, Any] | None = None,
) -> str:
    """Write pipeline/output/{date}_{slug}_demo.md in workflow companion store."""
    root = companion_root("workflows", workflow_id)
    out_dir = root / "pipeline" / "output"
    out_dir.mkdir(parents=True, exist_ok=True)

    date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    slug = _slugify(ticket_title or f"run-{run_id}")
    path = out_dir / f"{date}_{slug}_demo.md"

    packet = result_packet or {}
    artifacts = packet.get("artifacts") or {}
    screenshots = list(artifacts.get("screenshots") or [])
    ui = packet.get("ui_quality") or {}
    if ui.get("after_screenshot") and ui["after_screenshot"] not in screenshots:
        screenshots.insert(0, ui["after_screenshot"])

    lines = [
        f"# Demo — {ticket_title or f'Run {run_id}'}",
        "",
        f"_Generated from workflow run #{run_id}_",
        "",
        "## What changed",
        "",
        (handoff_summary or "_See memory/handoff.md for session continuity._").strip(),
        "",
        "## How to use it",
        "",
        "1. Open DecisionsAI and select the workflow that ran this ticket.",
        "2. Check **Runs → Executor** for screenshots and step evidence.",
        "3. Use **Work in your IDE** to continue in your harness with updated memory.",
        "",
    ]
    if screenshots:
        lines.append("## Screenshots")
        lines.append("")
        for shot in screenshots[:6]:
            lines.append(f"![evidence]({shot})")
            lines.append("")
    else:
        lines.append("_No screenshots attached to the result packet._")
        lines.append("")

    harness = packet.get("harness_report") or packet.get("iteration_report")
    if harness:
        lines.extend(["## Self-assessment", "", f"```\n{harness}\n```", ""])

    write_text(path, "\n".join(lines))
    return str(path)
