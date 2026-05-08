"""Helpers for attaching concise runtime evidence to ticket notes."""

from __future__ import annotations

import logging
import os
import re
from typing import Dict, List

from distr.core.paths import DB_DIR


_TS_PREFIX = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})")


def discover_log_path() -> str:
    """Resolve active app log path; fall back to DB_DIR/logs/decisions.log."""
    for name in ("distr", ""):
        logger_obj = logging.getLogger(name)
        for handler in logger_obj.handlers:
            candidate = getattr(handler, "baseFilename", None)
            if candidate and os.path.isfile(candidate):
                return os.path.abspath(candidate)
    return os.path.abspath(os.path.join(DB_DIR, "logs", "decisions.log"))


def _tail_lines(path: str, max_lines: int = 80, max_bytes: int = 256 * 1024) -> List[str]:
    if not os.path.isfile(path):
        return []
    with open(path, "r", encoding="utf-8", errors="replace") as handle:
        handle.seek(0, 2)
        size = handle.tell()
        if size <= max_bytes:
            handle.seek(0)
            text = handle.read()
        else:
            handle.seek(size - max_bytes)
            handle.readline()
            text = handle.read()
    lines = text.splitlines()
    if len(lines) > max_lines:
        lines = lines[-max_lines:]
    return lines


def collect_log_evidence(max_lines: int = 40) -> Dict[str, object]:
    """Return log source, timestamp window, and concise snippet."""
    path = discover_log_path()
    lines = _tail_lines(path, max_lines=max_lines)
    if not lines:
        return {
            "path": path,
            "window_start": "",
            "window_end": "",
            "snippet": [],
            "status": "no_log_file_yet" if not os.path.isfile(path) else "empty_log_file",
        }
    ts_matches = [_TS_PREFIX.search(line or "") for line in lines]
    timestamps = [m.group(1) for m in ts_matches if m]
    return {
        "path": path,
        "window_start": timestamps[0] if timestamps else "",
        "window_end": timestamps[-1] if timestamps else "",
        "snippet": lines[-10:],
        "status": "ok",
    }


def format_evidence_block() -> str:
    """Render a compact markdown evidence section for ticket descriptions."""
    evidence = collect_log_evidence()
    lines = [
        "Evidence:",
        f"- Log source: {evidence.get('path', '')}",
    ]
    status = evidence.get("status", "")
    if status == "ok":
        start = evidence.get("window_start", "")
        end = evidence.get("window_end", "")
        lines.append(f"- Log window: {start} -> {end}")
        lines.append("- Log snippet:")
        for line in evidence.get("snippet", []):
            lines.append(f"  - {line}")
    elif status == "empty_log_file":
        lines.append("- Log status: file exists but is empty.")
    else:
        lines.append("- Log status: no log file yet.")
    return "\n".join(lines).strip()
