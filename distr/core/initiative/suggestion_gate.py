"""Decide which assessed suggestions are worth saying, and which are worth interrupting for.

Pure by design: no clock, no I/O, no model. The caller supplies the candidates,
the remaining budget and a ``seen`` predicate, so the restraint rules can be
tested directly. This module is where "not annoying" actually lives — the
assessor only decides whether an idea is *good*, this decides whether it is
worth *saying* and whether it is worth *interrupting*.

Fail-closed: a malformed candidate is dropped, never guessed at.
"""

from __future__ import annotations

from typing import Any, Callable, Iterable

# Below this, an idea is not worth saying at all. Silence is the default.
MIN_SPEAK_CONFIDENCE = 0.5

# At most one interruption per cycle. Being interrupted twice in one pass is
# never acceptable, however good both suggestions are.
MAX_INTERRUPTS_PER_CYCLE = 1


def _confidence(candidate: Any) -> float:
    try:
        value = float(candidate.get("confidence"))
    except (AttributeError, TypeError, ValueError):
        return -1.0
    if value != value:  # NaN
        return -1.0
    return min(max(value, 0.0), 1.0)


def _text(candidate: Any) -> str:
    try:
        return str(candidate.get("text") or "").strip()
    except Exception:
        return ""


def suggestion_key(candidate: Any) -> str:
    """Stable identity for dedup: kind + normalised text."""
    kind = ""
    try:
        kind = str(candidate.get("kind") or "").strip().lower()
    except Exception:
        kind = ""
    text = " ".join(_text(candidate).lower().split())
    return f"{kind}:{text[:160]}"


def gate(
    candidates: Iterable[Any],
    *,
    interrupt_threshold: float,
    budget_remaining: int,
    seen: Callable[[str], bool] | None = None,
    min_speak: float = MIN_SPEAK_CONFIDENCE,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Split assessed candidates into ``(inbox, interrupts)``.

    - confidence below ``min_speak`` -> dropped (silence)
    - already seen (per ``seen``)     -> dropped
    - confidence >= ``min_speak``     -> inbox
    - confidence >= ``interrupt_threshold`` and budget remains -> interrupt,
      capped at ``MAX_INTERRUPTS_PER_CYCLE`` per call

    Both lists are highest-confidence first. Candidates are never mutated.
    """
    try:
        threshold = float(interrupt_threshold)
    except (TypeError, ValueError):
        threshold = 1.0  # unusable threshold -> never interrupt
    try:
        budget = max(0, int(budget_remaining))
    except (TypeError, ValueError):
        budget = 0

    scored: list[tuple[float, dict[str, Any]]] = []
    for candidate in candidates or []:
        if not isinstance(candidate, dict):
            continue
        confidence = _confidence(candidate)
        if confidence < 0.0:
            continue
        text = _text(candidate)
        if not text:
            continue
        key = suggestion_key(candidate)
        if seen is not None:
            try:
                if seen(key):
                    continue
            except Exception:
                # A broken predicate must not let an unvetted suggestion through.
                continue
        entry = dict(candidate)
        entry["confidence"] = confidence
        entry["key"] = key
        scored.append((confidence, entry))

    scored.sort(key=lambda pair: pair[0], reverse=True)

    inbox: list[dict[str, Any]] = []
    interrupts: list[dict[str, Any]] = []
    for confidence, entry in scored:
        if confidence < min_speak:
            continue
        inbox.append(entry)
        if (
            confidence >= threshold
            and budget > 0
            and len(interrupts) < MAX_INTERRUPTS_PER_CYCLE
        ):
            interrupts.append(entry)
            budget -= 1

    return inbox, interrupts
