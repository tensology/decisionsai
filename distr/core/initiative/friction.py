"""Friction scoring: what the agent notices about itself and the operator.

**Debug mode only, default off.** This is the Decisions platform critiquing
ITSELF - its own errors, repeated warnings, failed tool calls, and the places the
operator keeps fighting it (planning, development threads). Findings are written
as markdown into ``downloads/DecisionsAI/Tickets/``.

That destination is deliberately NOT the project's planning folder: planning
tickets describe work in the operator's product, these describe work on the
platform.

Restraint is the point, as everywhere else in Initiative:
- the same finding is not re-filed (a stable key + a window),
- findings are SCORED so the worst come first,
- there is a cap per pass,
- and a pass that finds nothing writes nothing.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

logger = logging.getLogger(__name__)

ENABLED_SETTING = "orchestrator_debug_enabled"
OUTPUT_DIR_NAME = "DecisionsAI/Tickets"
MAX_FINDINGS = 40
MAX_SCAN_BYTES = 4_000_000
DEDUP_WINDOW_S = 7 * 24 * 3600.0
SEEN_FILE = "orchestrator_debug_seen.json"

# How much each kind of signal matters. An unhandled exception is worse than a
# repeated warning; a failed tool call is worse than a retry.
SEVERITY = {
    "exception": 5,
    "error": 4,
    "tool_failure": 4,
    "repeated_warning": 2,
    "warning": 1,
}

_LINE = re.compile(
    r"^(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}),\d+ - (?P<logger>[\w\.\-]+) - "
    r"(?P<level>DEBUG|INFO|WARNING|ERROR|CRITICAL) - (?P<message>.*)$"
)


@dataclass
class Finding:
    """One thing worth fixing on the platform, with its evidence."""

    kind: str
    where: str          # logger/module the signal came from
    summary: str
    count: int = 1
    first_ts: str = ""
    last_ts: str = ""
    samples: list[str] = field(default_factory=list)
    severity: int = 1

    @property
    def score(self) -> float:
        """Severity times how often, with a small boost for volume."""
        return round(float(self.severity) * (1.0 + min(self.count - 1, 20) * 0.25), 3)

    @property
    def key(self) -> str:
        raw = f"{self.kind}|{self.where}|{self.summary}".lower()
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "kind": self.kind,
            "where": self.where,
            "summary": self.summary,
            "count": self.count,
            "score": self.score,
            "severity": self.severity,
            "first_ts": self.first_ts,
            "last_ts": self.last_ts,
            "samples": self.samples[:5],
        }

    def to_markdown(self) -> str:
        lines = [
            f"# {self.summary}",
            "",
            f"- **Where:** `{self.where}`",
            f"- **Kind:** {self.kind}",
            f"- **Seen:** {self.count}×  (first {self.first_ts or '?'}, last {self.last_ts or '?'})",
            f"- **Frustration score:** {self.score}",
            "",
            "## Evidence",
            "",
        ]
        lines.extend(f"- `{s}`" for s in self.samples[:5])
        lines += ["", "## Suggested fix", "", _suggestion_for(self), ""]
        return "\n".join(lines)


def _suggestion_for(finding: Finding) -> str:
    if finding.kind == "tool_failure":
        return (
            "A tool call failed repeatedly. Check the tool's error path - it should either "
            "recover or say why in one line, not fail the same way each time."
        )
    if finding.kind == "exception":
        return (
            "An unhandled exception reached the log. Find the call site, decide whether it is "
            "expected (handle it) or a bug (fix it), and make the failure legible either way."
        )
    if finding.kind == "repeated_warning":
        return (
            "The same warning keeps firing. Either it is expected and should be quieter, or it "
            "is pointing at something that is quietly broken."
        )
    return "Look at the evidence above and decide whether this is a bug or noise to silence."


# ------------------------------------------------------------------ scanning

def _normalise(message: str) -> str:
    """Collapse a log line to something groupable."""
    text = " ".join(str(message or "").split())
    text = re.sub(r"0x[0-9a-fA-F]+", "0xN", text)
    text = re.sub(r"\b\d{4,}\b", "N", text)
    text = re.sub(r"\b[0-9a-f]{8}-[0-9a-f-]{20,}\b", "UUID", text)
    return text[:200]


def scan_lines(lines: Iterable[str]) -> list[Finding]:
    """Group log lines into findings. Pure: takes text, returns findings."""
    findings: dict[tuple[str, str], Finding] = {}

    def record(kind: str, where: str, summary: str, ts: str, sample: str) -> None:
        key = (kind, f"{where}|{summary}")
        existing = findings.get(key)
        if existing is None:
            findings[key] = Finding(
                kind=kind, where=where, summary=summary,
                severity=SEVERITY.get(kind, 1), first_ts=ts, last_ts=ts, samples=[sample],
            )
            return
        existing.count += 1
        existing.last_ts = ts
        if sample not in existing.samples and len(existing.samples) < 5:
            existing.samples.append(sample)

    for raw in lines:
        match = _LINE.match(str(raw).rstrip("\n"))
        if not match:
            continue
        level = match.group("level")
        where = match.group("logger")
        message = match.group("message")
        ts = match.group("ts")

        # A failed tool call is logged at INFO by distr.agent.activity, so the
        # level filter must not run before this check.
        if "tool=" in message and "status=failed" in message:
            record("tool_failure", where, _normalise(message), ts, message)
            continue
        if level not in ("WARNING", "ERROR", "CRITICAL"):
            continue

        if "Traceback (most recent call last)" in message or level == "CRITICAL":
            record("exception", where, _normalise(message), ts, message)
        elif level == "ERROR":
            record("error", where, _normalise(message), ts, message)
        else:
            record("warning", where, _normalise(message), ts, message)

    # A warning that repeats is more interesting than a one-off.
    for finding in findings.values():
        if finding.kind == "warning" and finding.count >= 3:
            finding.kind = "repeated_warning"
            finding.severity = SEVERITY["repeated_warning"]

    return sorted(findings.values(), key=lambda f: f.score, reverse=True)


def log_path() -> Path:
    try:
        from distr.core.paths import DB_DIR

        return Path(DB_DIR) / "logs" / "decisions.log"
    except Exception:
        return Path.home() / ".decisions" / "logs" / "decisions.log"


def collect(*, path: Path | None = None, max_bytes: int = MAX_SCAN_BYTES) -> list[Finding]:
    """Read the log and score what it contains. Never raises."""
    target = path or log_path()
    try:
        if not target.is_file():
            return []
        size = target.stat().st_size
        with target.open("r", encoding="utf-8", errors="replace") as handle:
            if size > max_bytes:
                handle.seek(size - max_bytes)
                handle.readline()  # drop the partial line
            return scan_lines(handle)
    except (OSError, ValueError):
        logger.debug("friction: could not read %s", target, exc_info=True)
        return []


# ------------------------------------------------------------------ output

def output_dir() -> Path:
    """``downloads/DecisionsAI/Tickets/`` - the PLATFORM notes, not a project's."""
    override = (os.environ.get("DECISIONS_DEBUG_TICKETS_DIR") or "").strip()
    if override:
        return Path(override).expanduser()
    return Path.home() / "Downloads" / OUTPUT_DIR_NAME


