"""Bundled skills catalog — registry, Hermes transfer hints, and validation."""

from __future__ import annotations

import json
import logging
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

from distr.core.plugins import ecc_vendor_dir, competition_ponytail_skills_dir, competition_fallow_skills_dir

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_SKILLS_DIR = _PROJECT_ROOT / "skills"
_ECC_VENDOR_DIR = ecc_vendor_dir()
_ECC_SKILLS_DIR = _ECC_VENDOR_DIR / "skills"
_COMPETITION_SKILL_DIRS = [competition_ponytail_skills_dir(), competition_fallow_skills_dir()]
_ECC_VENDOR_METADATA_FILE = _ECC_VENDOR_DIR / ".decisions-vendor.json"
_REGISTRY_FILE = _SKILLS_DIR / "skills_registry.json"

# Ticket text keywords -> bundled skill ids (google/skills + core workflow skills).
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
    (["frontend", "react", "vue", "css", "tailwind", "playwright"], ["webapp-testing", "frontend-design", "decisions-design-references"]),
    (["landing page", "dashboard", "ui design", "mockup", "aceternity", "mobbin", "refero", "godly"], ["decisions-ui-ideation", "decisions-design-references", "frontend-design-direction"]),
    (["debug", "bug", "failing test", "regression"], ["systematic-debugging", "qa-tester"]),
    (["over-engineer", "bloat", "yagni", "minimal", "ponytail"], ["ponytail", "ponytail-review"]),
    (["dead code", "unused export", "circular dep", "code health", "fallow", "dupes"], ["fallow"]),
    (["brainstorm", "design feature", "explore idea"], ["brainstorming", "ceo-scope-review"]),
    (["research", "competitor", "deep dive", "look up", "twitter", "reddit", "youtube", "rss", "podcast"], ["decisions-agent-reach", "agent-reach", "last30days"]),
    (["humanize", "ai slop", "sounds robotic", "polish copy"], ["humanizer"]),
    (["marketing", "landing page", "conversion", "cro", "seo audit"], ["decisions-marketing-skills", "product-marketing"]),
    (["youtube", "yt-dlp", "subtitle", "transcript", "video brief"], ["decisions-yt-dlp", "video-editing"]),
    (["gmail", "slack", "notion", "linear", "jira", "composio", "send email", "post to slack"], ["decisions-composio"]),
    (["aesthetic", "design system", "glassmorphism", "bento grid"], ["decisions-design-aesthetics"]),
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

_VENDOR_SOURCES = {"ecc", "ecc_vendor", "competition", "competition_vendor", "agent_reach_vendor", "community_vendor"}


def bundled_skills_directory() -> Path:
    return _SKILLS_DIR


def ecc_vendor_directory() -> Path:
    return _ECC_VENDOR_DIR


def skills_registry_path() -> Path:
    return _REGISTRY_FILE


def _canonical_id(value: str) -> str:
    text = re.sub(r"[^a-z0-9]+", "-", str(value or "").strip().lower())
    return text.strip("-")


def _relative_path(path: Path) -> str:
    try:
        return str(path.relative_to(_PROJECT_ROOT))
    except ValueError:
        return str(path)


