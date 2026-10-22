"""Skill registry with source provenance and dedupe support."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


TARGET_PATHS = {
    "pi": ".pi/skills",
    "cursor": ".cursor/commands",
    "claude_code": ".claude/commands",
    "claude": ".claude/commands",
    "codex": ".codex/commands",
    "gemini": ".gemini/commands",
    "cline": ".cline/skills",
}


@dataclass(frozen=True)
class SkillEntry:
    canonical_id: str
    name: str
    description: str
    path: Path
    source: str
    content_hash: str
    aliases: tuple[str, ...] = ()
    target_surfaces: tuple[str, ...] = ("codex", "cursor", "claude", "pi", "cline")


def _canonical_id(value: str) -> str:
    text = re.sub(r"[^a-z0-9]+", "-", str(value or "").strip().lower())
    return text.strip("-")


_BLOCK_SCALAR = re.compile(r"^[|>][+-]?\d?$")


def _fallback_frontmatter(block: str) -> dict:
    """Minimal YAML-subset parser used only if PyYAML is unavailable or fails.

    Handles ``key: value``, quoted values, block scalars (``|``, ``>``, ``>-`` ...)
    and indented plain continuation lines. Nested mappings are skipped.
    """
    data: dict = {}
    lines = block.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        m = re.match(r"^([A-Za-z0-9_-]+):(?:\s+(.*))?$", line)
        if not m:
            i += 1
            continue
        key, raw = m.group(1), (m.group(2) or "").strip()
        i += 1
        cont: list[str] = []
        while i < len(lines) and (lines[i].startswith((" ", "\t")) or not lines[i].strip()):
            cont.append(lines[i])
            i += 1
        if _BLOCK_SCALAR.match(raw):
            parts = [c.strip() for c in cont]
            if raw.startswith("|"):
                value = "\n".join(parts).strip()
            else:
                value = " ".join(p for p in parts if p).strip()
        elif raw:
            value = " ".join([raw] + [c.strip() for c in cont if c.strip()])
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
        else:
            value = " ".join(c.strip() for c in cont if c.strip() and ":" not in c.strip()[:40]) if cont else ""
            if cont and any(re.match(r"^\s+[A-Za-z0-9_-]+:", c) for c in cont):
                value = ""  # nested mapping; not needed here
        data[key.lower()] = value
    return data


def parse_frontmatter(text: str) -> dict | None:
    """Return the SKILL.md frontmatter mapping, or None when absent/unterminated."""
    if not text.startswith("---"):
        return None
    end = text.find("\n---", 3)
    if end == -1:
        return None
    block = text[3:end].lstrip("\r\n")
    try:
        import yaml  # PyYAML is in requirements

        data = yaml.safe_load(block)
        if isinstance(data, dict):
            return {str(k).lower(): v for k, v in data.items()}
    except Exception:
        pass
    return _fallback_frontmatter(block)


def _frontmatter_fields(text: str, fallback: str) -> tuple[str, str]:
    data = parse_frontmatter(text) or {}
    name = data.get("name")
    name = str(name).strip() if name not in (None, "") else fallback
    description = data.get("description")
    description = " ".join(str(description).split()) if description not in (None, "") else ""
    return name or fallback, description


def _skill_md(skill_dir: Path) -> Path | None:
    for name in ("SKILL.md", "skill.md"):
        path = skill_dir / name
        if path.is_file():
            return path
    return None


def _hash_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class SkillRegistry:
    """Scan local and vendored skills, preferring local definitions on conflicts."""

    def __init__(
        self,
        *,
        local_roots: Iterable[str | Path] = (),
        vendor_roots: Iterable[str | Path] = (),
        competition_roots: Iterable[str | Path] = (),
    ) -> None:
        self.local_roots = [Path(p) for p in local_roots]
        self.vendor_roots = [Path(p) for p in vendor_roots]
        self.competition_roots = [Path(p) for p in competition_roots]
        self.entries: dict[str, SkillEntry] = {}
        self.conflicts: dict[str, list[SkillEntry]] = {}

    def scan(self) -> "SkillRegistry":
        self.entries.clear()
        self.conflicts.clear()
        for source, roots in (
            ("local", self.local_roots),
            ("ecc_vendor", self.vendor_roots),
            ("competition_vendor", self.competition_roots),
        ):
            for root in roots:
                self._scan_root(root, source=source)
        return self

    def _scan_root(self, root: Path, *, source: str) -> None:
        if not root.exists() or not root.is_dir():
            return
        for skill_dir in sorted([p for p in root.iterdir() if p.is_dir()], key=lambda p: p.name.lower()):
            skill_file = _skill_md(skill_dir)
            if not skill_file:
                continue
            text = skill_file.read_text(encoding="utf-8", errors="replace")
            name, description = _frontmatter_fields(text, skill_dir.name)
            canonical_id = _canonical_id(skill_dir.name)
            entry = SkillEntry(
                canonical_id=canonical_id,
                name=name,
                description=description,
                path=skill_dir,
                source=source,
                content_hash=_hash_file(skill_file),
                aliases=tuple(sorted({_canonical_id(name), canonical_id})),
            )
            existing = self.entries.get(canonical_id)
            if existing is None:
                self.entries[canonical_id] = entry
                continue
            if existing.content_hash == entry.content_hash:
                continue
            preferred, duplicate = self._prefer(existing, entry)
            self.entries[canonical_id] = preferred
            self.conflicts.setdefault(canonical_id, []).append(duplicate)

    @staticmethod
    def _prefer(left: SkillEntry, right: SkillEntry) -> tuple[SkillEntry, SkillEntry]:
        order = {"local": 0, "ecc_vendor": 1, "competition_vendor": 2}
        left_rank = order.get(left.source, 99)
        right_rank = order.get(right.source, 99)
        if left_rank <= right_rank:
            return left, right
        return right, left

    def get(self, skill_id: str) -> SkillEntry | None:
        return self.entries.get(_canonical_id(skill_id))

    def target_path(self, entry: SkillEntry, backend_id: str, project_root: str | Path) -> Path:
        backend = str(backend_id or "").strip().lower()
        if backend == "claude_code":
            backend = "claude"
        target = TARGET_PATHS.get(backend, TARGET_PATHS["pi"])
        project_path = Path(project_root).expanduser().resolve()
        if target == TARGET_PATHS["pi"]:
            return project_path / target / entry.canonical_id / "SKILL.md"
        return project_path / target / f"{entry.canonical_id}.md"
