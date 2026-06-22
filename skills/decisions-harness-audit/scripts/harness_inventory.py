#!/usr/bin/env python3
"""Read-only Decisions harness inventory."""

from __future__ import annotations

import json
from pathlib import Path


def count_files(path: Path, pattern: str) -> int:
    if not path.exists():
        return 0
    return sum(1 for _ in path.rglob(pattern))


def main() -> int:
    root = Path.cwd()
    skills = root / "skills"
    registry = skills / "skills_registry.json"
    payload = {
        "root": str(root),
        "registry_exists": registry.is_file(),
        "registry_rows": 0,
        "skill_folders": count_files(skills, "SKILL.md"),
        "project_surfaces": {
            ".codex/commands": (root / ".codex" / "commands").exists(),
            ".claude/commands": (root / ".claude" / "commands").exists(),
            ".cursor/commands": (root / ".cursor" / "commands").exists(),
            ".gemini/commands": (root / ".gemini" / "commands").exists(),
            ".pi/skills": (root / ".pi" / "skills").exists(),
            ".cline/skills": (root / ".cline" / "skills").exists(),
        },
    }
    if registry.is_file():
        rows = json.loads(registry.read_text(encoding="utf-8"))
        payload["registry_rows"] = len(rows) if isinstance(rows, list) else 0
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
