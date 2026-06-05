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
_REGISTRY_FILE = _SKILLS_DIR / "skills_registry.json"
_ECC_VENDOR_DIR = _PROJECT_ROOT / "vendor" / "ecc"
_ECC_VENDOR_SKILLS_DIR = _ECC_VENDOR_DIR / "skills"
_ECC_VENDOR_METADATA_FILE = _ECC_VENDOR_DIR / ".decisions-vendor.json"

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


def ecc_vendor_directory() -> Path:
    return _ECC_VENDOR_DIR


def _load_ecc_vendor_metadata() -> dict[str, Any]:
    if not _ECC_VENDOR_METADATA_FILE.is_file():
        return {}
    try:
        payload = json.loads(_ECC_VENDOR_METADATA_FILE.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except Exception:
        logger.warning("ECC vendor metadata unreadable", exc_info=True)
        return {}


def _parse_skill_frontmatter(path: Path) -> dict[str, str]:
    text = path.read_text(encoding="utf-8", errors="replace")
    data: dict[str, str] = {}
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            for line in parts[1].splitlines():
                if ":" not in line:
                    continue
                key, value = line.split(":", 1)
                key = key.strip()
                if key in {"name", "description"}:
                    data[key] = value.strip().strip("\"'")
    if not data.get("description"):
        lines = [
            line.strip()
            for line in text.splitlines()
            if line.strip() and not line.strip().startswith("---")
        ]
        data["description"] = " ".join(lines[:3])[:300]
    return data


def _load_ecc_vendor_registry() -> tuple[dict[str, Any], ...]:
    if not _ECC_VENDOR_SKILLS_DIR.is_dir():
        return tuple()
    metadata = _load_ecc_vendor_metadata()
    commit = str(metadata.get("commit") or "").strip()
    rows: list[dict[str, Any]] = []
    for skill_file in sorted(_ECC_VENDOR_SKILLS_DIR.glob("*/SKILL.md")):
        skill_id = skill_file.parent.name
        frontmatter = _parse_skill_frontmatter(skill_file)
        rows.append(
            {
                "id": skill_id,
                "name": frontmatter.get("name") or skill_id,
                "description": frontmatter.get("description") or "",
                "path": str(skill_file.parent.relative_to(_PROJECT_ROOT)),
                "source": "ecc",
                "vendor": {
                    "name": "ecc",
                    "path": str(_ECC_VENDOR_DIR.relative_to(_PROJECT_ROOT)),
                    "commit": commit,
                    "license": str(metadata.get("license") or "MIT"),
                    "upstream": str(metadata.get("source") or "https://github.com/affaan-m/ecc"),
                },
                "tags": ["ecc", "vendor"],
            }
        )
    return tuple(rows)


@lru_cache(maxsize=1)
def load_registry() -> tuple[dict[str, Any], ...]:
    """Load native and vendored skill rows as an immutable tuple of dict rows."""
    if not _REGISTRY_FILE.is_file():
        raw = []
    else:
        try:
            raw = json.loads(_REGISTRY_FILE.read_text(encoding="utf-8"))
        except Exception:
            logger.warning("skills_registry.json unreadable", exc_info=True)
            raw = []
    if not isinstance(raw, list):
        raw = []
    rows: list[dict[str, Any]] = []
    for item in raw:
        if isinstance(item, dict) and str(item.get("id") or "").strip():
            row = dict(item)
            row.setdefault("source", "local")
            rows.append(row)
    by_id = {str(row.get("id") or "").strip().lower(): row for row in rows}
    for vendor_row in _load_ecc_vendor_registry():
        key = str(vendor_row.get("id") or "").strip().lower()
        if not key:
            continue
        if key in by_id:
            native = by_id[key]
            sources = native.setdefault("vendor_sources", [])
            if isinstance(sources, list) and not any(
                isinstance(item, dict) and item.get("source") == vendor_row.get("source")
                for item in sources
            ):
                sources.append(
                    {
                        "source": vendor_row.get("source"),
                        "path": vendor_row.get("path"),
                        "vendor": vendor_row.get("vendor"),
                    }
                )
            native.setdefault("merged_from", []).append(vendor_row.get("path"))
            continue
        rows.append(dict(vendor_row))
        by_id[key] = rows[-1]
    return tuple(rows)


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
    elif limit and rows:
        ecc_rows = [r for r in rows if str(r.get("source") or "").lower() == "ecc"]
        native_rows = [r for r in rows if str(r.get("source") or "").lower() != "ecc"]
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
