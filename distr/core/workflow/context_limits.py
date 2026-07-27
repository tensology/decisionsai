"""Shared limits for workflow step context assembly (Stage 0 BUG-2)."""

from __future__ import annotations

import re
from typing import List

# Max characters from each prior step's agent_response injected into the next step's prompt.
PRIOR_STEP_RESULT_MAX_CHARS = 1000

# Workflow run summaries shown to the agent / bridge: keep more text when a step failed.
STEP_SUMMARY_FAILED_MAX_CHARS = 6000

_TRUNCATION_MARKER = "\n\n[... verbose worker output omitted; full transcript remains in the execution session ...]\n\n"

_LINE_FILE = re.compile(
    r"^(?:\s*)([^\s:]+\.(?:py|js|ts|tsx|md|txt|json|yaml|yml|csv|log|pdf|png|jpe?g|webp|ogg|mp3|m4a|opus|mp4|3gp|bin))\s*$",
    re.MULTILINE | re.IGNORECASE,
)
_ABS_PATH = re.compile(r"(/(?:[\w.-]+/)+[\w.-]+\.\w{1,8})\b")
_WHATSAPP_MEDIA = re.compile(r"\b(whatsapp_media/[\w./-]+\.\w{1,8})\b", re.IGNORECASE)


def truncate_step_result(text: str) -> str:
    """Trim prior-step text to PRIOR_STEP_RESULT_MAX_CHARS."""
    if not text:
        return ""
    s = text.strip()
    if len(s) <= PRIOR_STEP_RESULT_MAX_CHARS:
        return s
    return s[:PRIOR_STEP_RESULT_MAX_CHARS]


def truncate_step_summary(text: str, status: str) -> str:
    """Bound text stored on workflow run summaries; failures retain more for diagnostics."""
    if not text:
        return ""
    s = text.strip()
    st = (status or "").strip().lower()
    limit = (
        PRIOR_STEP_RESULT_MAX_CHARS
        if st in ("completed", "passed")
        else STEP_SUMMARY_FAILED_MAX_CHARS
    )
    if len(s) <= limit:
        return s
    return s[:limit]


def compact_execution_result(text: str, *, max_chars: int = 6000) -> str:
    """Bound verbose worker output while preserving its final contract/report.

    Coding workers commonly put required handoff fields, blockers, test results,
    and the terminal status at the end of their answer. A prefix-only slice can
    therefore turn a valid result into a false validation failure and trigger a
    second model run. Keep both the opening evidence and the conclusion; the
    complete transcript remains available on ``ProjectExecutionSession``.
    """
    value = str(text or "").strip()
    if max_chars <= 0 or len(value) <= max_chars:
        return value
    marker = _TRUNCATION_MARKER
    available = max_chars - len(marker)
    if available <= 1:
        return value[:max_chars]
    tail_chars = min(3000, available // 2)
    head_chars = available - tail_chars
    return value[:head_chars].rstrip() + marker + value[-tail_chars:].lstrip()


def extract_artifact_paths_from_result(text: str, *, max_paths: int = 12) -> List[str]:
    """Pull plausible file paths from step output for downstream context."""
    if not text:
        return []
    candidates: list[str] = []
    seen: set[str] = set()

    def add(p: str) -> None:
        p = p.strip().strip("`\"'")
        if len(p) < 3 or p in seen:
            return
        if p.startswith("http://") or p.startswith("https://"):
            return
        seen.add(p)
        candidates.append(p)

    for m in _LINE_FILE.finditer(text):
        add(m.group(1))
        if len(candidates) >= max_paths:
            return candidates
    for m in _ABS_PATH.finditer(text):
        add(m.group(1))
        if len(candidates) >= max_paths:
            return candidates
    for m in _WHATSAPP_MEDIA.finditer(text):
        add(m.group(1))
        if len(candidates) >= max_paths:
            return candidates
    return candidates
