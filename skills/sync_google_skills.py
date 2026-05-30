#!/usr/bin/env python3
"""
Sync google/skills from COMPETITION/google-skills into DecisionsAI/skills and registry.

Usage (from DecisionsAI/):
  python skills/sync_google_skills.py
  python skills/sync_google_skills.py --source ../COMPETITION/google-skills/skills/cloud
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SKILLS_DIR = PROJECT_ROOT / "skills"
REGISTRY_FILE = SKILLS_DIR / "skills_registry.json"
DEFAULT_SOURCE = PROJECT_ROOT.parent / "COMPETITION" / "google-skills" / "skills" / "cloud"


def _parse_frontmatter(skill_md: Path) -> tuple[str, str]:
    name = skill_md.parent.name
    description = ""
    if not skill_md.is_file():
        return name, description
    text = skill_md.read_text(encoding="utf-8")
    if not text.startswith("---"):
        return name, description
    end = text.find("---", 3)
    if end <= 0:
        return name, description
    for line in text[3:end].split("\n"):
        line = line.strip()
        if line.lower().startswith("name:"):
            v = line.split(":", 1)[1].strip().strip('"').strip("'")
            if v:
                name = v
        elif line.lower().startswith("description:"):
            v = line.split(":", 1)[1].strip().strip('"').strip("'")
            if v:
                description = v
    return name, description


def _load_registry() -> list[dict]:
    if not REGISTRY_FILE.is_file():
        return []
    raw = json.loads(REGISTRY_FILE.read_text(encoding="utf-8"))
    return raw if isinstance(raw, list) else []


def _write_registry(entries: list[dict]) -> None:
    entries.sort(key=lambda r: str(r.get("id") or "").lower())
    REGISTRY_FILE.write_text(json.dumps(entries, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def sync_google_skills(source_dir: Path, *, copy_files: bool = True) -> int:
    if not source_dir.is_dir():
        raise FileNotFoundError(f"Source not found: {source_dir}")

    if copy_files:
        SKILLS_DIR.mkdir(parents=True, exist_ok=True)
        for child in sorted(source_dir.iterdir()):
            if not child.is_dir():
                continue
            dest = SKILLS_DIR / child.name
            if dest.exists():
                shutil.rmtree(dest)
            shutil.copytree(child, dest)

    entries = _load_registry()
    by_id = {str(r.get("id") or "").strip().lower(): r for r in entries if isinstance(r, dict)}
    added = 0

    for child in sorted(source_dir.iterdir()):
        if not child.is_dir():
            continue
        skill_md = child / "SKILL.md"
        if not skill_md.is_file():
            skill_md = child / "skill.md"
        if not skill_md.is_file():
            continue
        folder_id = child.name
        name, description = _parse_frontmatter(skill_md)
        key = folder_id.lower()
        row = {
            "id": folder_id,
            "name": name,
            "description": description or f"Google Agent Skill ({folder_id})",
            "path": folder_id,
            "source": "google/skills",
            "tags": ["google-cloud"],
        }
        if key in by_id:
            by_id[key].update(row)
        else:
            by_id[key] = row
            added += 1

    _write_registry(list(by_id.values()))
    return added


def main() -> int:
    parser = argparse.ArgumentParser(description="Sync google/skills into bundled skills registry")
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE, help="Path to google/skills/cloud")
    parser.add_argument("--no-copy", action="store_true", help="Only update registry (files already copied)")
    args = parser.parse_args()
    added = sync_google_skills(args.source, copy_files=not args.no_copy)
    print(f"Registry updated. New google skills added: {added}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
