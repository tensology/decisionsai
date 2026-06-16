"""
Day / week / month planners (R4).

Dedicated system prompts are chosen *before* the LLM call (not the initiative JSON rubric prompt).
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timedelta
from typing import Any

from distr.core.initiative.context import ContextBundle
from distr.core.initiative.scheduler import default_local_tz

logger = logging.getLogger(__name__)

_SCOPE_BY_TASK_NAME = {
    "morning brief": "morning",
    "day planner": "day",
    "week planner": "week",
    "month planner": "month",
}


def planner_scope_for_task_name(name: str) -> str | None:
    """Return scope string or None if *name* is not a built-in planner task."""
    if not name:
        return None
    return _SCOPE_BY_TASK_NAME.get(str(name).strip().lower())


def _litellm_model(provider: str, model: str, settings: dict) -> str:
    """Map provider + model to a litellm model string (same rules as initiative.service)."""
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


def _llm_candidates(settings: dict) -> list[tuple[str, str]]:
    from distr.core.llm_factory import resolve_llm_candidates

    return resolve_llm_candidates(settings)


def _completion_options(litellm_model: str) -> dict[str, Any]:
    """Return provider-safe LiteLLM options for planner calls."""
    model_name = (litellm_model or "").split("/")[-1].lower()
    options: dict[str, Any] = {"max_tokens": 3072}
    if not model_name.startswith(("o1", "o3", "o4")):
        options["temperature"] = 0.45
    return options


def _clip_text(value: Any, limit: int = 140) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rsplit(" ", 1)[0].rstrip() + "…"


_USER_INSTRUCTION_RE = re.compile(r"^user instruction:\s*", re.IGNORECASE)


def _normalize_ticket_title(raw: Any) -> str:
    text = _USER_INSTRUCTION_RE.sub("", str(raw or "")).strip()
    text = re.sub(r"\s+", " ", text)
    return _clip_text(text, 120) or "the next useful outcome"


def extract_markdown_section(markdown: str, heading_prefix: str) -> str:
    """Return bullet lines under the first ## heading that starts with *heading_prefix*."""
    if not markdown or not heading_prefix:
        return ""
    lines = str(markdown).splitlines()
    collecting = False
    body: list[str] = []
    prefix_lower = heading_prefix.strip().lower()
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("##"):
            heading = re.sub(r"^#+\s*", "", stripped).strip()
            if collecting:
                break
            if heading.lower().startswith(prefix_lower):
                collecting = True
            continue
        if collecting and stripped:
            body.append(stripped)
    return "\n".join(body).strip()


def _first_nonempty(*values: Any, default: str = "") -> str:
    for value in values:
        text = _clip_text(value, 180)
        if text:
            return text
    return default


def _plural(count: int, singular: str, plural: str | None = None) -> str:
    return singular if int(count or 0) == 1 else (plural or f"{singular}s")


def _count_phrase(count: int, singular: str, plural: str | None = None) -> str:
    return f"{int(count or 0)} {_plural(int(count or 0), singular, plural)}"


def _ticket_title(ticket: dict[str, Any]) -> str:
    return _normalize_ticket_title(
        _first_nonempty(
            ticket.get("title"),
            ticket.get("name"),
            ticket.get("description_preview"),
            ticket.get("description"),
            default="the next useful outcome",
        )
    )


def _ticket_why(ticket: dict[str, Any]) -> str:
    title = _ticket_title(ticket).lower()
    text = _first_nonempty(
        ticket.get("description_preview"),
        ticket.get("description"),
        ticket.get("source_label"),
        default="",
    )
    if not text:
        return ""
    normalized = _normalize_ticket_title(text).lower()
    if normalized == title or normalized.startswith(title[:40]) or title.startswith(normalized[:40]):
        return ""
    return f" — {_clip_text(text, 90)}"


def _append_period_phrase(title: str, period: str) -> str:
    clean = str(title or "").rstrip(".")
    lowered = clean.lower()
    if period == "today" and lowered.endswith("today"):
        return clean
    if period == "this week" and lowered.endswith("this week"):
        return clean
    return f"{clean} {period}"


def _finish_outcome_line(board_name: str, ticket: dict[str, Any], period: str) -> str:
    title = _ticket_title(ticket)
    why = _ticket_why(ticket)
    lowered = title.lower()
    title_with_period = _append_period_phrase(title, period)
    if lowered.startswith(("finish ", "complete ", "send ", "review ", "fix ", "ship ", "prepare ")):
        return f"{board_name}: {title_with_period}{why}."
    return f"{board_name}: finish {title_with_period}{why}."


