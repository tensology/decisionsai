"""
Learnings Keeper — persistent cross-session knowledge base.

Stores per-project learnings as JSONL files. Learnings compound across sessions,
but with deliberate anti-context-rot guards: staleness decay, hard caps on
context injection, and on-demand retrieval rather than preamble dumps.

File layout:
    <project>/.decisions/learnings/learnings.jsonl

Entry format:
    {
        "ts": "2026-04-29T10:30:00Z",
        "type": "pattern|pitfall|preference|quirk|operational",
        "key": "kebab-case-identifier",
        "insight": "Human-readable description",
        "confidence": 8,
        "source": "observed|user-stated|inferred",
        "files": ["path/to/file.ts"],
        "skill": "skill-name",
        "branch": "feature/auth",
        "tags": ["auth", "security"],
        "reinforced": 0,
        "last_used": "2026-04-29T10:30:00Z"
    }

Anti-rot design:
- Staleness decay: half-life of 14 days (relevance halves every 2 weeks)
- Reinforced counter: each time a learning is used/confirmed, it gets a boost
- Hard cap: max 3 learnings ever injected into agent context
- On-demand: learnings are queried surgically, not dumped at session start
- Compaction: when >5 learnings share the same key, old ones get summarized
"""

from __future__ import annotations

import json
import logging
import math
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ── Anti-rot constants ────────────────────────────────────────────
STALENESS_HALF_LIFE_DAYS = 14        # relevance halves every 2 weeks
MAX_CONTEXT_LEARNINGS = 3            # never inject more than 3 into context
MAX_LEARNINGS_PER_KEY = 5            # compact when >5 entries share same key
REINFORCEMENT_BOOST = 0.15           # each reinforce adds 15% to confidence


def _find_project_root(start: Optional[Path] = None) -> Path:
    cwd = start or Path.cwd()
    for parent in [cwd] + list(cwd.parents):
        if (parent / ".decisions").exists() or (parent / "distr").exists():
            return parent
    return cwd


def _get_learnings_dir(project_path: Optional[Path] = None) -> Path:
    root = _find_project_root(project_path)
    d = root / ".decisions" / "learnings"
    d.mkdir(parents=True, exist_ok=True)
    return d


def get_learnings_file(project_path: Optional[Path] = None) -> Path:
    return _get_learnings_dir(project_path) / "learnings.jsonl"


def _get_branch(project_path: Optional[Path] = None) -> Optional[str]:
    try:
        import subprocess
        result = subprocess.run(
            ["git", "-C", str(project_path or Path.cwd()), "branch", "--show-current"],
            capture_output=True, text=True, timeout=5,
        )
        return result.stdout.strip() if result.returncode == 0 else None
    except Exception:
        return None


# ── Relevance scoring ─────────────────────────────────────────────

def _staleness_decay(ts_str: str) -> float:
    """Compute decay factor based on age. 14-day half-life."""
    try:
        entry_time = datetime.fromisoformat(ts_str.replace("Z", "+00:00")).timestamp()
    except (ValueError, AttributeError):
        return 0.5
    age_days = (time.time() - entry_time) / 86400.0
    if age_days < 0:
        return 1.0
    return math.pow(0.5, age_days / STALENESS_HALF_LIFE_DAYS)