def _seen_path() -> Path:
    try:
        from distr.core.paths import DB_DIR

        return Path(DB_DIR) / SEEN_FILE
    except Exception:
        return Path.home() / ".decisions" / SEEN_FILE


def _load_seen() -> dict[str, float]:
    try:
        data = json.loads(_seen_path().read_text(encoding="utf-8"))
        return {str(k): float(v) for k, v in data.items()} if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_seen(seen: dict[str, float]) -> None:
    try:
        path = _seen_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(seen), encoding="utf-8")
    except (OSError, ValueError):
        logger.debug("friction: could not persist the seen list", exc_info=True)


def _slug(value: str) -> str:
    cleaned = "".join(ch if (ch.isalnum() or ch in "-_") else "-" for ch in str(value or "").lower())
    while "--" in cleaned:
        cleaned = cleaned.replace("--", "-")
    return cleaned.strip("-")[:60] or "finding"


def file_findings(
    findings: list[Finding],
    *,
    directory: Path | None = None,
    now: float | None = None,
    window_s: float = DEDUP_WINDOW_S,
) -> dict[str, Any]:
    """Write each new finding as a markdown file. Returns what was written.

    A finding already filed inside the window is skipped - the platform should not
    be told about the same wart every pass.
    """
    now = time.time() if now is None else now
    target = directory or output_dir()
    seen = _load_seen()
    fresh = [f for f in findings if not (seen.get(f.key) and now - seen[f.key] < window_s)]

    written: list[str] = []
    try:
        target.mkdir(parents=True, exist_ok=True)
    except (OSError, ValueError) as exc:
        return {"written": [], "skipped": len(findings) - len(fresh), "error": str(exc)}

    for finding in fresh[:MAX_FINDINGS]:
        name = f"{finding.kind}-{_slug(finding.summary)}.md"
        try:
            (target / name).write_text(finding.to_markdown(), encoding="utf-8")
        except (OSError, ValueError):
            logger.debug("friction: could not write %s", name, exc_info=True)
            continue
        seen[finding.key] = now
        written.append(name)

    if written:
        _save_seen({k: v for k, v in seen.items() if now - v < window_s * 4})
    return {
        "written": written,
        "skipped": len(findings) - len(fresh),
        "dir": str(target),
    }


def run(*, settings: dict[str, Any] | None = None, path: Path | None = None, directory: Path | None = None) -> dict[str, Any]:
    """One debug pass. A no-op unless the setting is on. Never raises."""
    if settings is None:
        try:
            from distr.core.settings import load_settings_from_db

            settings = load_settings_from_db()
        except Exception:
            return {"enabled": False, "written": [], "skipped": 0, "findings": 0}

    if not bool((settings or {}).get(ENABLED_SETTING)):
        return {"enabled": False, "written": [], "skipped": 0, "findings": 0}

    findings = collect(path=path)
    result = file_findings(findings, directory=directory)
    if result.get("written"):
        logger.info("orchestrator-debug: filed %d platform finding(s)", len(result["written"]))
    return {"enabled": True, "findings": len(findings), **result}
