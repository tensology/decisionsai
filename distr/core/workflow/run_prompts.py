"""Plain-language run prompts, status lines and recovery/stall rules.

One place for every user-visible wait/interrupt text so Paul hears the same
shape each time: project + ticket, what happened, what Continue does, the safe
default, and whether he must act. Pure functions only (no DB) so they are unit
tested; callers persist the results.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

# ---------------------------------------------------------------- labels


def run_label(run_data: Optional[dict] = None, *, project: str = "", ticket: str = "",
              ticket_id: Any = None) -> str:
    """'JackCat PROJ-178-003' style label; falls back gracefully."""
    data = run_data if isinstance(run_data, dict) else {}
    project = str(project or data.get("project_name") or "").strip()
    title = str(ticket or data.get("ticket_title") or "").strip()
    number = str(data.get("ticket_key") or data.get("ticket_external_id")
                 or (f"#{ticket_id or data.get('ticket_id')}" if ticket_id or data.get('ticket_id') else "")).strip()
    ticket = " ".join(part for part in (number, title) if part)
    if len(ticket) > 60:
        ticket = ticket[:57].rstrip() + "..."
    label = " ".join(part for part in (project, ticket) if part)
    return label or "This run"


# ---------------------------------------------------------------- prompts

WAIT_TEMPLATES: dict[str, dict[str, str]] = {
    "checkpoint": {
        "continue": "Continue applies your feedback and follows the step's configured routing.",
        "default": "Safe default if you do nothing: this run stays paused.",
        "act": "Review the step result before continuing.",
    },
    "restart_recovery": {
        "continue": "Continue re-runs the same step from its saved context. No step is skipped and no approval is given.",
        "default": "Safe default if you do nothing: this run stays paused.",
        "act": "Review the recovery reason before continuing.",
    },
    "provider_failure": {
        "continue": "Continue retries the same configured harness for this step.",
        "default": "Safe default if you do nothing: this run stays paused on its current route.",
        "act": "Your choice is needed.",
    },
    "human_question": {
        "continue": "Continue sends your answer to the worker and carries on.",
        "default": "Safe default if you do nothing: the worker stays paused awaiting your answer.",
        "act": "Your answer is needed.",
    },
}


def wait_prompt(kind: str, what_happened: str, *, label: str = "", detail: str = "",
                continue_text: str = "", default_text: str = "") -> str:
    """Compose a wait prompt: what happened, what Continue does, safe default."""
    tmpl = WAIT_TEMPLATES.get(kind) or WAIT_TEMPLATES["human_question"]
    happened = str(what_happened or "").strip().rstrip(".")
    parts = [f"{label or 'This run'}: {happened}."]
    if detail:
        parts.append(str(detail).strip())
    parts.append(continue_text or tmpl["continue"])
    parts.append(default_text or tmpl["default"])
    parts.append(tmpl["act"])
    return " ".join(p for p in parts if p)


def restart_prompt(label: str = "", *, step_name: str = "", attempts: int = 0, limit: int = 0) -> str:
    step = f" during step {step_name}" if step_name else ""
    detail = ""
    if limit and attempts >= limit:
        detail = f"It already resumed itself {attempts} time(s), so it now waits for you."
    return wait_prompt("restart_recovery", f"Decisions restarted{step}, so that step was interrupted",
                       label=label, detail=detail)


def budget_summary(budget: dict) -> str:
    """'Run used 41 of 80 turns and about $0.02.' plus tokens as a detail."""
    b = budget if isinstance(budget, dict) else {}
    turns_used = int(b.get("turns_used") or 0)
    max_turns = int(b.get("max_turns") or 0)
    cost = float(b.get("estimated_cost_usd") or 0.0)
    tokens_used = int(b.get("tokens_used") or 0)
    max_tokens = int(b.get("max_tokens") or 0)
    dollars = f"${cost:.2f}" if cost >= 0.01 else ("under $0.01" if cost > 0 else "$0.00")
    about = "" if dollars.startswith("under") else "about "
    return (f"Run used {turns_used} of {max_turns} turns and {about}{dollars}. "
            f"Tokens: {tokens_used:,} of {max_tokens:,}.")




def provider_failure_prompt(question: str, *, label: str = "") -> str:
    """Wrap an existing provider/route question; idempotent."""
    text = str(question or "").strip()
    if "Safe default" in text:
        return text
    return wait_prompt("provider_failure", "the worker route needs a decision", label=label, detail=text)


def human_question_prompt(question: str, *, label: str = "") -> str:
    text = str(question or "").strip()
    if "Safe default" in text:
        return text
    return wait_prompt("human_question", "the worker has a question for you", label=label, detail=text)


# ---------------------------------------------------------------- provider swaps


_PLACEHOLDER_APPROVERS = {"", "unknown", "unknown approver", "operator via route approval", "system fallback",
                          "legacy recorded approval", "operator", "operator via interaction", "none", "null"}


def real_approver(value: Any) -> str:
    """The approver identity, or '' when it is missing or a placeholder."""
    text = str(value or "").strip()
    return "" if text.lower() in _PLACEHOLDER_APPROVERS else text[:200]


def web_approver(named: Any = "", client_host: str = "") -> str:
    """Identity for a web-UI approval: an explicit name, else the local OS user."""
    import getpass

    text = real_approver(named)
    if text:
        return f"{text} via web UI"
    try:
        user = getpass.getuser()
    except Exception:
        user = ""
    if not user:
        return ""
    return f"web UI user {user}" + (f" from {client_host}" if client_host else "")


def provider_unready_reason(preflight: Any) -> str:
    pre = preflight if isinstance(preflight, dict) else {}
    return str(pre.get("failure_reason") or pre.get("reason") or pre.get("detail") or pre.get("error")
               or pre.get("message") or "").strip()[:300]


def provider_swap_record(*, failed_backend: str, failed_model: str, to_backend: str, to_model: str,
                         preflight: Optional[dict] = None, approved_by: str = "",
                         at: Optional[datetime] = None) -> dict:
    pre = preflight if isinstance(preflight, dict) else {}
    why = str(
        pre.get("failure_reason") or pre.get("reason") or pre.get("detail") or pre.get("error")
        or pre.get("message") or "readiness check failed"
    ).strip()[:300]
    approver = real_approver(approved_by) or "unknown approver"
    reason = (f"{failed_backend}/{failed_model} was not ready ({why}); "
              f"switched to {to_backend}/{to_model}, approved by {approver}.")
    return {
        "from_backend": failed_backend,
        "from_model": failed_model,
        "to_backend": to_backend,
        "to_model": to_model,
        "unready_reason": why,
        "approved_by": approver,
        "approved_at": (at or datetime.now(timezone.utc)).isoformat(),
        "rationale": reason,
    }


# ---------------------------------------------------------------- status line


def _ago_minutes(start: Optional[datetime], now: datetime) -> Optional[int]:
    if not start:
        return None
    return max(0, int((now - start).total_seconds() // 60))


def status_line(*, label: str, status: str, now: datetime, started_at: Optional[datetime] = None,
                backend: str = "", model: str = "", step_name: str = "", waiting_kind: str = "",
                tool_calls: int = 0, files_touched: int = 0, last_activity_at: Optional[datetime] = None,
                stalled: bool = False) -> str:
    """One spoken line, e.g. 'JackCat PROJ-178-003: heavy session on Codex, 12 min in, no files changed yet.'"""
    mins = _ago_minutes(started_at, now)
    if status == "waiting":
        need = "needs you" if waiting_kind not in {"restart_recovery"} else "paused after restart"
        what = f"waiting ({(waiting_kind or 'checkpoint').replace('_', ' ')}), {need}"
        return f"{label}: {what}."
    worker = (backend or "worker").strip()
    if worker:
        worker = worker[:1].upper() + worker[1:]
    bits = [f"{'session' if not step_name else step_name} on {worker}"]
    if mins is not None:
        bits.append(f"{mins} min in")
    if tool_calls:
        bits.append(f"{tool_calls} tool call{'s' if tool_calls != 1 else ''}")
    bits.append(f"{files_touched} file{'s' if files_touched != 1 else ''} changed" if files_touched
                else "no files changed yet")
    idle = _ago_minutes(last_activity_at, now)
    if stalled:
        bits.append(f"STALLED, no activity for {idle if idle is not None else '?'} min; you may want to check it")
    elif idle is not None and idle >= 2:
        bits.append(f"last activity {idle} min ago")
    return f"{label}: " + ", ".join(bits) + "."


# ---------------------------------------------------------------- stall check

DEFAULT_STALL_MINUTES = 10


def is_stalled(*, status: str, last_activity_at: Optional[datetime], started_at: Optional[datetime],
               now: datetime, threshold_minutes: float = DEFAULT_STALL_MINUTES) -> bool:
    if str(status or "") != "running":
        return False
    ref = last_activity_at or started_at
    if ref is None:
        return False
    return (now - ref).total_seconds() > float(threshold_minutes) * 60.0


# ---------------------------------------------------------------- restart auto-resume

DEFAULT_MAX_AUTO_RESUMES = 2


def auto_resume_decision(run_data: dict, step_id: int, *, max_resumes: int = DEFAULT_MAX_AUTO_RESUMES,
                         enabled: bool = True) -> tuple[bool, int]:
    """Return (should_auto_resume, attempts_so_far_for_this_step).

    Only plain mid-step restarts qualify: never when the run was already
    waiting on a human gate (callers only pass status=='running' runs).
    """
    rec = run_data.get("restart_recovery") if isinstance(run_data, dict) else None
    counts = (rec or {}).get("auto_resume_counts") if isinstance(rec, dict) else None
    attempts = int((counts or {}).get(str(int(step_id))) or 0)
    if not enabled or max_resumes <= 0:
        return False, attempts
    return attempts < max_resumes, attempts