def _backlog_outcome_line(board_name: str, ticket: dict[str, Any], period: str) -> str:
    title = _ticket_title(ticket)
    why = _ticket_why(ticket)
    return f"{board_name}: today's focus is {title}{why}."


def _priority_rank(ticket: dict[str, Any]) -> int:
    priority = str(ticket.get("priority") or "").strip().lower()
    return {
        "critical": 5,
        "urgent": 5,
        "high": 4,
        "medium": 3,
        "normal": 3,
        "low": 1,
    }.get(priority, 2)


def _lane_lookup(board: dict[str, Any]) -> dict[str, dict[str, Any]]:
    lanes = [lane for lane in board.get("lanes") or [] if isinstance(lane, dict)]
    return {str(lane.get("name") or "").strip().lower(): lane for lane in lanes}


def _lane_tickets(lane: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(lane, dict):
        return []
    tickets = [ticket for ticket in lane.get("tickets") or [] if isinstance(ticket, dict)]
    tickets.sort(key=_priority_rank, reverse=True)
    return tickets


def _fallback_board_outcomes(boards: list[dict[str, Any]], *, scope: str) -> list[str]:
    outcomes: list[str] = []
    for board in boards[:6]:
        board_name = _first_nonempty(board.get("name"), board.get("board_name"), default="Board")
        lane_by_name = _lane_lookup(board)
        current = lane_by_name.get("current") or lane_by_name.get("doing") or lane_by_name.get("in progress")
        backlog = lane_by_name.get("backlog") or lane_by_name.get("todo") or lane_by_name.get("to do")
        current_tickets = _lane_tickets(current)
        backlog_tickets = _lane_tickets(backlog)
        period = "this week" if scope == "week" else "today"

        if current_tickets:
            for ticket in current_tickets[:2]:
                outcomes.append(_finish_outcome_line(board_name, ticket, period))
            continue

        if backlog_tickets:
            for ticket in backlog_tickets[:2 if scope == "week" else 1]:
                outcomes.append(_backlog_outcome_line(board_name, ticket, period))
            continue

        backlog_count = int((backlog or {}).get("ticket_count") or 0)
        if backlog_count:
            outcomes.append(
                f"{board_name}: Choose the {board_name} outcome from Backlog before starting new work."
            )
            continue

        lanes = [lane for lane in board.get("lanes") or [] if isinstance(lane, dict)]
        visible_count = sum(int(lane.get("ticket_count") or len(lane.get("tickets") or []) or 0) for lane in lanes)
        if visible_count:
            outcomes.append(
                f"{board_name}: pick one visible work item and turn it into a finished outcome {period}."
            )
    return outcomes


def _fallback_source_pressure(proposals: list[dict[str, Any]], *, limit: int = 5) -> list[str]:
    pressure: list[str] = []
    seen: set[str] = set()
    for proposal in proposals:
        payload = proposal.get("payload") if isinstance(proposal.get("payload"), dict) else {}
        source = _first_nonempty(payload.get("source"), proposal.get("source"), default="source")
        description = _first_nonempty(proposal.get("description"), proposal.get("action_type"), default="")
        if not description:
            continue
        key = f"{source}:{description}".lower()
        if key in seen:
            continue
        seen.add(key)
        pressure.append(f"{source.title()}: {description}")
        if len(pressure) >= limit:
            break
    return pressure


def build_planner_orchestration_actions(bundle: ContextBundle, *, scope: str) -> list[dict[str, Any]]:
    """Build concrete, permission-gated actions implied by a plan."""
    if scope != "day":
        return []
    work_scan = bundle.work_scan if isinstance(bundle.work_scan, dict) else {}
    boards = [board for board in work_scan.get("boards", []) if isinstance(board, dict)]
    actions: list[dict[str, Any]] = []
    for board in boards[:8]:
        board_id = board.get("id") or board.get("board_id")
        if not board_id:
            continue
        board_name = _first_nonempty(board.get("name"), board.get("board_name"), default="Board")
        lane_by_name = _lane_lookup(board)
        current = lane_by_name.get("current") or lane_by_name.get("doing") or lane_by_name.get("in progress")
        backlog = lane_by_name.get("backlog") or lane_by_name.get("todo") or lane_by_name.get("to do")
        if _lane_tickets(current):
            continue
        backlog_tickets = _lane_tickets(backlog)
        if not backlog_tickets:
            continue
        ticket = backlog_tickets[0]
        ticket_id = ticket.get("id")
        if ticket_id is None:
            continue
        title = _ticket_title(ticket)
        actions.append({
            "action_type": "ticket_lane_move",
            "description": f"Make '{title}' current for today's {board_name} outcome.",
            "payload": {
                "board_id": int(board_id),
                "ticket_ids": [int(ticket_id)],
                "target_lane": "Current",
                "source": "planner_orchestration",
                "confidence": 0.74,
                "risk_level": "low",
            },
        })
    return actions


def _fallback_planner_markdown(
    scope: str,
    date_info: dict[str, Any],
    bundle: ContextBundle,
    task_instruction: str,
    failure_summary: str,
) -> str:
    """Create a useful planner when configured LLM providers are unavailable."""
    work_scan = bundle.work_scan if isinstance(bundle.work_scan, dict) else {}
    orchestrator_triage = work_scan.get("orchestrator_triage") if isinstance(work_scan.get("orchestrator_triage"), dict) else {}
    triage_candidates = [
        c for c in orchestrator_triage.get("candidates", []) if isinstance(c, dict)
    ]
    proposals = [p for p in work_scan.get("proposals", []) if isinstance(p, dict)]
    connected_sources = [
        s for s in work_scan.get("connected_sources", []) if isinstance(s, dict)
    ]
    unavailable_sources = [
        s for s in work_scan.get("unavailable_sources", []) if isinstance(s, dict)
    ]
    boards = [b for b in work_scan.get("boards", []) if isinstance(b, dict)]
    board_summary = bundle.kanban_summary or []

    connected_labels = [
        s.get("label") or s.get("provider")
        for s in connected_sources
        if s.get("connected") and (s.get("label") or s.get("provider"))
    ]
    pending_sources = [
        s.get("label") or s.get("provider")
        for s in connected_sources
        if not s.get("connected") and (s.get("label") or s.get("provider"))
    ]

    today_items: list[str] = []
    if connected_labels:
        today_items.append(f"Connected work sources: {', '.join(connected_labels[:8])}.")
    if pending_sources:
        today_items.append(f"Needs setup: {', '.join(pending_sources[:6])}.")
    for board in boards[:4]:
        lane_count = len(board.get("lanes") or [])
        ticket_count = sum(int(l.get("ticket_count") or 0) for l in board.get("lanes") or [])
        today_items.append(
            f"{_first_nonempty(board.get('name'), default='Board')} has {_count_phrase(ticket_count, 'visible item')} across {_count_phrase(lane_count, 'lane')}."
        )
    if not today_items:
        today_items.append("No connected work source produced actionable items yet.")

    outcome_items = _fallback_board_outcomes(boards, scope=scope)
    source_pressure = _fallback_source_pressure(proposals)

    attention_items: list[str] = []
    for candidate in triage_candidates[:8]:
        attention_items.append(_first_nonempty(
            candidate.get("question"),
            candidate.get("title"),
            default="A work item needs a decision.",
        ))
    for task in bundle.stuck_tasks[:5]:
        attention_items.append(_first_nonempty(
            task.get("title") if isinstance(task, dict) else task,
            task.get("description") if isinstance(task, dict) else "",
            default="Stuck task needs review.",
        ))
    for proposal in proposals[:6]:
        description = _first_nonempty(
            proposal.get("description"),
            proposal.get("action_type"),
            default="",
        )
        lowered = description.lower()
        if "backlog item" in lowered and "promot" in lowered:
            continue
        attention_items.append(description or "Proposed work item needs review.")
    for workflow in bundle.unfinished_workflows[:4]:
        attention_items.append(_first_nonempty(
            workflow.get("name") if isinstance(workflow, dict) else workflow,
            workflow.get("title") if isinstance(workflow, dict) else "",
            default="Unfinished workflow needs review.",
        ))
    if not attention_items:
        for board in board_summary[:4]:
            if not isinstance(board, dict):
                continue
            overdue = int(board.get("overdue_tickets") or 0)
            total = int(board.get("total_tickets") or 0)
            if overdue or total:
                attention_items.append(
                    f"{_first_nonempty(board.get('board_name'), default='Board')}: {_count_phrase(total, 'ticket')}, {overdue} overdue."
                )
    if not attention_items:
        attention_items.append("No stuck tickets, unfinished workflows, or board proposals were found in the current scan.")

    if not outcome_items:
        outcome_items = [
            item for item in attention_items
            if not item.startswith("No stuck")
        ][:4]
    if not outcome_items:
        outcome_items.append("Choose one meaningful work thread and turn it into a finished next step.")

    outcome_keys = {item.lower()[:72] for item in outcome_items}
    attention_items = [
        item for item in attention_items
        if item.lower()[:72] not in outcome_keys
    ]

    blockers = []
    if unavailable_sources:
        blockers.extend(
            f"{_first_nonempty(s.get('source'), default='source')}: {_first_nonempty(s.get('reason'), default='unavailable')}"
            for s in unavailable_sources[:4]
        )
    blockers.append(
        "Planner LLM fallback was used. Check the OpenAI key or install/configure the local Ollama fallback model."
    )

    next_action = (
        attention_items[0]
        if attention_items and not attention_items[0].startswith("No stuck")
        else "Open Advanced > Work Connectors, confirm the key work sources, then run the morning brief again."
    )

    if scope == "morning":
        return "\n\n".join([
            "## Morning Check-in",
            "\n".join(f"- {item}" for item in today_items),
            "## Needs Your Call",
            "\n".join(f"- {item}" for item in attention_items[:8]),
            "## Approvals & Blockers",
            "\n".join(f"- {item}" for item in blockers[:6]),
            "## Suggested Next Action",
            f"- {next_action}",
        ])

    if scope == "day":
        return "\n\n".join([
            "## Outcome for Today",
            "\n".join(f"- {item}" for item in outcome_items[:6]),
            "## Project Moves",
            "\n".join(f"- {item}" for item in attention_items[:8]),
            "## Source Pressure",
            "\n".join(f"- {item}" for item in source_pressure[:5]) if source_pressure else "- No connected-source pressure stood out.",
            "## Signals Checked",
            f"- {', '.join(connected_labels[:8])}." if connected_labels else "- No connected work sources reported in this scan.",
            "## Blockers",
            "\n".join(f"- {item}" for item in blockers[:6]),
            f"<!-- planner provider failures: {_clip_text(failure_summary, 500)} -->",
        ])

    if scope == "week":
        return "\n\n".join([
            "## Week Outcome",
            "\n".join(f"- {item}" for item in outcome_items[:8]),
            "## This Week",
            "\n".join(f"- {item}" for item in attention_items[:8]),
            "## Source Pressure",
            "\n".join(f"- {item}" for item in source_pressure[:6]) if source_pressure else "- No connected-source pressure stood out.",
            "## Signals Checked",
            f"- {', '.join(connected_labels[:8])}." if connected_labels else "- No connected work sources reported in this scan.",
            "## Blockers",
            "\n".join(f"- {item}" for item in blockers[:6]),
            f"<!-- planner provider failures: {_clip_text(failure_summary, 500)} -->",
        ])

    return "\n\n".join([
        f"## {scope.title()} Plan",
        "\n".join(f"- {item}" for item in attention_items[:8]),
        "## Blockers",
        "\n".join(f"- {item}" for item in blockers[:6]),
        f"<!-- planner provider failures: {_clip_text(failure_summary, 500)} -->",
    ])


def build_date_info(scope: str, *, local_now: datetime | None = None) -> dict[str, Any]:
    """Structured period labels for persistence (local timezone)."""
    tz = default_local_tz()
    if local_now is None:
        local = datetime.now(tz)
    else:
        local = local_now
        if local.tzinfo is None:
            local = local.replace(tzinfo=tz)
        else:
            local = local.astimezone(tz)
    d = local.date()
    iso_d = d.isoformat()
    if scope == "day":
        return {"period": "day", "local_iso_date": iso_d, "timezone": str(tz)}
    if scope == "morning":
        return {"period": "morning", "local_iso_date": iso_d, "timezone": str(tz)}
    if scope == "week":
        week_start = d - timedelta(days=d.weekday())
        week_end = week_start + timedelta(days=6)
        return {
            "period": "week",
            "week_start": week_start.isoformat(),
            "week_end": week_end.isoformat(),
            "timezone": str(tz),
        }
    if scope == "month":
        return {
            "period": "month",
            "year": local.year,
            "month": local.month,
            "timezone": str(tz),
        }
    return {"period": scope, "timezone": str(tz)}


def _system_prompt_for_scope(scope: str, date_info: dict) -> str:
    common = (
        "Output ONLY markdown suitable for chat and optional text-to-speech. "
        "Do not wrap in JSON. Do not use markdown code fences around the whole document.\n"
        f"Period context (machine-readable): {json.dumps(date_info, ensure_ascii=False)}\n"
    )
    if scope == "morning":
        return (
            "You are the daily check-in orchestrator for a proactive desktop work agent. "
            "The user does not want a passive report. They need decisions: create tickets, update tickets, "
            "make bookings, draft replies, attach agent work back to tickets, or ignore noise. "
            "Assume the answer may be read as a Telegram voice note: conversational, short, and useful out loud.\n"
            + common
            + "Use these sections (## headings):\n"
            "## Morning Check-in — what changed across sources\n"
            "## Needs Your Call — direct questions the user can approve/reject\n"
            "## Today's Outcomes — what the user should actually achieve today\n"
            "## Approvals & blockers\n"
            "## Suggested next action\n"
            "Rules:\n"
            "- Be concise and direct.\n"
            "- Use Current lane items as active commitments before looking at Backlog.\n"
            "- If Current is empty or thin, infer achievable outcomes from Backlog and connected-source pressure.\n"
            "- Pull signal from Telegram, WhatsApp, Gmail/email, Slack, Jira, Trello, ClickUp, Monday, workflows, developer context, ticket board notes, and memory when present.\n"
            "- Use work_scan.orchestrator_triage.candidates as the primary source of decisions, but do not mention internal system names.\n"
            "- Ask concrete questions, e.g. 'Should I create a ticket from this WhatsApp thread?'\n"
            "- If source context is thin, say exactly which connector or permission is missing.\n"
            "- Do not say only that a proactive brief ran.\n"
            "- When board_notes are present, treat them as the user's own scratchpad and weave them into outcomes and decisions.\n"
            "- Prefer outcomes and decisions over lane maintenance or broad status lists.\n"
        )
    if scope == "day":
        return (
            "You are a day-planning assistant. The user uses a personal productivity agent with "
            "ticket boards, workflows, and memory files. Be outcome-driven: say what should be achieved "
            "for the day and for each important project, not just which tickets exist. "
            "The result will often become a Telegram voice note, so keep it natural and spoken-friendly.\n"
            + common
            + "Use these sections (## headings):\n"
            "## Outcome for Today — the result the day should produce\n"
            "## Project Moves — what needs to move forward per project or board\n"
            "## Source Pressure — Slack, Gmail/email, WhatsApp, Telegram, Jira, Trello, ClickUp, Monday, workflow, ticket board notes, and developer signals that change priority\n"
            "## Schedule — suggested time blocks (if inferable from context)\n"
            "## Risks & blockers\n"
            "Rules:\n"
            "- Inspect Current first and treat it as the active commitment list.\n"
            "- If Current is empty or too thin, pull only the most achievable Backlog outcomes and explain why.\n"
            "- Do not tell the user to move backlog items as the outcome; describe the useful result to finish.\n"
            "- Be conversational, concise, and actionable. Avoid robotic inventory wording.\n"
        )
    if scope == "week":
        return (
            "You are a week-planning assistant for DecisionsAI. Build a weekly arc from active commitments, "
            "achievable backlog outcomes, connected messages, workflows, ticket board notes, developer context, and memory. "
            "Do not create a ticket dump; explain what the week should accomplish.\n"
            + common
            + "Use these sections:\n"
            "## Week Outcome — the main results the week should produce\n"
            "## This Week — project-by-project focus\n"
            "## Source Pressure — external signals that change priority\n"
            "## Stuck / needs attention\n"
            "Rules:\n"
            "- Current lane work anchors the week.\n"
            "- Backlog only becomes part of the week if it is realistically achievable or externally pressured.\n"
            "- Keep phrasing useful for a Telegram summary.\n"
        )
    if scope == "month":
        return (
            "You are a month-planning assistant.\n"
            + common
            + "Use these sections:\n"
            "## Goals recap — progress vs intent\n"
            "## Adjustments — what to change going forward\n"
            "## Open work — major threads from boards/workflows\n"
        )
    return "You are a planning assistant.\n" + common


def _strip_outer_fence(text: str) -> str:
    t = (text or "").strip()
    if not t.startswith("```"):
        return t
    lines = t.split("\n")
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines).strip()


