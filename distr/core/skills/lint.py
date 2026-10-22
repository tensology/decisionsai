#!/usr/bin/env python3
"""Harness-agnostic skills lint ("skills doctor").

Checks the canonical shared skill dir (~/.agents/skills) and the per-harness skill
dirs that should only hold symlinks to it. Stdlib only (PyYAML used if present), so
the same file runs inside Decisions (distr.core.skills.lint) and standalone as
~/.agents/bin/skills-lint.

Usage: skills-lint [--home DIR] [--json] [--verbose] [--strict]
Exit code: 0 clean / warnings only, 1 when errors (or any finding with --strict).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import Counter
from pathlib import Path

ERROR, WARN = "error", "warn"
NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
CHECKS = {
    # id: (severity, pattern-or-None, help)
    "no-frontmatter": (ERROR, None, "SKILL.md has no (terminated) --- frontmatter"),
    "invalid-yaml": (ERROR, None, "frontmatter is not valid YAML"),
    "missing-name": (WARN, None, "frontmatter has no name (folder name is used)"),
    "missing-description": (ERROR, None, "frontmatter has no description; most harnesses skip the skill"),
    "invalid-name": (ERROR, None, "name must be lowercase a-z, 0-9, hyphens, <=64 chars"),
    "name-not-folder": (WARN, None, "name differs from folder name"),
    "desc-too-long": (ERROR, None, "description longer than 1024 characters"),
    "nested-skill": (WARN, None, "SKILL.md files nested below the skill root (often not loaded)"),
    "claude-only-tools": (WARN, re.compile(r"\b(TodoWrite|AskUserQuestion|subagent_type|ExitPlanMode|NotebookEdit|Task tool)\b"), "Claude Code-only tool names"),
    "mcp-tool-names": (WARN, re.compile(r"\bmcp__[A-Za-z0-9_-]+"), "hardcoded mcp__server__tool names"),
    "harness-home-paths": (WARN, re.compile(r"(~|\$HOME|/Users/[^/\s]+)/\.(codex|claude|cursor)/"), "hardcoded ~/.codex, ~/.claude or ~/.cursor paths"),
    "arguments-placeholder": (WARN, re.compile(r"\$ARGUMENTS\b"), "$ARGUMENTS placeholder (slash-command syntax)"),
    "too-long": (WARN, None, "SKILL.md longer than 500 lines"),
    "drift-copy": (ERROR, None, "harness entry is a real copy instead of a link to ~/.agents/skills"),
    "drift-missing-link": (WARN, None, "harness dir lacks a link for a shared skill"),
    "drift-foreign-link": (WARN, None, "harness link points somewhere other than the shared skill"),
    "broken-link": (ERROR, None, "dangling symlink"),
    "double-load": (WARN, None, "native ~/.agents reader also has its own entry with the same name"),
}
# Harness dirs that should mirror ~/.agents/skills with per-skill symlinks.
LINK_HARNESSES = {"claude": ".claude/skills", "cursor": ".cursor/skills", "codex": ".codex/skills"}
# Harnesses that read ~/.agents/skills natively; own entries double-load.
NATIVE_READERS = {"pi": ".pi/agent/skills", "kilo": ".kilocode/skills", "kilo2": ".kilo/skills"}
# Harness-owned names: Decisions per-harness projections, gstack (owns ~/gstack), dot dirs.
OWNED = re.compile(r"^(decisions-.+-harness|gstack|gstack-.+|\..+)$")


def _frontmatter(text: str):
    if not text.startswith("---"):
        return None, "no-frontmatter"
    end = text.find("\n---", 3)
    if end == -1:
        return None, "no-frontmatter"
    block = text[3:end].lstrip("\r\n")
    try:
        import yaml  # type: ignore

        try:
            data = yaml.safe_load(block)
        except Exception:
            return None, "invalid-yaml"
        return (data if isinstance(data, dict) else {}), None
    except ImportError:
        data = {}
        for m in re.finditer(r"^([A-Za-z0-9_-]+):[ \t]*(.*)$", block, re.M):
            data[m.group(1)] = m.group(2).strip().strip("\"'")
        # block scalars: take indented continuation
        for key in ("description",):
            m = re.search(rf"^{key}:[ \t]*([|>][+-]?)?[ \t]*\n((?:[ \t]+.*\n?)+)", block, re.M)
            if m and (m.group(1) or not data.get(key)):
                data[key] = " ".join(l.strip() for l in m.group(2).splitlines())
        return data, None


def lint_skill(skill_dir: Path) -> list[dict]:
    out = []
    f = skill_dir / "SKILL.md"
    if not f.is_file():
        f = skill_dir / "skill.md"
    if not f.is_file():
        return out
    text = f.read_text(encoding="utf-8", errors="replace")
    name_dir = skill_dir.name

    def add(check, detail=""):
        out.append({"skill": name_dir, "check": check, "severity": CHECKS[check][0], "detail": detail})

    data, err = _frontmatter(text)
    if err:
        add(err)
        data = {}
    if not err:
        name = data.get("name")
        desc = data.get("description")
        if not name:
            add("missing-name")
        else:
            name = str(name)
            if not NAME_RE.match(name) or len(name) > 64:
                add("invalid-name", name[:80])
            elif name != name_dir:
                add("name-not-folder", name)
        if not desc or not str(desc).strip():
            add("missing-description")
        elif len(" ".join(str(desc).split())) > 1024:
            add("desc-too-long", str(len(" ".join(str(desc).split()))))
    for check, (_sev, pat, _h) in CHECKS.items():
        if pat is not None:
            hits = sorted(set(m.group(0) for m in pat.finditer(text)))
            if hits:
                add(check, ", ".join(hits[:4]))
    n = text.count("\n")
    if n > 500:
        add("too-long", str(n))
    nested = [p for p in skill_dir.rglob("SKILL.md") if p.parent != skill_dir and "node_modules" not in p.parts]
    if nested:
        add("nested-skill", f"{len(nested)} below root")
    return out


def lint_home(home: Path | None = None) -> dict:
    home = Path(home or Path.home()).expanduser()
    shared = home / ".agents" / "skills"
    findings: list[dict] = []
    canon = {}
    if shared.is_dir():
        for e in sorted(shared.iterdir()):
            if e.name.startswith("."):
                continue
            if e.is_symlink() and not e.exists():
                findings.append({"skill": e.name, "check": "broken-link", "severity": ERROR, "detail": str(e)})
                continue
            if e.is_dir():
                canon[e.name] = e.resolve()
                findings.extend(lint_skill(e))
    for h, rel in LINK_HARNESSES.items():
        base = home / rel
        if not base.is_dir():
            continue
        names = set()
        for e in sorted(base.iterdir()):
            n = e.name
            names.add(n)
            if OWNED.match(n):
                continue
            if e.is_symlink():
                if not e.exists():
                    findings.append({"skill": n, "check": "broken-link", "severity": ERROR, "detail": f"{h}: {os.readlink(e)}"})
                elif n in canon and e.resolve() != canon[n]:
                    findings.append({"skill": n, "check": "drift-foreign-link", "severity": WARN, "detail": f"{h} -> {os.readlink(e)}"})
            elif e.is_dir() and n in canon:
                findings.append({"skill": n, "check": "drift-copy", "severity": ERROR, "detail": h})
        for n in sorted(set(canon) - names):
            findings.append({"skill": n, "check": "drift-missing-link", "severity": WARN, "detail": h})
    for h, rel in NATIVE_READERS.items():
        base = home / rel
        if base.is_dir():
            for e in sorted(base.iterdir()):
                if e.name in canon and not OWNED.match(e.name):
                    findings.append({"skill": e.name, "check": "double-load", "severity": WARN, "detail": h})
    counts = Counter(f["check"] for f in findings)
    sev = Counter(f["severity"] for f in findings)
    return {
        "home": str(home),
        "shared_dir": str(shared),
        "skills": len(canon),
        "errors": sev.get(ERROR, 0),
        "warnings": sev.get(WARN, 0),
        "counts": dict(sorted(counts.items())),
        "findings": findings,
    }


def summary(home: Path | None = None) -> dict:
    """Compact form for doctor reports (no per-finding list)."""
    r = lint_home(home)
    r.pop("findings", None)
    return r


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="skills-lint", description=__doc__.splitlines()[0])
    ap.add_argument("--home", default=None)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--verbose", "-v", action="store_true")
    ap.add_argument("--strict", action="store_true")
    a = ap.parse_args(argv)
    r = lint_home(Path(a.home) if a.home else None)
    if a.json:
        print(json.dumps(r, indent=1))
    else:
        print(f"skills-lint: {r['skills']} shared skills in {r['shared_dir']} - {r['errors']} errors, {r['warnings']} warnings")
        for check, n in r["counts"].items():
            print(f"  {CHECKS[check][0]:5s} {check:22s} {n:4d}  {CHECKS[check][2]}")
        if a.verbose:
            for f in r["findings"]:
                print(f"  [{f['severity']}] {f['check']}: {f['skill']} {f['detail']}")
    if r["errors"] or (a.strict and r["warnings"]):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