def _score_entry(
    entry: Dict[str, Any],
    query: Optional[str] = None,
    active_branch: Optional[str] = None,
    active_files: Optional[List[str]] = None,
    active_tags: Optional[List[str]] = None,
) -> float:
    """Score a learning by relevance. Higher = more likely to be useful now.

    Formula: confidence * staleness_decay * (1 + reinforcement_boost) * match_multiplier
    """
    confidence = entry.get("confidence", 5) / 10.0
    reinforced = entry.get("reinforced", 0)
    decay = _staleness_decay(entry.get("ts", ""))

    base = confidence * decay * (1.0 + reinforced * REINFORCEMENT_BOOST)
    multiplier = 1.0

    # Branch match — same branch gets a big boost
    if active_branch and entry.get("branch") == active_branch:
        multiplier *= 3.0

    # File match — working on same files
    if active_files:
        entry_files = set(entry.get("files", []))
        overlap = entry_files.intersection(active_files)
        if overlap:
            multiplier *= 1.0 + (len(overlap) * 0.5)

    # Tag match
    if active_tags:
        entry_tags = set(entry.get("tags", []))
        overlap = entry_tags.intersection(active_tags)
        if overlap:
            multiplier *= 1.0 + (len(overlap) * 0.3)

    # Query text match
    if query:
        query_lower = query.lower()
        text_fields = f"{entry.get('key', '')} {entry.get('insight', '')}"
        if query_lower in text_fields.lower():
            multiplier *= 2.5
        else:
            # Partial word match
            words = query_lower.split()
            matched = sum(1 for w in words if len(w) > 2 and w in text_fields.lower())
            if matched:
                multiplier *= 1.0 + (matched * 0.4)

    return base * multiplier


# ── Public API ─────────────────────────────────────────────────────

