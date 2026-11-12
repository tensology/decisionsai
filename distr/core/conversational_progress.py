"""Conversational progress replies: a real percentage first, then plain speech.

The owner's format (12 Nov 2026): every Decisions agent thread reply and mid-task
update starts with an honest progress indicator, Codex style ("10% done."),
followed by complete sentences a person would say. Not clipped status lines,
and never the raw harness banner ("Verification incomplete: ... Agent report
(not independently verified):").

Percentages come from real state only:
- ``percent_from_steps`` divides completed steps by planned steps.
- ``development_turn_percent`` scores the four observable stages of a
  development turn (dispatched, agent finished, changes recorded, verification).
- A percentage the agent wrote itself is kept for unfinished work, but it can
  never claim 100% unless the run really completed.
100% is reserved for genuine completion. When progress cannot be known, the
helpers return ``None`` and the caller says so in words instead of guessing.
"""
from __future__ import annotations

import re
from typing import Any, Iterable, Mapping

_PERCENT_LEAD_RE = re.compile(
    r"^\s*(?:[*_]{1,2})?\s*(?P<pct>\d{1,3})\s*(?:%|percent\b)\s*"
    r"(?:done|complete(?:d)?|finished|through)?\s*(?:[*_]{1,2})?\s*[.:,;!\-–—]*\s*",
    re.IGNORECASE,
)
_SPOKEN_LEAD_RE = re.compile(
    r"^\s*(?P<words>[a-z][a-z -]*?)\s+percent\s+done\b\s*[.:,;!\-–—]*\s*",
    re.IGNORECASE,
)
_HARNESS_BANNER_RE = re.compile(
    r"^\s*Verification incomplete:\s*(?P<error>.*?)\s*\n\s*\n?\s*Agent report \(not independently verified\):\s*\n?(?P<report>.*)$",
    re.IGNORECASE | re.DOTALL,
)
_AUDIT_BANNER_RE = re.compile(
    r"^\s*Audit-only scope was violated:[^\n]*\n\s*\n?\s*Agent report:\s*\n?(?P<report>.*)$",
    re.IGNORECASE | re.DOTALL,
)

_ONES = (
    "zero one two three four five six seven eight nine ten eleven twelve thirteen "
    "fourteen fifteen sixteen seventeen eighteen nineteen"
).split()
_TENS = "_ _ twenty thirty forty fifty sixty seventy eighty ninety".split()


def clamp_percent(value: Any) -> int | None:
    try:
        number = int(round(float(value)))
    except (TypeError, ValueError):
        return None
    return max(0, min(100, number))


def percent_from_steps(done: Any, total: Any, *, complete: bool = False) -> int | None:
    """Completed steps over planned steps; 100 only when genuinely complete."""
    if complete:
        return 100
    try:
        done_n = float(done)
        total_n = float(total)
    except (TypeError, ValueError):
        return None
    if total_n <= 0:
        return None
    pct = int(100 * max(0.0, min(done_n, total_n)) / total_n)
    return min(pct, 99)


def development_turn_percent(
    *,
    agent_finished: bool,
    complete: bool,
    change_missing: bool = False,
    verification_required: bool = False,
    verification: Mapping[str, Any] | None = None,
) -> int:
    """Score the four observable stages of a development turn.

    1. dispatched to an agent (always true once we are reporting),
    2. the agent finished its run,
    3. the deliverable is there: false only when the harness flagged that an
       expected file change never happened (``completed_noop``),
    4. verification passed (or none was required); partial credit is the share
       of recorded checks that passed.
    """
    if complete:
        return 100
    stages = [1.0, 1.0 if agent_finished else 0.0]
    stages.append(1.0 if agent_finished and not change_missing else 0.0)
    report = dict(verification or {})
    status = str(report.get("status") or "").lower()
    if not agent_finished:
        stages.append(0.0)
    elif status == "passed" or not verification_required:
        stages.append(1.0)
    else:
        checks = [row for row in (report.get("checks") or []) if isinstance(row, Mapping)]
        passed = sum(1 for row in checks if str(row.get("status") or "") == "passed")
        stages.append(passed / len(checks) if checks else 0.0)
    return min(99, int(100 * sum(stages) / len(stages)))


def leading_percent(text: str) -> int | None:
    match = _PERCENT_LEAD_RE.match(str(text or ""))
    return clamp_percent(match.group("pct")) if match else None


def strip_leading_percent(text: str) -> str:
    raw = str(text or "")
    match = _PERCENT_LEAD_RE.match(raw)
    return raw[match.end():].lstrip() if match else raw.strip()


def format_progress(percent: int | None) -> str:
    pct = clamp_percent(percent)
    return "" if pct is None else f"{pct}% done."


def with_progress(text: str, percent: int | None, *, fallback: str = "") -> str:
    """Return ``"<pct>% done. <conversational text>"`` with one leading percent."""
    body = strip_leading_percent(text) or fallback.strip()
    lead = format_progress(percent)
    if not lead:
        return body
    return f"{lead} {body}".strip() if body else lead


