"""Composio Connect (Tool Router) MCP harness for Cursor and Codex."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from distr.core.harness_bootstrap import (
    detected_harnesses,
    install_skills_to_harnesses,
    projection_paths,
    write_projection_skill,
)
from distr.core.plugins import project_root

PROJECT_ROOT = project_root()
LOCAL_SKILLS = PROJECT_ROOT / "skills"
STATE_VERSION = 2

COMPOSIO_CONNECT_URL = "https://connect.composio.dev/mcp"


def _state_path(home: Path) -> Path:
    return home / ".decisions" / "composio-pack-state.json"


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def composio_mcp_recommendations() -> dict[str, Any]:
    """Catalog entries consumed by distr.core.mcp_harness."""
    return {
        "composio_connect": {
            "description": (
                "Composio Connect (Tool Router) — dynamic MCP access to 1000+ apps "
                "(Gmail, Slack, GitHub, Notion, Jira, Linear, …). Replaces deprecated Rube."
            ),
            "docs": "https://docs.composio.dev/docs/composio-connect",
            "auto_merge": True,
            "cursor_name": "composio",
            "skill": "decisions-composio",
            "mcp": {"url": COMPOSIO_CONNECT_URL},
            "codex_mcp": {"url": COMPOSIO_CONNECT_URL},
            "api_key_env": ["COMPOSIO_API_KEY", "COMPOSIO_KEY"],
            "api_key_settings_field": "rube_token",
            "api_key_header": "x-api-key",
            "setup": "Settings → API Keys → Composio; OAuth per app via COMPOSIO_MANAGE_CONNECTIONS",
            "workflow": [
                "COMPOSIO_SEARCH_TOOLS",
                "COMPOSIO_MANAGE_CONNECTIONS (if needed)",
                "COMPOSIO_CREATE_PLAN (medium/hard tasks)",
                "COMPOSIO_MULTI_EXECUTE_TOOL",
            ],
            "deprecated_replaced": "Rube (@composio/rube-mcp, rube.app) — removed from market",
        },
    }


def merge_composio_pre_chain(skill_ids: list[str], *, project_folder: str = "") -> list[str]:
    blob = " ".join(skill_ids).lower()
    tokens = (
        "gmail",
        "slack",
        "notion",
        "linear",
        "jira",
        "trello",
        "asana",
        "hubspot",
        "salesforce",
        "composio",
        "oauth app",
        "send email",
        "post to slack",
    )
    if not any(t in blob for t in tokens):
        return list(skill_ids)
    baseline = ["decisions-composio"]
    merged: list[str] = []
    seen: set[str] = set()
    for skill_id in [*baseline, *skill_ids]:
        key = str(skill_id or "").strip()
        if not key or key in seen:
            continue
        seen.add(key)
        merged.append(key)
    return merged


def _projection_text(*, harness: str) -> str:
    return f"""---
name: decisions-composio-harness
description: Composio Connect MCP (Tool Router) for authenticated SaaS actions in {harness}.
---

# DecisionsAI Composio Harness

**Rube is deprecated** — use Composio Connect only.

| Server | URL |
|--------|-----|
| **composio** | `{COMPOSIO_CONNECT_URL}` |

Skill: **decisions-composio** — start with `COMPOSIO_SEARCH_TOOLS`.

API key: **Settings → API Keys → Composio** (encrypted in DB).

Not for: public web scraping (agent-reach), library docs (context7), YouTube (decisions-yt-dlp).
"""


def ensure_composio_pack_setup(
    *,
    home: Path | None = None,
    run_full: bool = False,
) -> dict[str, Any]:
    _ = run_full
    base_home = Path(home).expanduser() if home is not None else Path.home()
    detected = detected_harnesses()
    skill_id = "decisions-composio"
    sources = {skill_id: LOCAL_SKILLS / skill_id} if (LOCAL_SKILLS / skill_id).is_dir() else {}

    written = install_skills_to_harnesses(
        home=base_home,
        detected=detected,
        skill_sources=sources,
        also_commands=True,
    )

    for harness, path in projection_paths(base_home, detected, "decisions-composio-harness").items():
        if write_projection_skill(path, _projection_text(harness=harness)):
            written.append(str(path))

    payload = {
        "state_version": STATE_VERSION,
        "status": "configured",
        "detected": detected,
        "written": written,
        "mcp_entries": list(composio_mcp_recommendations().keys()),
    }
    _write_json(_state_path(base_home), payload)
    return payload


def ensure_composio_pack_setup_quiet() -> None:
    if (os.environ.get("DECISIONSAI_SKIP_COMPOSIO_PACK_SETUP") or "").strip() == "1":
        return
    try:
        ensure_composio_pack_setup(run_full=False)
    except Exception:
        pass
