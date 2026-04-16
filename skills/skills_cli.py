#!/usr/bin/env python3
"""
Skills CLI — manage skills from the terminal.

Usage:
  python skills_cli.py list [--tag TAG] [--limit N]
  python skills_cli.py find <query> [--limit N]
  python skills_cli.py read <skill_id>
  python skills_cli.py push <skill_id> [--project PATH] [--target TARGET]

Examples:
  python skills_cli.py list
  python skills_cli.py list --tag auditing
  python skills_cli.py find "docker setup"
  python skills_cli.py read ln-731-docker-generator
  python skills_cli.py push brainstorming --project ~/myapp --target claude
  python skills_cli.py push ln-621-security-auditor --target cursor
"""

import argparse
import json
import os
import shutil
import sys
from pathlib import Path

# Resolve project root (skills_cli.py lives in PROJECT_ROOT/skills/)
PROJECT_ROOT = Path(__file__).resolve().parent.parent
SKILLS_DIR = PROJECT_ROOT / "skills"
REGISTRY_FILE = SKILLS_DIR / "skills_registry.json"

CLI_TARGETS = {
    "pi": ".pi/skills",
    "claude": ".claude/commands",
    "cursor": ".cursor/commands",
    "gemini": ".gemini/commands",
    "codex": ".codex/commands",
}


def load_registry():
    if REGISTRY_FILE.exists():
        return json.loads(REGISTRY_FILE.read_text(encoding="utf-8"))
    return []


def compute_tags(skill):
    tag_rules = {
        "planning": ["scope", "epic", "story", "priorit", "research", "opportunity"],
        "documentation": ["doc", "creator", "writer", "reference", "presentation"],
        "execution": ["execut", "pipeline", "coordinat", "task-creator", "task-executor", "validator"],
        "quality": ["quality", "test", "regression", "checker", "planner"],
        "auditing": ["auditor", "audit", "security", "dead-code", "pattern", "dependency"],
        "bootstrap": ["bootstrap", "generat", "setup", "docker", "cicd", "linter", "healthcheck"],
        "performance": ["performance", "optim", "upgrad", "moderniz", "bundle"],
        "community": ["community", "github", "triager", "announcer", "debater", "responder"],
        "creative": ["art", "design", "canvas", "pptx", "docx", "xlsx", "pdf", "brand", "theme", "web-artifacts"],
        "dev-tools": ["mcp", "claude-api", "skill-creator", "webapp-test"],
        "superpowers": ["brainstorm", "debugging", "git-worktree", "code-review", "subagent"],
    }
    text = f"{skill.get('name', '')} {skill.get('description', '')} {skill.get('id', '')}".lower()
    tags = []
    for tag, keywords in tag_rules.items():
        if any(kw in text for kw in keywords):
            tags.append(tag)
    return tags or ["other"]


def cmd_list(args):
    registry = load_registry()
    if not registry:
        print("No skills found.")
        return

    for s in registry:
        s["tags"] = compute_tags(s)

    if args.tag:
        tag_lower = args.tag.lower()
        filtered = [s for s in registry if any(tag_lower in t for t in s.get("tags", []))]
    else:
        filtered = registry

    filtered = filtered[:args.limit]

    print(f"Showing {len(filtered)} skill(s){f' tagged `{args.tag}`' if args.tag else ''}:\n")
    for skill in filtered:
        name = skill.get("name", skill["id"])
        desc = (skill.get("description") or "No description")[:80]
        tags = ", ".join(skill.get("tags", []))
        print(f"  {name}  [{tags}]")
        print(f"    {desc}")
        print(f"    ID: {skill['id']}")
        print()


def cmd_find(args):
    registry = load_registry()
    if not registry:
        print("No skills found.")
        return

    for s in registry:
        s["tags"] = compute_tags(s)

    query_lower = args.query.lower().strip()

    scored = []
    for skill in registry:
        score = 0
        name = (skill.get("name") or "").lower()
        desc = (skill.get("description") or "").lower()
        skill_id = skill.get("id", "").lower()
        tags = skill.get("tags", [])

        if query_lower == name:
            score += 20
        elif query_lower in name:
            score += 10
        elif query_lower in skill_id:
            score += 8
        if query_lower in desc:
            score += 5
        for tag in tags:
            if query_lower in tag:
                score += 3

        words = query_lower.split()
        for word in words:
            if len(word) < 3:
                continue
            if word in name:
                score += 2
            if word in desc:
                score += 1
            if word in skill_id:
                score += 2

        if score > 0:
            scored.append((score, skill))

    scored.sort(key=lambda x: x[0], reverse=True)
    results = scored[:args.limit]

    if not results:
        print(f"No skills found matching '{args.query}'.")
        print("Try: find_skill --query 'docker', 'testing', 'security', 'pdf', 'brainstorming'")
        return

    print(f"Found {len(results)} skill(s) matching '{args.query}':\n")
    for score, skill in results:
        name = skill.get("name", skill["id"])
        desc = (skill.get("description") or "No description")[:150]
        print(f"  • {name}  (score: {score})")
        print(f"    {desc}")
        print(f"    ID: {skill['id']}")
        print()