def _load_ecc_vendor_metadata() -> dict[str, Any]:
    if not _ECC_VENDOR_METADATA_FILE.is_file():
        return {}
    try:
        payload = json.loads(_ECC_VENDOR_METADATA_FILE.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except Exception:
        logger.warning("ECC vendor metadata unreadable", exc_info=True)
        return {}


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
    text = re.sub(r"^(?:>\s*)+(?:[-*•]\s*)?", "", text)
    text = re.sub(r"^[-*•]\s+", "", text)
    return text[:limit].strip()


def _local_row_directory(row: dict[str, Any]) -> Path:
    raw_path = str(row.get("path") or row.get("id") or "").strip()
    path = Path(raw_path)
    if path.is_absolute():
        return path
    return _SKILLS_DIR / raw_path


def _registry_scan():
    from distr.core.skills.registry import SkillRegistry

    return SkillRegistry(
        local_roots=[_SKILLS_DIR],
        vendor_roots=[_ECC_SKILLS_DIR],
        competition_roots=_COMPETITION_SKILL_DIRS,
    ).scan()


def _vendor_payload(entry: Any, metadata: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": "ecc",
        "source": "ecc_vendor",
        "path": _relative_path(entry.path),
        "repo": str(metadata.get("source") or "https://github.com/affaan-m/ecc"),
        "commit": str(metadata.get("commit") or ""),
        "license": str(metadata.get("license") or "MIT"),
        "content_hash": entry.content_hash,
    }


def _vendor_registry_rows(scan: Any, existing_ids: set[str], metadata: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for entry in sorted(scan.entries.values(), key=lambda e: e.canonical_id):
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
                "path": _relative_path(entry.path),
                "source": "ecc_vendor",
                "vendor": _vendor_payload(entry, metadata),
                "provenance": {
                    "repo": str(metadata.get("source") or "https://github.com/affaan-m/ecc"),
                    "license": str(metadata.get("license") or "MIT"),
                    "vendored_path": "plugins/ecc",
                    "commit": str(metadata.get("commit") or ""),
                    "content_hash": entry.content_hash,
                },
                "target_surfaces": list(entry.target_surfaces),
                "conflict_policy": "local_preferred",
                "editable": False,
                "tags": ["ecc", "vendor"],
            }
        )
    return rows


def _attach_vendor_conflicts(rows: list[dict[str, Any]], scan: Any, metadata: dict[str, Any]) -> None:
    by_id = {_canonical_id(str(row.get("id") or "")): row for row in rows}
    for skill_id, conflicts in getattr(scan, "conflicts", {}).items():
        native = by_id.get(_canonical_id(skill_id))
        if not native:
            continue
        sources = native.setdefault("vendor_sources", [])
        merged_from = native.setdefault("merged_from", [])
        for duplicate in conflicts:
            if duplicate.source != "ecc_vendor":
                continue
            vendor = _vendor_payload(duplicate, metadata)
            if isinstance(sources, list) and not any(
                isinstance(item, dict) and item.get("path") == vendor.get("path")
                for item in sources
            ):
                sources.append(vendor)
            if isinstance(merged_from, list) and vendor["path"] not in merged_from:
                merged_from.append(vendor["path"])
        native.setdefault("conflict_policy", "local_preferred")


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

    scan = _registry_scan()
    metadata = _load_ecc_vendor_metadata()
    _attach_vendor_conflicts(rows, scan, metadata)
    rows.extend(_vendor_registry_rows(scan, existing_ids, metadata))
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
        if source in _VENDOR_SOURCES:
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


def registry_entry_for(skill_id: str) -> dict[str, Any] | None:
    return registry_by_id().get(str(skill_id or "").strip().lower())


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


def _matches_source(row: dict[str, Any], source: str) -> bool:
    row_source = str(row.get("source") or "").strip().lower()
    requested = source.strip().lower()
    if requested in _VENDOR_SOURCES:
        return row_source in _VENDOR_SOURCES
    return row_source == requested


def orchestrator_skill_catalog(*, limit: int = 80, source: str | None = None) -> list[dict[str, str]]:
    """Compact skill list for Hermes LLM routing prompts."""
    rows = load_registry()
    if source:
        rows = tuple(r for r in rows if _matches_source(r, source))
    elif limit and rows:
        ecc_rows = [r for r in rows if str(r.get("source") or "").lower() in _VENDOR_SOURCES]
        native_rows = [r for r in rows if str(r.get("source") or "").lower() not in _VENDOR_SOURCES]
        if ecc_rows and native_rows:
            preferred_ecc = {
                "react-patterns": 0,
                "python-patterns": 1,
                "security-review": 2,
                "mcp-server-patterns": 3,
                "git-workflow": 4,
                "repo-scan": 5,
                "documentation-lookup": 6,
                "docker-patterns": 7,
                "frontend-patterns": 8,
                "api-design": 9,
            }
            ecc_rows.sort(key=lambda row: preferred_ecc.get(str(row.get("id") or ""), 1000))
            ecc_budget = min(len(ecc_rows), max(5, limit // 2))
            native_budget = max(0, limit - ecc_budget)
            rows = tuple(native_rows[:native_budget] + ecc_rows[:ecc_budget])
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

    Order: workflow pre_chain -> board policy/harness prefs -> ticket inference -> LLM advisory.
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