def log_learning(
    entry_type: str,
    key: str,
    insight: str,
    confidence: int = 5,
    source: str = "observed",
    files: Optional[List[str]] = None,
    skill: Optional[str] = None,
    branch: Optional[str] = None,
    tags: Optional[List[str]] = None,
    project_path: Optional[Path] = None,
) -> bool:
    """Log a new learning. Returns True if logged."""
    learnings_file = get_learnings_file(project_path)
    branch = branch or _get_branch(project_path)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    entry: Dict[str, Any] = {
        "ts": now,
        "last_used": now,
        "type": entry_type,
        "key": key,
        "insight": insight,
        "confidence": min(confidence, 10),
        "source": source,
        "files": files or [],
        "skill": skill,
        "branch": branch or "unknown",
        "tags": tags or [],
        "reinforced": 0,
        "version": "2.0",
    }

    try:
        with open(learnings_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        logger.debug("Logged learning: %s (%s)", key, entry_type)

        # Auto-compact if too many entries for this key
        _maybe_compact(learnings_file, key)
        return True
    except OSError as e:
        logger.warning("Failed to log learning: %s", e)
        return False


def reinforce_learning(
    key: str,
    project_path: Optional[Path] = None,
) -> bool:
    """Mark a learning as used/confirmed. Boosts its relevance score."""
    lf = get_learnings_file(project_path)
    if not lf.exists():
        return False

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    updated = False
    lines = []

    try:
        with open(lf, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    lines.append(line)
                    continue

                if entry.get("key") == key:
                    entry["reinforced"] = entry.get("reinforced", 0) + 1
                    entry["last_used"] = now
                    updated = True
                lines.append(json.dumps(entry, ensure_ascii=False))

        with open(lf, "w", encoding="utf-8") as f:
            for line in lines:
                f.write(line + "\n")

        if updated:
            logger.debug("Reinforced learning: %s", key)
        return updated
    except OSError as e:
        logger.warning("Failed to reinforce learning: %s", e)
        return False


def search_learnings(
    query: Optional[str] = None,
    entry_type: Optional[str] = None,
    tags: Optional[List[str]] = None,
    files: Optional[List[str]] = None,
    branch: Optional[str] = None,
    limit: int = 10,
    min_confidence: int = 4,
    project_path: Optional[Path] = None,
    cross_project: bool = False,
) -> List[Dict[str, Any]]:
    """Full search — for UI browsing, export, admin. NOT for context injection.

    For context injection, use get_context_learnings() instead — it applies
    staleness decay, caps at 3, and returns a compact format.
    """
    results: List[Tuple[float, Dict[str, Any]]] = []
    learnings_dir = _get_learnings_dir(project_path)

    search_dirs = [learnings_dir]
    if cross_project:
        root = _find_project_root(project_path)
        parent = root.parent
        extra = []
        if parent.exists():
            for child in parent.iterdir():
                ld = child / ".decisions" / "learnings"
                if ld.exists():
                    extra.append(ld)
        search_dirs = extra if extra else search_dirs

    active_files = set(files or [])
    active_tags = set(tags or [])

    for directory in search_dirs:
        lf = directory / "learnings.jsonl"
        if not lf.exists():
            continue
        try:
            with open(lf, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                    except json.JSONDecodeError:
                        continue

                    if entry.get("confidence", 0) < min_confidence:
                        continue
                    if entry_type and entry.get("type") != entry_type:
                        continue
                    if branch and entry.get("branch") != branch:
                        continue

                    if active_tags:
                        entry_tags = set(entry.get("tags", []))
                        if not entry_tags.intersection(active_tags):
                            continue
                    if active_files:
                        entry_files = entry.get("files", [])
                        if not active_files.intersection(entry_files):
                            continue
                    if query and query.lower() not in (
                        f"{entry.get('key', '')} {entry.get('insight', '')}"
                    ).lower():
                        continue

                    score = _score_entry(entry, query, branch, list(active_files), list(active_tags))
                    entry["_score"] = round(score, 2)
                    entry["_project"] = directory.parent.parent.name
                    results.append((score, entry))
        except OSError as e:
            logger.debug("Error reading %s: %s", directory, e)

    results.sort(key=lambda x: x[0], reverse=True)
    return [e for _, e in results[:limit]]


def get_context_learnings(
    query: Optional[str] = None,
    active_files: Optional[List[str]] = None,
    active_tags: Optional[List[str]] = None,
    project_path: Optional[Path] = None,
) -> Optional[str]:
    """Get a compact, anti-rot snippet for agent context injection.

    Returns at most 3 highly-relevant learnings, each as a single line.
    Returns None if nothing relevant found. Designed to be cheap on tokens.

    Example output:
        [learnings] auth-middleware: auth.ts returns 200 not 401 on expired token
        [learnings] cors-config: must list prod origins explicitly in CORS_ORIGINS
    """
    branch = _get_branch(project_path)
    learnings_dir = _get_learnings_dir(project_path)

    scored: List[Tuple[float, Dict[str, Any]]] = []

    for directory in [learnings_dir]:
        lf = directory / "learnings.jsonl"
        if not lf.exists():
            continue
        try:
            with open(lf, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                    except json.JSONDecodeError:
                        continue

                    # Only return high-confidence, non-stale entries
                    score = _score_entry(entry, query, branch, active_files, active_tags)
                    if score < 0.3:
                        continue
                    if entry.get("confidence", 0) < 5:
                        continue

                    scored.append((score, entry))
        except OSError:
            continue

    if not scored:
        return None

    scored.sort(key=lambda x: x[0], reverse=True)
    top = scored[:MAX_CONTEXT_LEARNINGS]

    lines = []
    for _, entry in top:
        key = entry.get("key", "?")
        insight = entry.get("insight", "")
        # Truncate insight to one line
        if len(insight) > 120:
            insight = insight[:117] + "..."
        lines.append(f"[learnings] {key}: {insight}")

        # Auto-reinforce when surfaced to context
        reinforce_learning(key, project_path)

    return "\n".join(lines)


# ── Compaction ─────────────────────────────────────────────────────

def _maybe_compact(lf: Path, key: str) -> None:
    """If >MAX_LEARNINGS_PER_KEY entries for a key, summarize oldest into one."""
    entries: List[Dict[str, Any]] = []
    try:
        with open(lf, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except OSError:
        return

    matching = [e for e in entries if e.get("key") == key]
    if len(matching) <= MAX_LEARNINGS_PER_KEY:
        return

    # Keep the 3 most recent, summarize the rest into one compact entry
    matching.sort(key=lambda e: e.get("ts", ""))
    to_keep = matching[-3:]
    to_summarize = matching[:-3]

    summary_insights = [e.get("insight", "") for e in to_summarize]
    compact_insight = " | ".join(
        i[:80] for i in summary_insights if i
    )

    compact_entry = {
        "ts": to_summarize[0].get("ts", ""),
        "last_used": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "type": "pattern",
        "key": f"{key}-compacted",
        "insight": f"[{len(to_summarize)} older entries compacted] {compact_insight}",
        "confidence": max(e.get("confidence", 5) for e in to_summarize) - 1,
        "source": "compacted",
        "files": [],
        "tags": list(set(t for e in to_summarize for t in e.get("tags", []))),
        "reinforced": 0,
        "version": "2.0-compact",
    }

    # Rebuild: keep non-matching + compacted + recent 3
    non_matching = [e for e in entries if e.get("key") != key]
    all_entries = non_matching + [compact_entry] + to_keep
    all_entries.sort(key=lambda e: e.get("ts", ""))

    try:
        with open(lf, "w", encoding="utf-8") as f:
            for entry in all_entries:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        logger.info(
            "Compacted %d learnings for key '%s' into 1 summary entry",
            len(to_summarize), key,
        )
    except OSError as e:
        logger.warning("Compaction failed for %s: %s", key, e)


# ── Maintenance ────────────────────────────────────────────────────

def get_learnings_count(project_path: Optional[Path] = None) -> int:
    lf = get_learnings_file(project_path)
    if not lf.exists():
        return 0
    try:
        return sum(1 for _ in open(lf, "r", encoding="utf-8"))
    except OSError:
        return 0


def prune_learnings(
    max_age_days: int = 60,
    min_confidence: int = 5,
    project_path: Optional[Path] = None,
) -> int:
    """Remove stale learnings. Entries older than max_age_days and below
    min_confidence are removed. High-confidence entries survive indefinitely.
    """
    lf = get_learnings_file(project_path)
    if not lf.exists():
        return 0

    kept = []
    pruned = 0
    now = time.time()

    try:
        with open(lf, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    kept.append(line)
                    continue

                ts = entry.get("ts", "")
                try:
                    entry_time = datetime.fromisoformat(
                        ts.replace("Z", "+00:00")
                    ).timestamp()
                except (ValueError, AttributeError):
                    kept.append(json.dumps(entry, ensure_ascii=False))
                    continue

                age_days = (now - entry_time) / 86400
                conf = entry.get("confidence", 0)
                reinforced = entry.get("reinforced", 0)

                # Keep if: recent, high confidence, or reinforced
                if age_days < max_age_days:
                    kept.append(json.dumps(entry, ensure_ascii=False))
                elif conf >= 8:
                    kept.append(json.dumps(entry, ensure_ascii=False))
                elif reinforced >= 3:
                    kept.append(json.dumps(entry, ensure_ascii=False))
                else:
                    pruned += 1

        with open(lf, "w", encoding="utf-8") as f:
            for line in kept:
                f.write(line + "\n")

        if pruned:
            logger.info("Pruned %d stale learnings from %s", pruned, lf)
        return pruned
    except OSError as e:
        logger.warning("Error pruning learnings: %s", e)
        return 0


def export_learnings_markdown(project_path: Optional[Path] = None) -> Optional[str]:
    lf = get_learnings_file(project_path)
    if not lf.exists():
        return None

    root = _find_project_root(project_path)
    lines = [f"# Learnings for {root.name}", ""]

    by_type: Dict[str, list] = {}
    try:
        with open(lf, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                etype = entry.get("type", "other")
                by_type.setdefault(etype, []).append(entry)
    except OSError:
        return None

    for etype, entries in by_type.items():
        lines.append(f"## {etype.title()} ({len(entries)})")
        lines.append("")
        for entry in sorted(entries, key=lambda e: e.get("ts", ""), reverse=True):
            score = _staleness_decay(entry.get("ts", ""))
            freshness = "🟢" if score > 0.7 else "🟡" if score > 0.3 else "🔴"
            lines.append(f"### {freshness} {entry.get('key', 'unknown')}")
            lines.append(f"> {entry.get('insight', 'No description')}")
            lines.append("")
            lines.append(f"- **Confidence:** {entry.get('confidence', 0)}/10")
            lines.append(f"- **Reinforced:** {entry.get('reinforced', 0)}x")
            lines.append(f"- **Relevance:** {score:.0%}")
            lines.append(f"- **Date:** {entry.get('ts', 'unknown')}")
            lines.append(f"- **Tags:** {', '.join(entry.get('tags', []))}")
            lines.append("")

    return "\n".join(lines)
