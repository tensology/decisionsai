"""yt-dlp harness bootstrap — Decisions venv CLI + skill projection."""

from __future__ import annotations

import hashlib
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
from distr.core.plugins import project_root, yt_dlp_reference_dir
from distr.core.yt_dlp_support import ensure_ytdlp_package, is_ytdlp_available, ytdlp_version

PROJECT_ROOT = project_root()
LOCAL_SKILLS = PROJECT_ROOT / "skills"
STATE_VERSION = 1


def _state_path(home: Path) -> Path:
    return home / ".decisions" / "yt-dlp-pack-state.json"


def _registry_path(home: Path) -> Path:
    return home / ".decisions" / "harness" / "yt-dlp-registry.json"


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def merge_ytdlp_pre_chain(skill_ids: list[str], *, project_folder: str = "") -> list[str]:
    blob = " ".join(skill_ids).lower()
    tokens = ("youtube", "yt-dlp", "ytdlp", "video", "subtitle", "transcript", "remotion", "videodb")
    if not any(t in blob for t in tokens):
        return list(skill_ids)
    baseline = ["decisions-yt-dlp"]
    merged: list[str] = []
    seen: set[str] = set()
    for skill_id in [*baseline, *skill_ids]:
        key = str(skill_id or "").strip()
        if not key or key in seen:
            continue
        seen.add(key)
        merged.append(key)
    return merged


def _projection_text(*, harness: str, registry_path: Path) -> str:
    ref = yt_dlp_reference_dir()
    return f"""---
name: decisions-yt-dlp-harness
description: yt-dlp video/audio download and YouTube metadata/subtitle tool for {harness} workflows.
---

# DecisionsAI yt-dlp Harness

[yt-dlp](https://github.com/yt-dlp/yt-dlp) is installed in the Decisions Python venv and exposed to workflows.

- **Skill:** `decisions-yt-dlp`
- **Reference clone:** `{ref}`
- **Registry:** `{registry_path}`

## Workflow step type

Use action `ytdlp` with config:

```json
{{"mode": "metadata", "url": "https://www.youtube.com/watch?v=..."}}
{{"mode": "subtitles", "url": "...", "sub_lang": "en"}}
{{"mode": "search", "query": "topic", "limit": 5}}
```

## CLI (agents)

```bash
yt-dlp --dump-single-json --no-download URL
yt-dlp --write-sub --write-auto-sub --skip-download -o /tmp/%(id)s URL
```

**YouTube only** for yt-dlp in production — use bili-cli for Bilibili (Agent Reach). Pair with `video-editing`, `agent-reach`, `last30days`.
"""


def ensure_yt_dlp_pack_setup(
    *,
    home: Path | None = None,
    run_full: bool = False,
    install_package: bool = True,
) -> dict[str, Any]:
    base_home = Path(home).expanduser() if home is not None else Path.home()
    detected = detected_harnesses()
    skill_path = LOCAL_SKILLS / "decisions-yt-dlp"
    sources: dict[str, Path] = {}
    if skill_path.is_dir():
        sources["decisions-yt-dlp"] = skill_path

    fingerprint = hashlib.sha256(
        json.dumps(
            {
                "skill": skill_path.stat().st_mtime if skill_path.is_dir() else 0,
                "detected": detected,
                "reference": yt_dlp_reference_dir().exists(),
            },
            sort_keys=True,
            default=str,
        ).encode()
    ).hexdigest()

    pkg = ensure_ytdlp_package() if (install_package and run_full) else {"installed": is_ytdlp_available()}

    rows = [{"id": "decisions-yt-dlp", "path": str(skill_path), "source": "local"}]
    registry = _registry_path(base_home)
    _write_json(registry, rows)

    written = install_skills_to_harnesses(
        home=base_home,
        detected=detected,
        skill_sources=sources,
        also_commands=True,
    )

    for harness, path in projection_paths(base_home, detected, "decisions-yt-dlp-harness").items():
        if write_projection_skill(path, _projection_text(harness=harness, registry_path=registry)):
            written.append(str(path))

    payload = {
        "state_version": STATE_VERSION,
        "status": "configured",
        "detected": detected,
        "fingerprint": fingerprint,
        "written": written,
        "package": pkg,
        "version": ytdlp_version(),
        "reference_path": str(yt_dlp_reference_dir()),
        "registry_path": str(registry),
    }
    _write_json(_state_path(base_home), payload)
    return payload


def ensure_yt_dlp_pack_setup_quiet() -> None:
    if (os.environ.get("DECISIONSAI_SKIP_YT_DLP_PACK_SETUP") or "").strip() == "1":
        return
    try:
        ensure_yt_dlp_pack_setup(run_full=False)
    except Exception:
        pass
