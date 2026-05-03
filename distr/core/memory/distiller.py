"""R9 — Distill EVENTS.md into MEMORY.md with snapshot/backup safety.

Never clears EVENTS.md unless MEMORY.md append succeeds. LLM failures leave EVENTS intact.
"""

from __future__ import annotations

import logging
import re
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from distr.core.memory.files import (
    DEFAULT_EVENTS_BODY,
    append_markdown_section,
    atomic_write_text,
    ensure_memory_files,
    memory_paths,
)

logger = logging.getLogger(__name__)

_distill_lock = threading.Lock()

MAX_EVENTS_CHARS_FOR_LLM = 120_000

DISTILL_SYSTEM = """You turn a raw EVENTS log into durable long-term memories for MEMORY.md.
Output markdown only: one or more sections separated by a line containing exactly --- between sections.
Each section should be a concise fact, preference, or decision worth recalling later.
No title line like "# Memory". No JSON. No preamble or explanation outside the sections."""

DISTILL_USER_TEMPLATE = """Distill the following event log into MEMORY.md sections (--- separated).
Omit noise, duplicates, and transient chatter. Preserve names, dates, and commitments when important.

--- EVENT LOG ---
{events}
--- END ---"""


@dataclass
class DistillOutcome:
    """Result of one distillation attempt."""

    ok: bool
    skipped: bool = False
    reason: str = ""
    backup_path: Path | None = None


def _litellm_model(provider: str, model: str, settings: dict) -> str:
    """Map provider + model to a litellm model string (aligned with initiative service)."""
    p = provider.strip().lower()
    if p == "ollama":
        base = settings.get("ollama_url", "http://localhost:11434").rstrip("/")
        import os

        os.environ.setdefault("OLLAMA_API_BASE", base)
        return f"ollama/{model}" if model else "ollama/llama3.2"
    if p == "openai":
        return model or "gpt-4o-mini"
    if p == "anthropic":
        return model or "claude-3-5-sonnet-20241022"
    if p == "groq":
        return f"groq/{model}" if model else "groq/llama-3.1-70b-versatile"
    if p == "openrouter":
        return f"openrouter/{model}" if model else "openrouter/openai/gpt-4o-mini"
    if p in ("kilocode", "kilo"):
        return model or "kilocode/kilocode"
    if p == "gemini":
        return f"gemini/{model}" if model else "gemini/gemini-2.5-flash"
    return f"ollama/{model}" if model else "ollama/llama3.2"


def events_effectively_empty(raw: str) -> bool:
    """True if EVENTS.md has no user content beyond the default template."""
    s = (raw or "").strip()
    if not s:
        return True
    # Remove default scaffold; compare normalized remainder.
    stripped = s.replace(DEFAULT_EVENTS_BODY.strip(), "").strip()
    if not stripped:
        return True
    lines = [
        ln.strip()
        for ln in stripped.splitlines()
        if ln.strip() and not ln.strip().lower().startswith("# events")
    ]
    return len(lines) == 0


def _strip_code_fences(text: str) -> str:
    t = text.strip()
    if not t.startswith("```"):
        return t
    lines = t.splitlines()
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines).strip()


def _completion_with_litellm(events_snippet: str, settings: dict) -> str:
    import litellm

    candidates: list[tuple[str, str]] = []
    conv_p = (settings.get("conversational_llm_provider") or "").strip().lower()
    conv_m = (settings.get("conversational_llm_model") or "").strip()
    ag_p = (settings.get("agent_provider") or "").strip().lower()
    ag_m = (settings.get("agent_model") or "").strip()
    if conv_p and conv_m:
        candidates.append((conv_p, conv_m))
    if ag_p and ag_m and (ag_p, ag_m) != (conv_p, conv_m):
        candidates.append((ag_p, ag_m))
    candidates.append(("ollama", "llama3.2"))

    user = DISTILL_USER_TEMPLATE.format(events=events_snippet)
    messages = [
        {"role": "system", "content": DISTILL_SYSTEM},
        {"role": "user", "content": user},
    ]
    last_err: Exception | None = None
    for provider, model in candidates:
        litellm_model = _litellm_model(provider, model, settings)
        try:
            response = litellm.completion(
                model=litellm_model,
                messages=messages,
                max_tokens=2048,
                temperature=0.3,
            )
            return (response.choices[0].message.content or "").strip()
        except Exception as e:
            last_err = e
            logger.warning(
                "memory distillation: LLM failed for %s/%s: %s", provider, model, e
            )
    if last_err:
        raise last_err
    raise RuntimeError("memory distillation: no LLM candidates")


def run_memory_distillation(
    *,
    root: Path | None = None,
    settings: dict | None = None,
    llm_distill: Callable[[str], str] | None = None,
) -> DistillOutcome:
    """Snapshot EVENTS → backup, distill, append MEMORY, clear EVENTS only on success.

    *llm_distill* — optional injector for tests (takes events text, returns markdown).
    """
    with _distill_lock:
        base = ensure_memory_files(root=root)
        paths = memory_paths(root)
        events_path = paths["events"]
        raw_events = events_path.read_text(encoding="utf-8", errors="replace")
        if events_effectively_empty(raw_events):
            return DistillOutcome(ok=True, skipped=True, reason="events_empty")

        if settings is None:
            from distr.core.utils import load_settings_from_db

            settings = load_settings_from_db()

        backup_dir = base / "distill_backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        backup_path = backup_dir / f"EVENTS_{ts}.md"
        atomic_write_text(backup_path, raw_events)

        snippet = raw_events.strip()
        if len(snippet) > MAX_EVENTS_CHARS_FOR_LLM:
            snippet = snippet[:MAX_EVENTS_CHARS_FOR_LLM] + "\n\n[… truncated for distillation …]"

        try:
            if llm_distill is not None:
                distilled = llm_distill(snippet).strip()
            else:
                distilled = _completion_with_litellm(snippet, settings).strip()
        except Exception:
            logger.exception(
                "memory distillation: LLM failed — EVENTS.md left unchanged (%s)",
                backup_path,
            )
            return DistillOutcome(ok=False, reason="llm_failed", backup_path=backup_path)

        distilled = _strip_code_fences(distilled)
        distilled = re.sub(r"\A#+\s*memory\s*$", "", distilled, flags=re.IGNORECASE | re.MULTILINE).strip()
        if not distilled:
            logger.warning(
                "memory distillation: empty model output — EVENTS.md left unchanged (%s)",
                backup_path,
            )
            return DistillOutcome(ok=False, reason="empty_model_output", backup_path=backup_path)

        try:
            append_markdown_section("memory", distilled, root=root)
        except Exception:
            logger.exception(
                "memory distillation: MEMORY append failed — EVENTS.md left unchanged (%s)",
                backup_path,
            )
            return DistillOutcome(ok=False, reason="memory_append_failed", backup_path=backup_path)

        atomic_write_text(events_path, DEFAULT_EVENTS_BODY.strip() + "\n")

        try:
            from distr.core.events import MEMORY_DISTILLED, get_event_bus

            get_event_bus().publish(MEMORY_DISTILLED, {"backup_path": str(backup_path)})
        except Exception:
            logger.debug("memory distillation: event publish failed", exc_info=True)

        logger.info("memory distillation: success, backup=%s", backup_path)
        return DistillOutcome(ok=True, reason="distilled", backup_path=backup_path)