def number_words(value: int) -> str:
    n = clamp_percent(value)
    if n is None:
        return str(value)
    if n == 100:
        return "one hundred"
    if n < 20:
        return _ONES[n]
    tens, ones = divmod(n, 10)
    return _TENS[tens] + (f" {_ONES[ones]}" if ones else "")


def _words_number(words: str) -> int | None:
    clean = re.sub(r"[-\s]+", " ", words.strip().lower())
    for n in range(101):
        if number_words(n) == clean:
            return n
    return None


def spoken_progress(text: str) -> str:
    """Speak a leading "40% done." as "Forty percent done." (house TTS rule)."""
    raw = str(text or "")
    pct = leading_percent(raw)
    if pct is None:
        return raw
    words = number_words(pct)
    return f"{words[0].upper()}{words[1:]} percent done. {strip_leading_percent(raw)}".strip()


def display_progress(text: str) -> str:
    """Inverse of ``spoken_progress`` for on-screen text: "Forty percent done." -> "40% done."."""
    raw = str(text or "")
    match = _SPOKEN_LEAD_RE.match(raw)
    if not match:
        return raw
    pct = _words_number(match.group("words"))
    if pct is None:
        return raw
    return f"{pct}% done. {raw[match.end():].lstrip()}".strip()


def _check_names(rows: Iterable[Mapping[str, Any]], status: str) -> list[str]:
    return [str(row.get("check") or "").strip() for row in rows if str(row.get("status") or "") == status and row.get("check")]


def verification_sentence(verification: Mapping[str, Any] | None) -> str:
    report = dict(verification or {})
    status = str(report.get("status") or "").lower()
    checks = [row for row in (report.get("checks") or []) if isinstance(row, Mapping)]
    failed = _check_names(checks, "failed")
    unknown = _check_names(checks, "unknown")
    missing = [str(item) for item in (report.get("missing") or []) if item]
    if status == "failed" and failed:
        named = ", ".join(f"`{name}`" for name in failed[:2])
        return f"{named} failed when it ran, so that check needs another pass"
    if status == "unknown" and unknown:
        return ("I couldn't read a clear pass or fail from the checks that ran, "
                "so they need to run again in a form that reports a real result")
    if status == "incomplete" and missing:
        return f"the {' and '.join(missing[:2])} check hasn't run and passed yet"
    return "none of the checks I can track have run and passed yet"


def conversational_development_reply(
    response: str,
    *,
    status: str,
    success: bool,
    change_expected: bool = False,
    changed_files: int = 0,
    verification: Mapping[str, Any] | None = None,
    audit_violation: bool = False,
) -> str:
    """Rewrite a finished development turn into percent-first conversational text."""
    raw = str(response or "").strip()
    report = raw
    lead_sentence = ""
    banner = _HARNESS_BANNER_RE.match(raw)
    audit = _AUDIT_BANNER_RE.match(raw)
    if banner:
        report = banner.group("report").strip()
        lead_sentence = f"The agent finished the work, but I can't call it done yet because {verification_sentence(verification)}."
    elif audit or audit_violation:
        report = (audit.group("report").strip() if audit else raw)
        lead_sentence = ("This was meant to be a read-only audit, but project files changed during the run, "
                         "so please look at those changes before anything else happens.")
    status = str(status or "").lower()
    complete = bool(success) and status == "completed"
    model_pct = leading_percent(report)
    report = strip_leading_percent(report)
    if complete:
        pct: int | None = 100
    else:
        pct = development_turn_percent(
            # A turn waiting on the user has not finished its work; it keeps the
            # agent's own percent if it gave one, else the conservative 25%.
            agent_finished=status in {"blocked", "completed_noop", "completed"} and not audit_violation,
            complete=False,
            change_missing=status == "completed_noop",
            verification_required=bool(change_expected),
            verification=verification,
        )
        if status == "waiting" and model_pct is not None:
            pct = min(model_pct, 99)
    if status == "completed_noop" and not lead_sentence:
        lead_sentence = "The agent finished, but no project files changed even though this asked for a change, so I'm not counting it as done."
    if report and lead_sentence:
        report = "Here's what the agent reported, which I haven't confirmed myself yet:\n" + report
    body = "\n\n".join(part for part in (lead_sentence, report) if part)
    if not body:
        body = "It's finished." if complete else "The agent stopped without saying anything about the result."
    return with_progress(body, pct)


def conversational_mid_task(summary: str, *, floor_percent: int, previous_percent: int | None = None) -> tuple[str, int]:
    """Shape a mid-task update; keep the agent's own percent when it gave one.

    Mid-task updates never claim 100% and never move backwards.
    """
    text = re.sub(r"\s+", " ", str(summary or "")).strip()
    own = leading_percent(text)
    pct = own if own is not None else floor_percent
    if previous_percent is not None:
        pct = max(pct, previous_percent)
    pct = max(0, min(99, pct))
    body = strip_leading_percent(text)
    if not body or body.lower().rstrip(".…") in {"working", "writing an update", "running the next project tool"}:
        body = "I'm still working on it and will tell you as soon as the next step lands."
    if body and body[-1] not in ".!?":
        body += "."
    return with_progress(body, pct), pct
