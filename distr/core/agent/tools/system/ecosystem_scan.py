"""Ecosystem scan — board/project/workflow health for orchestrator initiative."""

from __future__ import annotations

import json
import logging
from typing import Any, Optional, Type

from langchain.tools import BaseTool
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class EcosystemScanInput(BaseModel):
    suggest_only: bool = Field(
        default=True,
        description="If true, return suggestions only (no dispatch).",
    )


class EcosystemScanTool(BaseTool):
    name: str = "ecosystem_scan"
    description: str = (
        "Scan boards, projects, and workflows for ecosystem health: empty lanes, "
        "unscoped tickets, missing project folders, and name→ID index. "
        "Use when user asks what needs attention across boards or projects."
    )
    args_schema: Type[BaseModel] = EcosystemScanInput

    def _run(self, suggest_only: bool = True, **kwargs) -> str:
        try:
            from distr.core.developer_context import DeveloperContextAssembler

            ctx = DeveloperContextAssembler().build()
            eco = ctx.ecosystem or {}
            warnings = list(ctx.warnings or [])
            lines = ["Ecosystem scan:"]
            if eco.get("board_health"):
                lines.append("\nBoard health:")
                for item in eco["board_health"][:15]:
                    lines.append(f"  - {item}")
            if eco.get("unscoped_tickets"):
                lines.append("\nUnscoped tickets (no project link):")
                for item in eco["unscoped_tickets"][:15]:
                    lines.append(f"  - {item}")
            if eco.get("projects_missing_folder"):
                lines.append("\nProjects missing folder (harness blocked):")
                for item in eco["projects_missing_folder"][:10]:
                    lines.append(f"  - {item}")
            name_index = eco.get("name_index") or {}
            if name_index.get("workflows"):
                lines.append("\nWorkflow name index:")
                for name, wid in list(name_index["workflows"].items())[:15]:
                    lines.append(f"  - {name} → #{wid}")
            if warnings:
                lines.append("\nWarnings: " + "; ".join(warnings[:6]))
            lines.append("\nSuggested actions:")
            if eco.get("unscoped_tickets"):
                lines.append("  - Link tickets to projects or set board default_project_id")
            if eco.get("projects_missing_folder"):
                lines.append("  - Set folder_location on projects before send_to_project_cli")
            lines.append("  - Use run_workflow with workflow_name for dogfood loops")
            if suggest_only:
                lines.append("\n(suggest_only=true — no auto-dispatch)")
            return "\n".join(lines)
        except Exception as e:
            logger.error("ecosystem_scan failed: %s", e, exc_info=True)
            return f"Error: {e}"

    async def _arun(self, **kwargs) -> str:
        return self._run(**kwargs)