def cmd_read(args):
    skill_dir = SKILLS_DIR / args.skill_id
    if not skill_dir.exists():
        # fuzzy match
        matches = [d for d in os.listdir(SKILLS_DIR) if args.skill_id.lower() in d.lower()]
        if len(matches) == 1:
            skill_dir = SKILLS_DIR / matches[0]
        elif len(matches) > 1:
            print(f"Ambiguous. Did you mean: {', '.join(matches)}?")
            return
        else:
            print(f"Skill '{args.skill_id}' not found.")
            return

    skill_file = skill_dir / "SKILL.md"
    if not skill_file.exists():
        skill_file = skill_dir / "skill.md"
    if not skill_file.exists():
        print(f"Skill '{args.skill_id}' has no SKILL.md.")
        return

    content = skill_file.read_text(encoding="utf-8")
    if content.startswith("---"):
        end = content.find("---", 3)
        if end > 0:
            content = content[end + 3:].strip()

    print(content)


def cmd_push(args):
    skill_dir = SKILLS_DIR / args.skill_id
    actual_id = args.skill_id

    if not skill_dir.exists():
        matches = [d for d in os.listdir(SKILLS_DIR) if args.skill_id.lower() in d.lower()]
        if len(matches) == 1:
            skill_dir = SKILLS_DIR / matches[0]
            actual_id = matches[0]
        else:
            print(f"Skill '{args.skill_id}' not found.")
            return

    # Resolve project path
    project = Path(args.project).resolve()
    if not project.exists():
        print(f"Project path '{args.project}' does not exist.")
        return

    # Load skill name from frontmatter
    skill_md = skill_dir / "SKILL.md"
    if not skill_md.exists():
        skill_md = skill_dir / "skill.md"
    if not skill_md.exists():
        print(f"Skill '{actual_id}' has no SKILL.md file.")
        return

    skill_name = actual_id
    try:
        content = skill_md.read_text(encoding="utf-8")
        if content.startswith("---"):
            end = content.find("---", 3)
            if end > 0:
                for line in content[3:end].split("\n"):
                    if line.strip().startswith("name:"):
                        skill_name = line.split(":", 1)[1].strip().strip('"').strip("'")
                        break
    except Exception:
        pass

    # Determine target directory
    target_dir_name = CLI_TARGETS.get(args.target.lower())
    if not target_dir_name:
        print(f"Unknown target '{args.target}'. Supported: {', '.join(CLI_TARGETS.keys())}")
        return

    target_dir = project / target_dir_name
    target_dir.mkdir(parents=True, exist_ok=True)

    # For pi: create <skill_id>/SKILL.md (Agent Skills spec). For others: flat .md
    if args.target.lower() == "pi":
        dest_skill_dir = target_dir / actual_id
        dest_skill_dir.mkdir(parents=True, exist_ok=True)
        dest_file = dest_skill_dir / "SKILL.md"
        shutil.copy2(skill_md, dest_file)
        for subdir_name in ["scripts", "references", "reference"]:
            subdir = skill_dir / subdir_name
            if subdir.exists() and subdir.is_dir():
                dest_subdir = dest_skill_dir / subdir_name
                if dest_subdir.exists():
                    shutil.rmtree(dest_subdir)
                shutil.copytree(subdir, dest_subdir)
    else:
        dest_file = target_dir / f"{actual_id}.md"
        shutil.copy2(skill_md, dest_file)
        for subdir_name in ["scripts", "references", "reference"]:
            subdir = skill_dir / subdir_name
            if subdir.exists() and subdir.is_dir():
                dest_subdir = target_dir / actual_id / subdir_name
                if dest_subdir.exists():
                    shutil.rmtree(dest_subdir)
                shutil.copytree(subdir, dest_subdir)

    print(f"✅ Pushed skill '{skill_name}' ({actual_id}) to {args.target}!")
    print(f"   Destination: {dest_file}")
    print(f"   Project: {project}")
    print(f"   Target: {args.target} ({target_dir_name}/)")
    slash = f"/skill:{actual_id}" if args.target.lower() == "pi" else f"/{actual_id}"
    print(f"\n   Use as command: {slash}")


def main():
    parser = argparse.ArgumentParser(description="Skills CLI — manage skills from the terminal")
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # list
    p_list = subparsers.add_parser("list", help="List all available skills")
    p_list.add_argument("--tag", default="", help="Filter by tag")
    p_list.add_argument("--limit", type=int, default=30, help="Max results")

    # find
    p_find = subparsers.add_parser("find", help="Find skills matching a query")
    p_find.add_argument("query", help="Search query")
    p_find.add_argument("--limit", type=int, default=10, help="Max results")

    # read
    p_read = subparsers.add_parser("read", help="Read a skill's full content")
    p_read.add_argument("skill_id", help="Skill ID")

    # push
    p_push = subparsers.add_parser("push", help="Push a skill to a project's CLI")
    p_push.add_argument("skill_id", help="Skill ID")
    p_push.add_argument("--project", default=".", help="Project directory path (default: current directory)")
    p_push.add_argument("--target", default="pi", choices=list(CLI_TARGETS.keys()), help="CLI target (default: pi)")

    args = parser.parse_args()

    if args.command == "list":
        cmd_list(args)
    elif args.command == "find":
        cmd_find(args)
    elif args.command == "read":
        cmd_read(args)
    elif args.command == "push":
        cmd_push(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()