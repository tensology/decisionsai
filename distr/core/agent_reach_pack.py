"""DecisionsAI Agent Reach harness pack — internet research router for all IDEs/CLIs."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Any

from distr.core.harness_bootstrap import (
    detected_harnesses,
    install_skills_to_harnesses,
    projection_paths,
    write_projection_skill,
)
from distr.core.plugins import (
    AGENT_REACH_PACK_DIR,
    agent_reach_reference_dir,
    agent_reach_skill_dir,
    project_root,
)

PROJECT_ROOT = project_root()
LOCAL_SKILLS = PROJECT_ROOT / "skills"
STATE_VERSION = 1


def _home(path: Path | None = None) -> Path:
    return Path(path).expanduser() if path is not None else Path.home()


def _vendor_metadata() -> dict[str, Any]:
    metadata_path = AGENT_REACH_PACK_DIR / ".decisions-vendor.json"
    if not metadata_path.is_file():
        return {}
    try:
        payload = json.loads(metadata_path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


def _vendor_ready() -> bool:
    return (agent_reach_skill_dir() / "SKILL.md").is_file()


def _fingerprint(detected: dict[str, bool]) -> str:
    meta = _vendor_metadata()
    payload = {
        "state_version": STATE_VERSION,
        "commit": meta.get("commit"),
        "detected": detected,
        "skill_mtime": (agent_reach_skill_dir() / "SKILL.md").stat().st_mtime
        if _vendor_ready()
        else 0,
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _state_path(home: Path) -> Path:
    return home / ".decisions" / "agent-reach-pack-state.json"


def _registry_cache_path(home: Path) -> Path:
    return home / ".decisions" / "harness" / "agent-reach-skills-registry.json"


def _projection_text(*, harness: str, registry_path: Path) -> str:
    ref = agent_reach_reference_dir()
    return f"""---
name: decisions-agent-reach-harness
description: Agent Reach internet research router for {harness} — Twitter, Reddit, YouTube, GitHub, web, RSS, Exa search, and more.
---

# DecisionsAI Agent Reach Harness

Agent Reach is a **capability layer**: it picks, installs, and health-checks upstream CLIs (twitter-cli,
yt-dlp, gh, Jina Reader, bili-cli, mcporter/Exa, OpenCLI, etc.) — agents call those tools directly.

- **Upstream skill:** `agent-reach` (full routing table + reference docs)
- **Decisions wrapper:** `decisions-agent-reach` (when to use with tickets/workflows)
- **Reference clone:** `{ref}`
- **Registry:** `{registry_path}`

## First commands

```bash
agent-reach doctor --json    # which platforms work right now
agent-reach check-update       # after large research tasks
```

## Install / update (agent-driven)

```
Install Agent Reach: https://raw.githubusercontent.com/Panniantong/agent-reach/main/docs/install.md
Update Agent Reach: https://raw.githubusercontent.com/Panniantong/agent-reach/main/docs/update.md
```

Decisions `bin/setup.py` can pip-install from the local reference clone on full setup.

## Decisions rules

