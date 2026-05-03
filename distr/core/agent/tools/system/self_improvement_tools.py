"""
Self-improvement tools (R11) — queue MCP server or bundled skill installs for approval.

Mutations run only after the user approves the initiative draft (``execute_payload``).
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Literal, Optional, Type

from langchain_core.tools import BaseTool
from pydantic import BaseModel, Field

from distr.core.initiative.draft_queue import DraftEntry, DraftQueue
from distr.core.initiative.draft_execute import (
    validate_skill_install_queue,
    validated_mcp_server_for_install,
)
from distr.core.initiative.tiers import PermissionTier

logger = logging.getLogger(__name__)


def _queue_install_draft(
    *,
    description: str,
    draft_body: str,
    execute_payload: dict[str, Any],
) -> str:
    """Append a draft that runs ``execute_payload`` on approve."""
    q = DraftQueue()
    now = datetime.now(tz=timezone.utc)
    entry = DraftEntry(
        id=str(uuid.uuid4()),
        action_type="file_change",
        description=description,
        draft=draft_body,
        reason="Approval required (APPROVE) before installing MCP server or bundled skill",
        created_at=now.isoformat(),
        expires_at=(now + timedelta(hours=48)).isoformat(),
        permission_tier=int(PermissionTier.APPROVE),
        execute_payload=execute_payload,
    )
    q.add(entry)
    logger.info("Self-improvement draft queued: %s — %s", entry.id, description)
    return entry.id


class InstallMCPServerInput(BaseModel):
    name: str = Field(description="Unique MCP server name (shown in Settings).")
    transport: Literal["stdio", "sse"] = Field(default="stdio")
    command: list[str] = Field(
        default_factory=list,
        description="For stdio transport: executable and arguments, e.g. ['npx','-y','@scope/server'].",
    )
    url: str = Field(default="", description="For sse transport: server URL.")
    env: dict[str, str] = Field(
        default_factory=dict,
        description="Optional env vars for stdio (string values only).",
    )
    headers: dict[str, str] = Field(
        default_factory=dict,
        description="Optional HTTP headers for sse transport.",
    )


class InstallMCPServerTool(BaseTool):
    """Queue adding one MCP server to ``mcp_config.json`` after approval."""

    name: str = "install_mcp_server"
    description: str = """Propose adding an MCP server to DecisionsAI configuration.

Does not write disk immediately — creates a **pending initiative action** (APPROVE tier). The user approves in Settings → Initiative or via voice approve flow; then the server is merged into mcp_config.json (duplicate names rejected).

Use when the user asks to add, install, or enable an MCP server. Gather name, transport (stdio vs sse), and either command[] or url."""
    args_schema: Type[BaseModel] = InstallMCPServerInput

    def _run(
        self,
        name: str = "",
        transport: Literal["stdio", "sse"] = "stdio",
        command: Optional[list[str]] = None,
        url: str = "",
        env: Optional[dict[str, str]] = None,
        headers: Optional[dict[str, str]] = None,
        **kwargs: Any,
    ) -> str:
        command = command or []
        env = env or {}
        headers = headers or {}
        raw: dict[str, Any] = {
            "name": name.strip(),
            "transport": transport,
            "command": command,
            "url": url.strip(),
            "env": env,
            "headers": headers,
            "enabled": True,
        }
        try:
            norm = validated_mcp_server_for_install(raw)
        except ValueError as e:
            return str(e)
        except Exception as e:
            logger.exception("install_mcp_server validation failed")
            return f"Could not validate MCP server: {e}"

        payload = {"kind": "mcp_install", "server": norm}
        preview = json.dumps(norm, indent=2)
        draft_id = _queue_install_draft(
            description=f"Install MCP server {norm['name']!r}",
            draft_body=f"Proposed MCP server entry:\n```json\n{preview}\n```",
            execute_payload=payload,
        )
        return (
            f"Queued pending approval (draft id `{draft_id}`). "
            f"After you approve in Initiative settings or voice, `{norm['name']}` is added to MCP config."
        )


class InstallSkillInput(BaseModel):
    repo_url: str = Field(description="HTTPS Git URL to clone (public or tokenless).")
    folder_name: str = Field(
        default="",
        description="Destination folder under bundled skills/; omit to derive from the repo name.",
    )


class InstallSkillTool(BaseTool):
    """Queue cloning a skill repo into ``DecisionsAI/skills/<folder>/`` after approval."""

    name: str = "install_skill"
    description: str = """Propose cloning a skill Git repository into DecisionsAI bundled skills.

Does not clone until the user **approves** the pending initiative action. Only ``https://`` URLs. Requires ``git`` on PATH at approval time. The clone must contain SKILL.md at repo root. After success, the skill is appended to bundled ``skills/skills_registry.json`` (duplicate ids skipped; corrupt registry file is left unchanged).

Use when the user asks to install, add, or import a skill from a Git URL."""
    args_schema: Type[BaseModel] = InstallSkillInput

    def _run(self, repo_url: str = "", folder_name: str = "", **kwargs: Any) -> str:
        try:
            url, safe_folder = validate_skill_install_queue(repo_url, folder_name)
        except ValueError as e:
            return str(e)
        except Exception as e:
            logger.exception("install_skill validation failed")
            return f"Could not validate skill install: {e}"

        payload = {"kind": "skill_install", "repo_url": url, "folder_name": safe_folder}
        draft_id = _queue_install_draft(
            description=f"Clone skill into skills/{safe_folder}",
            draft_body=f"Repository: {url}\nDestination: skills/{safe_folder}/",
            execute_payload=payload,
        )
        return (
            f"Queued pending approval (draft id `{draft_id}`). "
            f"After approve, git clones into bundled skills folder `{safe_folder}`."
        )
