"""Bundled skills catalog — registry, Hermes transfer hints, and validation."""

from __future__ import annotations

import json
import logging
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_SKILLS_DIR = _PROJECT_ROOT / "skills"
_ECC_SKILLS_DIR = _PROJECT_ROOT / "vendor" / "ecc" / "skills"
_REGISTRY_FILE = _SKILLS_DIR / "skills_registry.json"

# Ticket text keywords → bundled skill ids (google/skills + core workflow skills).
_TICKET_SKILL_HINTS: list[tuple[list[str], list[str]]] = [
    (["gemini", "vertex ai", "agent platform", "gen ai", "google genai"], ["gemini-api"]),
    (["gemini interaction", "interactions api"], ["gemini-interactions-api"]),
    (["managed agent", "gemini agent"], ["gemini-agents-api"]),
    (["skill registry", "agent platform skill"], ["agent-platform-skill-registry"]),
    (["bigquery", "bq dataset", "bq table"], ["bigquery-basics"]),
    (["cloud run", "serverless container"], ["cloud-run-basics"]),
    (["cloud sql", "postgres instance", "mysql instance"], ["cloud-sql-basics"]),
    (["gke", "kubernetes engine", "k8s cluster"], ["gke-basics"]),
    (["firebase", "firestore", "firebase auth"], ["firebase-basics"]),
    (["alloydb", "alloy db"], ["alloydb-basics"]),
    (["onboard google cloud", "gcloud setup", "new gcp project"], ["google-cloud-recipe-onboarding"]),
    (["gcp auth", "google cloud auth", "service account", "adc"], ["google-cloud-recipe-auth"]),
    (["vpc flow", "cloud nat", "network observability", "firewall rule"], ["google-cloud-networking-observability"]),
    (["well-architected", "waf security", "cloud security posture"], ["google-cloud-waf-security"]),
    (["reliability", "slo", "disaster recovery"], ["google-cloud-waf-reliability"]),
    (["cost optimization", "finops", "cloud billing"], ["google-cloud-waf-cost-optimization"]),
    (["operational excellence", "runbook", "incident response"], ["google-cloud-waf-operational-excellence"]),
    (["performance optimization", "latency", "cloud performance"], ["google-cloud-waf-performance-optimization"]),
    (["sustainability", "carbon", "green cloud"], ["google-cloud-waf-sustainability"]),
    (["frontend", "react", "vue", "css", "tailwind", "playwright"], ["webapp-testing", "frontend-design"]),
    (["debug", "bug", "failing test", "regression"], ["systematic-debugging", "qa-tester"]),
    (["brainstorm", "design feature", "explore idea"], ["brainstorming", "ceo-scope-review"]),
    (["ship", "merge", "pull request", "pr "], ["finishing-a-development-branch"]),
    (["security audit", "vulnerability", "owasp"], ["ln-621-security-auditor", "safety-guard"]),
    (["docker", "container setup"], ["ln-731-docker-generator"]),
]

_GOOGLE_SKILL_PREFIXES = (
    "gemini-",
    "google-cloud-",
    "cloud-",
    "bigquery-",
    "firebase-",
    "gke-",
    "alloydb-",
    "agent-platform-",
)


def bundled_skills_directory() -> Path:
    return _SKILLS_DIR


def skills_registry_path() -> Path:
    return _REGISTRY_FILE


def _canonical_id(value: str) -> str:
    text = re.sub(r"[^a-z0-9]+", "-", str(value or "").strip().lower())
    return text.strip("-")


def _skill_file(skill_dir: Path | None) -> Path | None:
    if not skill_dir:
        return None
    for name in ("SKILL.md", "skill.md"):
        path = skill_dir / name
        if path.is_file():
            return path
    return None


def _body_excerpt(skill_file: Path, *, limit: int = 220) -> str:
    try:
        text = skill_file.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            text = text[end + 4 :]
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit]


def _local_row_directory(row: dict[str, Any]) -> Path:
    raw_path = str(row.get("path") or row.get("id") or "").strip()
    path = Path(raw_path)
    if path.is_absolute():
        return path
    return _SKILLS_DIR / raw_path


def _vendor_registry_rows(existing_ids: set[str]) -> list[dict[str, Any]]:
    from distr.core.skills.registry import SkillRegistry

    registry = SkillRegistry(local_roots=[_SKILLS_DIR], vendor_roots=[_ECC_SKILLS_DIR]).scan()
    rows: list[dict[str, Any]] = []
    for entry in sorted(registry.entries.values(), key=lambda e: e.canonical_id):
        if entry.source != "ecc_vendor":
            continue
        if entry.canonical_id in existing_ids:
            continue
        skill_file = _skill_file(entry.path)
        description = entry.description or (_body_excerpt(skill_file) if skill_file else "")
        rows.append(
            {
                "id": entry.canonical_id,
                "name": entry.name or entry.canonical_id,
                "description": description,
                "path": str(entry.path.relative_to(_PROJECT_ROOT)) if entry.path.is_relative_to(_PROJECT_ROOT) else str(entry.path),
                "source": "ecc_vendor",
                "provenance": {
                    "repo": "https://github.com/affaan-m/ecc",
                    "license": "MIT",
                    "vendored_path": "vendor/ecc",
                    "content_hash": entry.content_hash,
                },
                "target_surfaces": list(entry.target_surfaces),
                "conflict_policy": "local_preferred",
                "editable": False,
            }
        )
    return rows