- Use for **fetching** external content (research, links, social posts, video subtitles) — not for writing reports.
- Attach sources (URLs, command output paths under `/tmp/`) in workflow result packets.
- Cookies stay in `~/.agent-reach/` — never commit them to the project repo.
- Pair with `decisions-ui-ideation` when research informs UI; pair with `content-engine` when research feeds articles.
"""


def _ensure_cli_installed(*, enabled: bool) -> dict[str, Any]:
    if not enabled:
        return {"installed": False, "reason": "skipped"}
    if shutil.which("agent-reach"):
        return {"installed": True, "method": "existing"}
    ref = agent_reach_reference_dir()
    if not (ref / "pyproject.toml").is_file():
        return {"installed": False, "reason": "reference clone missing"}
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pip", "install", "-e", str(ref)],
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
        )
        ok = result.returncode == 0 and bool(shutil.which("agent-reach"))
        return {
            "installed": ok,
            "method": "pip-editable-reference",
            "returncode": result.returncode,
            "stderr": (result.stderr or "")[:400],
        }
    except Exception as exc:
        return {"installed": False, "reason": str(exc)}


def _run_doctor_quiet() -> dict[str, Any]:
    if not shutil.which("agent-reach"):
        return {"ok": False, "reason": "agent-reach not on PATH"}
    try:
        result = subprocess.run(
            ["agent-reach", "doctor", "--json"],
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        if result.returncode != 0:
            return {"ok": False, "returncode": result.returncode, "stderr": (result.stderr or "")[:300]}
        try:
            return {"ok": True, "report": json.loads(result.stdout)}
        except Exception:
            return {"ok": True, "raw": (result.stdout or "")[:500]}
    except Exception as exc:
        return {"ok": False, "reason": str(exc)}


def merge_agent_reach_pre_chain(skill_ids: list[str], *, project_folder: str = "") -> list[str]:
    """Prepend research skills when the chain or path suggests external lookup."""
    research_tokens = (
        "research",
        "twitter",
        "reddit",
        "youtube",
        "bilibili",
        "github",
        "competitor",
        "deep dive",
        "look up",
        "rss",
        "podcast",
        "xiaohongshu",
        "linkedin",
    )
    blob = " ".join(skill_ids).lower()
    needs = any(token in blob for token in research_tokens)
    if not needs:
        return list(skill_ids)
    baseline = ["decisions-agent-reach", "agent-reach"]
    merged: list[str] = []
    seen: set[str] = set()
    for skill_id in [*baseline, *skill_ids]:
        key = str(skill_id or "").strip()
        if not key or key in seen:
            continue
        seen.add(key)
        merged.append(key)
    return merged


def ensure_agent_reach_pack_setup(
    *,
    home: Path | None = None,
    run_full: bool = False,
    install_cli: bool = True,
    run_doctor: bool = False,
) -> dict[str, Any]:
    base_home = _home(home)
    detected = detected_harnesses()
    fingerprint = _fingerprint(detected)
    registry_path = _registry_cache_path(base_home)

    sources: dict[str, Path] = {}
    upstream = agent_reach_skill_dir()
    if upstream.is_dir():
        sources["agent-reach"] = upstream
    local = LOCAL_SKILLS / "decisions-agent-reach"
    if local.is_dir():
        sources["decisions-agent-reach"] = local

    skip_heavy = False
    state_path = _state_path(base_home)
    if not run_full and state_path.is_file():
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
            if state.get("fingerprint") == fingerprint and registry_path.is_file():
                skip_heavy = True
        except Exception:
            pass

    if not skip_heavy:
        rows = [
            {
                "id": skill_id,
                "path": str(path),
                "source": "agent_reach_vendor" if skill_id == "agent-reach" else "local",
            }
            for skill_id, path in sources.items()
        ]
        _write_json(registry_path, rows)

    written = install_skills_to_harnesses(
        home=base_home,
        detected=detected,
        skill_sources=sources,
        also_commands=True,
    )

    for harness, path in projection_paths(base_home, detected, "decisions-agent-reach-harness").items():
        if write_projection_skill(path, _projection_text(harness=harness, registry_path=registry_path)):
            written.append(str(path))

    cli_install = _ensure_cli_installed(enabled=install_cli and run_full)
    doctor = _run_doctor_quiet() if run_doctor or (run_full and cli_install.get("installed")) else {}

    status = "current" if skip_heavy and not written else "configured"
    payload = {
        "state_version": STATE_VERSION,
        "status": status,
        "vendor_ready": _vendor_ready(),
        "vendor": _vendor_metadata(),
        "detected": detected,
        "fingerprint": fingerprint,
        "registry_path": str(registry_path),
        "written": written,
        "cli": cli_install,
        "doctor": doctor,
        "reference_path": str(agent_reach_reference_dir()),
    }
    _write_json(state_path, payload)
    return payload


def ensure_agent_reach_pack_setup_quiet() -> None:
    if (os.environ.get("DECISIONSAI_SKIP_AGENT_REACH_PACK_SETUP") or "").strip() == "1":
        return
    try:
        ensure_agent_reach_pack_setup(run_full=False)
    except Exception:
        pass
