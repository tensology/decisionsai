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
                max_tokens=3072,
                temperature=0.45,
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
    raise RuntimeError(f"planners: all LLM providers failed. Tried: [{summary}]")


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
