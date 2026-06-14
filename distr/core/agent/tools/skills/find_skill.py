"""
Find Skill Tool — search skills by capability, or list all.

When query is provided: searches by name/description/tag with relevance scoring.
When query is empty: lists all skills (optionally filtered by tag).
"""

import json
import logging
from pathlib import Path
from typing import Type

from distr.core.agent.tool_voice_format import voice_then_reference
from langchain.tools import BaseTool
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent.parent
_SKILLS_DIR = _PROJECT_ROOT / "skills"

_TAG_RULES = {
    "planning": ["scope", "epic", "story", "priorit", "research", "opportunity"],
    "documentation": ["doc", "creator", "writer", "reference", "presentation"],
    "execution": ["execut", "pipeline", "coordinat", "task-creator", "task-executor", "validator"],
    "quality": ["quality", "test", "regression", "checker", "planner"],
    "auditing": ["auditor", "audit", "security", "dead-code", "pattern", "dependency"],
    "bootstrap": ["bootstrap", "generat", "setup", "docker", "cicd", "linter", "healthcheck"],
    "performance": ["performance", "optim", "upgrad", "moderniz", "bundle"],
    "creative": ["art", "design", "canvas", "pptx", "docx", "xlsx", "pdf", "brand", "theme", "web-artifacts"],
    "dev-tools": ["mcp", "skill-creator", "webapp-test"],
    "superpowers": ["brainstorm", "debugging", "git-worktree", "code-review", "subagent"],
}


def _load_registry() -> list[dict]:
    registry_file = _SKILLS_DIR / "skills_registry.json"
    if registry_file.exists():
        try:
            return json.loads(registry_file.read_text(encoding="utf-8"))
        except Exception as e:
            logger.error("Failed to load skills registry: %s", e)
    return []


def _compute_tags(skill: dict) -> list[str]:
    text = f"{skill.get('name', '')} {skill.get('description', '')} {skill.get('id', '')}".lower()
    tags = [tag for tag, keywords in _TAG_RULES.items() if any(kw in text for kw in keywords)]
    return tags or ["other"]


class FindSkillInput(BaseModel):
    query: str = Field(default="", description="Search query: a capability, technology, or task. Leave empty to list all. Examples: 'debugging', 'docker setup', 'security audit', 'pdf'.")
    tag: str = Field(default="", description="Filter by tag: planning, documentation, execution, quality, auditing, bootstrap, performance, creative, dev-tools, superpowers.")
    limit: int = Field(default=10, description="Max results to return.")


class FindSkillTool(BaseTool):
    """Tool for searching and listing skills."""

    name: str = "find_skill"
    description: str = """Search skills by capability, or list all available skills.

When you're about to work on a task, use this to check if a skill exists that can help.
Also use when the user asks: "what skills do we have?", "find a skill for X", "show security skills".

If query is empty, lists all skills (optionally filtered by tag).
If query is provided, returns relevance-scored matches.

After finding a skill, read bundled content under skills/<id>/, or use push_skill(skill_id, project_path, instructions=...)
to install into Pi — include how the user wants to use it in instructions (ask once if unclear).
"""
    args_schema: Type[BaseModel] = FindSkillInput

    def _run(self, query: str = "", tag: str = "", limit: int = 10, **kwargs) -> str:
        try:
            registry = _load_registry()
            if not registry:
                return "No skills found. The skills registry is empty or missing."

            for s in registry:
                s["tags"] = _compute_tags(s)

            # Filter by tag first
            if tag:
                tag_lower = tag.lower().strip()
                registry = [s for s in registry if any(tag_lower in t for t in s.get("tags", []))]
                if not registry:
                    return f"No skills with tag '{tag}'. Tags: {', '.join(_TAG_RULES.keys())}"

            # No query → list mode
            query_lower = query.lower().strip()
            if not query_lower:
                results = registry[:limit]
                lines = [f"Showing {len(results)} of {len(registry)} skill(s){f' tagged `{tag}`' if tag else ''}:\n"]
                for skill in results:
                    name = skill.get("name", skill["id"])
                    desc = (skill.get("description") or "No description")[:80]
                    tags_str = ", ".join(skill.get("tags", []))
                    lines.append(f"• {name} [{tags_str}]")
                    lines.append(f"  {desc}")
                    lines.append(f"  ID: {skill['id']}")
                    lines.append("")
                if len(registry) > limit:
                    lines.append(f"... and {len(registry) - limit} more. Pass a query to search.")
                lines.append(
                    "Read bundled skill files under skills/<id>/. To install into a project's Pi CLI, use push_skill(skill_id, project_path, instructions=...) "
                    "with the user's stated goals in instructions (or ask briefly how they want to use it before pushing)."
                )
                ref = "\n".join(lines)
                tag_note = f", filtered by tag {tag}" if tag else ""
                spoken = (
                    f"I opened the bundled skill list{tag_note}. "
                    f"There are {len(registry)} total; showing {len(results)}. Names and IDs are in the reference."
                )
                return voice_then_reference(spoken, ref)

            # Query → search mode with scoring
            scored = []
            for skill in registry:
                score = 0
                name = (skill.get("name") or "").lower()
                desc = (skill.get("description") or "").lower()
                skill_id = (skill.get("id") or "").lower()

                if query_lower == name:
                    score += 20
                elif query_lower in name:
                    score += 10
                elif query_lower in skill_id:
                    score += 8
                if query_lower in desc:
                    score += 5
                for t in skill.get("tags", []):
                    if query_lower in t:
                        score += 3
                for word in query_lower.split():
                    if len(word) < 3:
                        continue
                    if word in name: score += 2
                    if word in desc: score += 1
                    if word in skill_id: score += 2
                    for t in skill.get("tags", []):
                        if word in t: score += 1

                if score > 0:
                    scored.append((score, skill))

            scored.sort(key=lambda x: x[0], reverse=True)
            results = scored[:limit]

            if not results:
                return f"No skills matching '{query}'. Try: {', '.join(list(_TAG_RULES.keys())[:6])}"

            lines = [f"Found {len(results)} skill(s) matching '{query}':\n"]
            for score, skill in results:
                lines.append(f"• {skill.get('name', skill['id'])}  (score: {score})")
                lines.append(f"  {skill.get('description', 'No description')[:200]}")
                lines.append(f"  ID: {skill['id']}")
                lines.append("")

            lines.append(
                "Read bundled skill files under skills/<id>/. To install into a project's Pi CLI, use push_skill(skill_id, project_path, instructions=...) "
                "with the user's stated goals in instructions (or ask briefly how they want to use it before pushing)."
            )
            ref = "\n".join(lines)
            spoken = (
                f"I searched skills for {query!r} and found {len(results)} matches. "
                "Scores and IDs are in the reference."
            )
            return voice_then_reference(spoken, ref)

        except Exception as e:
            logger.error("Error in find_skill: %s", e, exc_info=True)
            return f"Error searching skills: {str(e)}"

    async def _arun(self, query: str = "", tag: str = "", limit: int = 10, **kwargs) -> str:
        return self._run(query=query, tag=tag, limit=limit)