def _user_payload(
    bundle: ContextBundle,
    task_instruction: str,
    date_info: dict,
) -> str:
    return json.dumps(
        {
            "task_instruction": task_instruction,
            "date_info": date_info,
            "current_datetime": bundle.current_datetime,
            "chat_history": bundle.chat_history,
            "scheduled_sessions": bundle.scheduled_sessions,
            "kanban_summary": bundle.kanban_summary,
            "board_notes": bundle.board_notes,
            "stuck_tasks": bundle.stuck_tasks,
            "unfinished_workflows": bundle.unfinished_workflows,
            "active_project": bundle.active_project,
            "recent_audit": bundle.recent_audit[:15],
            "developer_context": bundle.developer_context,
            "work_scan": bundle.work_scan,
            "memory_files_trimmed": {
                "agent": (bundle.memory_agent or "")[:8000],
                "user": (bundle.memory_user or "")[:8000],
                "long_term": (bundle.memory_long_term or "")[:8000],
            },
        },
        ensure_ascii=False,
    )


def run_day_planner(
    settings: dict, bundle: ContextBundle, task_instruction: str
) -> tuple[str, dict]:
    """R4: day scope — same as ``generate_planner_markdown(\"day\", ...)``."""
    return generate_planner_markdown("day", settings, bundle, task_instruction)