@lru_cache(maxsize=1)
def load_registry() -> tuple[dict[str, Any], ...]:
    """Load the deduped local + vendored skills registry as immutable rows."""
    rows: list[dict[str, Any]] = []
    existing_ids: set[str] = set()
    if not _REGISTRY_FILE.is_file():
        raw = []
    else:
        try:
            raw = json.loads(_REGISTRY_FILE.read_text(encoding="utf-8"))
        except Exception:
            logger.warning("skills_registry.json unreadable", exc_info=True)
            raw = []
    if isinstance(raw, list):
        for item in raw:
            if not isinstance(item, dict):
                continue
            skill_id = str(item.get("id") or "").strip()
            if not skill_id:
                continue
            row = dict(item)
            row.setdefault("source", "local")
            row.setdefault("editable", True)
            rows.append(row)
            existing_ids.add(_canonical_id(skill_id))
    rows.extend(_vendor_registry_rows(existing_ids))
    return tuple(rows)


def skill_directory_for_id(skill_id: str) -> Path | None:
    """Resolve a skill id to either a local skill dir or a vendored ECC skill dir."""
    key = _canonical_id(skill_id)
    if not key:
        return None
    for row in load_registry():
        if _canonical_id(str(row.get("id") or "")) != key:
            continue
        source = str(row.get("source") or "").lower()
        if source == "ecc_vendor":
            raw_path = Path(str(row.get("path") or ""))
            path = raw_path if raw_path.is_absolute() else _PROJECT_ROOT / raw_path
        else:
            path = _local_row_directory(row)
        if _skill_file(path):
            return path
    return None


def skill_file_for_id(skill_id: str) -> Path | None:
    return _skill_file(skill_directory_for_id(skill_id))


def registry_by_id() -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for row in load_registry():
        key = str(row.get("id") or "").strip().lower()
        if key:
            out[key] = row
    return out


def filter_known_skill_ids(skill_ids: list[str]) -> list[str]:
    """Keep only ids present in the bundled registry (deduped, order preserved)."""
    known = registry_by_id()
    seen: set[str] = set()
    out: list[str] = []
    for raw in skill_ids:
        sid = str(raw or "").strip()
        if not sid:
            continue
        key = sid.lower()
        if key in known and key not in seen:
            seen.add(key)
            out.append(str(known[key].get("id") or sid))
    return out


def infer_skills_for_ticket(text: str, *, limit: int = 5) -> list[str]:
    """Keyword-based skill suggestions from ticket title/description."""
    lowered = (text or "").lower()
    if not lowered.strip():
        return []
    scored: list[tuple[int, str]] = []
    for keywords, skill_ids in _TICKET_SKILL_HINTS:
        hits = sum(1 for kw in keywords if kw in lowered)
        if hits <= 0:
            continue
        for sid in skill_ids:
            scored.append((hits, sid))
    scored.sort(key=lambda x: x[0], reverse=True)
    return filter_known_skill_ids([sid for _, sid in scored])[:limit]


def hermes_skill_catalog(*, limit: int = 80, source: str | None = None) -> list[dict[str, str]]:
    """Compact skill list for Hermes LLM routing prompts."""
    rows = load_registry()
    if source:
        src = source.strip().lower()
        rows = tuple(r for r in rows if str(r.get("source") or "").lower() == src)
    out: list[dict[str, str]] = []
    for row in rows[:limit]:
        out.append(
            {
                "id": str(row.get("id") or ""),
                "name": str(row.get("name") or row.get("id") or ""),
                "description": (str(row.get("description") or ""))[:180],
                "source": str(row.get("source") or "bundled"),
            }
        )
    return out


def merge_transfer_skills(
    *,
    policy_skills: list[str] | None = None,
    advisory_skills: list[str] | None = None,
    inferred_skills: list[str] | None = None,
    workflow_pre_chain: list[str] | None = None,
) -> list[str]:
    """
    Merge skill ids Hermes should push before harness execution.

    Order: workflow pre_chain → board policy/harness prefs → ticket inference → LLM advisory.
    Unknown ids are dropped.
    """
    combined: list[str] = []
    for batch in (workflow_pre_chain, policy_skills, inferred_skills, advisory_skills):
        if batch:
            combined.extend(str(s).strip() for s in batch if str(s).strip())
    return filter_known_skill_ids(combined)


def parse_skill_chain(raw: str | list | None) -> list[str]:
    """Parse workflow pre_chain/post_chain JSON or list."""
    if isinstance(raw, list):
        return filter_known_skill_ids([str(s) for s in raw])
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, list):
            return filter_known_skill_ids([str(s) for s in parsed])
    except Exception:
        pass
    return filter_known_skill_ids([s.strip() for s in str(raw).split(",") if s.strip()])


def is_google_skill(skill_id: str) -> bool:
    sid = str(skill_id or "").lower()
    return any(sid.startswith(prefix) for prefix in _GOOGLE_SKILL_PREFIXES)


def registry_entry_for(skill_id: str) -> dict[str, Any] | None:
    return registry_by_id().get(str(skill_id or "").strip().lower())
