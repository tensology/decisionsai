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
    candidates: list[tuple[str, str]] = []
    conv_provider = (settings.get("conversational_llm_provider") or "").strip().lower()
    conv_model = (settings.get("conversational_llm_model") or "").strip()
    agent_provider = (settings.get("agent_provider") or "").strip().lower()
    agent_model = (settings.get("agent_model") or "").strip()
    if conv_provider and conv_model:
        candidates.append((conv_provider, conv_model))
    if agent_provider and agent_model and (agent_provider, agent_model) != (conv_provider, conv_model):
        candidates.append((agent_provider, agent_model))
    candidates.append(("ollama", "llama3.2"))
    return candidates


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


def _first_nonempty(*values: Any, default: str = "") -> str:
    for value in values:
        text = _clip_text(value, 180)
        if text:
            return text
    return default


def _fallback_planner_markdown(
    scope: str,
    date_info: dict[str, Any],
    bundle: ContextBundle,
    task_instruction: str,
    failure_summary: str,
) -> str:
    """Create a useful planner when configured LLM providers are unavailable."""
    work_scan = bundle.work_scan if isinstance(bundle.work_scan, dict) else {}
    hermes_triage = work_scan.get("hermes_triage") if isinstance(work_scan.get("hermes_triage"), dict) else {}
    triage_candidates = [
        c for c in hermes_triage.get("candidates", []) if isinstance(c, dict)
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
            f"{_first_nonempty(board.get('name'), default='Board')} has {ticket_count} visible item(s) across {lane_count} lane(s)."
        )
    if not today_items:
        today_items.append("No connected work source produced actionable items yet.")

    attention_items: list[str] = []
    for candidate in triage_candidates[:8]:
        attention_items.append(_first_nonempty(
            candidate.get("question"),
            candidate.get("title"),
            default="Hermes triage candidate needs a decision.",
        ))
    for task in bundle.stuck_tasks[:5]:
        attention_items.append(_first_nonempty(
            task.get("title") if isinstance(task, dict) else task,
            task.get("description") if isinstance(task, dict) else "",
            default="Stuck task needs review.",
        ))
    for proposal in proposals[:6]:
        attention_items.append(_first_nonempty(
            proposal.get("description"),
            proposal.get("action_type"),
            default="Proposed work item needs review.",
        ))
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
                    f"{_first_nonempty(board.get('board_name'), default='Board')}: {total} ticket(s), {overdue} overdue."
                )
    if not attention_items:
        attention_items.append("No stuck tickets, unfinished workflows, or board proposals were found in the current scan.")

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
            "## Standup Triage",
            "\n".join(f"- {item}" for item in today_items),
            "## Decisions I Need From You",
            "\n".join(f"- {item}" for item in attention_items[:8]),
            "## Approvals & Blockers",
            "\n".join(f"- {item}" for item in blockers[:6]),
            "## Suggested Next Action",
            f"- {next_action}",
        ])

    return "\n\n".join([
        f"## {scope.title()} Plan",
        f"- Task instruction: {_clip_text(task_instruction, 220)}",
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
            "You are Hermes, the daily standup triage orchestrator for a proactive desktop work agent. "
            "The user does not want a passive report. They need decisions: create tickets, update tickets, "
            "make bookings, draft replies, attach agent work back to tickets, or ignore noise.\n"
            + common
            + "Use these sections (## headings):\n"
            "## Standup Triage — what changed across sources\n"
            "## Decisions I Need From You — direct questions the user can approve/reject\n"
            "## Approvals & blockers\n"
            "## Suggested next action\n"
            "Rules:\n"
            "- Be concise and direct.\n"
            "- Use work_scan.hermes_triage.candidates as the primary source of decisions.\n"
            "- Ask concrete questions, e.g. 'Should I create a ticket from this WhatsApp thread?'\n"
            "- If source context is thin, say exactly which connector or permission is missing.\n"
            "- Do not say only that a proactive brief ran.\n"
            "- Prefer an actionable triage queue over a broad status list.\n"
        )
    if scope == "day":
        return (
            "You are a day-planning assistant. The user uses a personal productivity agent with "
            "ticket boards, workflows, and memory files.\n"
            + common
            + "Use these sections (## headings):\n"
            "## Focus — top priorities today\n"
            "## Schedule — suggested time blocks (if inferable from context)\n"
            "## Work items — tickets / stuck items to address\n"
            "## Risks & blockers\n"
            "Be concise and actionable.\n"
        )
    if scope == "week":
        return (
            "You are a week-planning assistant.\n"
            + common
            + "Use these sections:\n"
            "## Themes — what matters this week\n"
            "## Deadlines & milestones\n"
            "## Backlog — suggested pulls into the week\n"
            "## Stuck / needs attention\n"
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
    text = re.sub(r"```[^`]*```", " ", text, flags=re.DOTALL)
    text = re.sub(r"`([^`]+)`", r"\1", text)
    text = re.sub(r"#+\s*", "", text)
    text = re.sub(r"\*\*([^*]+)\*\*", r"\1", text)
    text = re.sub(r"\*([^*]+)\*", r"\1", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = " ".join(text.split())
    if len(text) <= max_len:
        return text
    return text[: max_len - 1].rsplit(" ", 1)[0] + "…"