def run_week_planner(
    settings: dict, bundle: ContextBundle, task_instruction: str
) -> tuple[str, dict]:
    return generate_planner_markdown("week", settings, bundle, task_instruction)


def run_month_planner(
    settings: dict, bundle: ContextBundle, task_instruction: str
) -> tuple[str, dict]:
    return generate_planner_markdown("month", settings, bundle, task_instruction)


def generate_planner_markdown(
    scope: str,
    settings: dict,
    bundle: ContextBundle,
    task_instruction: str,
) -> tuple[str, dict]:
    """
    Run the planner LLM with scope-specific system prompt (before the call).

    Returns (markdown, date_info).
    """
    import litellm

    date_info = build_date_info(scope)
    system_prompt = _system_prompt_for_scope(scope, date_info)
    user_prompt = _user_payload(bundle, task_instruction, date_info)
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]

    failure_reasons: list[tuple[str, str, str]] = []
    for provider, model in _llm_candidates(settings):
        litellm_model = _litellm_model(provider, model, settings)
        try:
            response = litellm.completion(
                model=litellm_model,
                messages=messages,
                **_completion_options(litellm_model),
            )
            raw = (response.choices[0].message.content or "").strip()
            markdown = _strip_outer_fence(raw)
            if not markdown:
                raise RuntimeError("empty planner response")
            return markdown, date_info
        except litellm.AuthenticationError as e:
            short = str(e).split("\n")[0][:120]
            failure_reasons.append((provider, model, f"AUTH: {short}"))
            logger.warning(
                "planners: auth error for %s/%s, trying next provider",
                provider,
                model,
            )
            continue
        except Exception as e:
            short = f"{type(e).__name__}: {str(e).split(chr(10))[0][:120]}"
            failure_reasons.append((provider, model, short))
            logger.warning(
                "planners: LLM failed for %s/%s, trying next provider",
                provider,
                model,
            )
            continue

    summary = ", ".join(f"{p}/{m}: {r}" for p, m, r in failure_reasons)
    logger.warning(
        "planners: all LLM providers failed; using deterministic fallback. Tried: [%s]",
        summary,
    )
    return _fallback_planner_markdown(
        scope,
        date_info,
        bundle,
        task_instruction,
        summary,
    ), date_info


def tts_excerpt_from_markdown(markdown: str, max_len: int = 950) -> str:
    """Flatten markdown to a single spoken line, capped for TTS."""
    text = markdown or ""
    for heading in (
        "Outcome for Today",
        "Today's Outcomes",
        "Morning Check-in",
        "Week Outcome",
    ):
        section = extract_markdown_section(text, heading)
        if section:
            text = section
            break
    text = re.sub(r"<!--.*?-->", " ", text, flags=re.DOTALL)
    text = re.sub(r"```[^`]*```", " ", text, flags=re.DOTALL)
    text = re.sub(r"`([^`]+)`", r"\1", text)
    text = re.sub(r"#+\s*", "", text)
    text = re.sub(r"\*\*([^*]+)\*\*", r"\1", text)
    text = re.sub(r"\*([^*]+)\*", r"\1", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"^[-*]\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"\bUser instruction:\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\bPlanner LLM fallback was used\b.*", "", text, flags=re.IGNORECASE)
    text = " ".join(text.split())
    if len(text) <= max_len:
        return text
    return text[: max_len - 1].rsplit(" ", 1)[0] + "…"
