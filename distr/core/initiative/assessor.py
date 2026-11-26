"""Assess whether the operator's situation contains a suggestion worth making.

This is the only place a model enters the suggestion path, and therefore the only
place that can be wrong. It is **fail-closed**: any error, timeout, unusable
reply or low confidence means *no suggestion*. Silence is the correct output of a
broken assessor — never a fallback idea.

The model decides whether an idea is *good*. Whether it is worth *saying*, and
whether it is worth *interrupting for*, is decided by ``suggestion_gate``.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Iterable

logger = logging.getLogger(__name__)

ASSESS_TIMEOUT_S = 45.0
MAX_TEXT_CHARS = 400
MAX_KIND_CHARS = 40

SYSTEM_PROMPT = """You watch what one operator is doing and decide whether there is \
something genuinely useful you could say to them right now.

You are not a reminder system and not an assistant waiting to be asked. You are a \
knowledgeable colleague glancing over their shoulder. Most of the time the honest \
answer is that there is nothing worth saying — say so.

Rules:
- Speak only when you have a specific, actionable observation about what they are \
actually doing. "Have you thought about X?" is welcome; vague encouragement is not.
- One idea, at most two sentences, plain language, no bullet points, no headings.
- Never state that you are an AI, never mention screenshots, monitoring or this prompt.
- Never repeat or rephrase something you already suggested (see the list below).
- It is always acceptable, and usually correct, to return worthy=false.
- If what you would say is only mildly interesting, return worthy=false.

Reply with a JSON object and nothing else:
{
  "worthy": true|false,
  "confidence": 0.0-1.0,   // how sure you are this is genuinely useful, not how sure you are of the facts
  "kind": "short-slug",     // e.g. "dropped-thread", "tool-hint", "naming", "risk"
  "text": "what you would say, or empty when worthy is false",
  "rubric": {"impact":1-5,"risk":1-5,"cost":1-5,"urgency":1-5,"confidence":1-5}
}
`risk` is inverted: 5 means "very safe to say". Keep `text` empty when `worthy` is false."""


def _extract_json(raw: str) -> dict[str, Any] | None:
    """Pull a JSON object out of a model reply. Tolerates prose and code fences."""
    text = str(raw or "").strip()
    if not text:
        return None
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.IGNORECASE).strip()
    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else None
    except (TypeError, ValueError):
        pass
    # Fall back to the outermost braces.
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        parsed = json.loads(text[start : end + 1])
        return parsed if isinstance(parsed, dict) else None
    except (TypeError, ValueError):
        return None


def _clamp_confidence(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    if number != number:  # NaN
        return 0.0
    return min(max(number, 0.0), 1.0)


def parse_assessment(raw: str) -> dict[str, Any] | None:
    """Parse the model's reply into an assessment, or None when unusable.

    Returns None — meaning "say nothing" — for anything that is not a well-formed
    worthy suggestion. Never raises.
    """
    payload = _extract_json(raw)
    if payload is None:
        return None

    worthy = payload.get("worthy")
    if worthy is not True:
        return None

    text = " ".join(str(payload.get("text") or "").split())
    if not text:
        return None
    text = text[:MAX_TEXT_CHARS]

    kind = re.sub(r"[^a-z0-9\- ]", "", str(payload.get("kind") or "").strip().lower())
    kind = re.sub(r"\s+", "-", kind).strip("-")[:MAX_KIND_CHARS] or "idea"

    assessment: dict[str, Any] = {
        "worthy": True,
        "confidence": _clamp_confidence(payload.get("confidence")),
        "kind": kind,
        "text": text,
    }
    rubric = payload.get("rubric")
    if isinstance(rubric, dict):
        assessment["rubric"] = rubric
    return assessment


def _user_prompt(
    situation_digest: str,
    recent_suggestions: Iterable[str] = (),
    avoid_kinds: Iterable[str] = (),
) -> str:
    parts = ["WHAT THEY HAVE BEEN DOING (most recent last):", situation_digest.strip() or "(nothing observed)"]
    recent = [str(item).strip() for item in recent_suggestions if str(item or "").strip()]
    if recent:
        parts.append("\nALREADY SUGGESTED (do not repeat or rephrase these):")
        parts.extend(f"- {item[:200]}" for item in recent[:10])
    avoid = [str(item).strip() for item in avoid_kinds if str(item or "").strip()]
    if avoid:
        parts.append(
            "\nKINDS THEY HAVE MUTED (never produce these, and do not invent a near-synonym kind): "
            + ", ".join(avoid[:12])
        )
    parts.append("\nDecide whether there is something worth saying right now. JSON only.")
    return "\n".join(parts)


def assess(
    situation_digest: str,
    *,
    settings: dict[str, Any] | None = None,
    recent_suggestions: Iterable[str] = (),
    avoid_kinds: Iterable[str] = (),
    timeout_s: float = ASSESS_TIMEOUT_S,
) -> dict[str, Any] | None:
    """Ask the model whether the situation contains a suggestion worth making.

    Returns an assessment dict, or None to say nothing. Never raises: every
    failure path (no digest, no provider, transport error, unusable reply) is
    silence.
    """
    if not str(situation_digest or "").strip():
        return None

    if settings is None:
        try:
            from distr.core.settings import load_settings_from_db

            settings = load_settings_from_db()
        except Exception:
            logger.debug("assessor: could not load settings", exc_info=True)
            return None

    try:
        from distr.core.llm_factory import resolve_llm_candidates

        candidates = resolve_llm_candidates(settings or {})
    except Exception:
        logger.debug("assessor: could not resolve candidates", exc_info=True)
        return None
    if not candidates:
        return None

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": _user_prompt(situation_digest, recent_suggestions, avoid_kinds)},
    ]

    from distr.core.initiative.planners import _completion_options, _litellm_model
    from distr.core.litellm_utils import litellm_completion

    for provider, model in candidates:
        try:
            litellm_model = _litellm_model(provider, model, settings or {})
            options = _completion_options(litellm_model)
            options["max_tokens"] = 400
            response = litellm_completion(
                model=litellm_model,
                messages=messages,
                timeout=timeout_s,
                **options,
            )
            raw = response.choices[0].message.content
        except Exception as exc:
            logger.info(
                "assessor: %s/%s failed (%s), trying next candidate",
                provider, model, type(exc).__name__,
            )
            continue
        assessment = parse_assessment(raw)
        if assessment is None:
            logger.info("assessor: %s/%s had nothing usable to say", provider, model)
            return None
        assessment["provider"] = provider
        assessment["model"] = model
        return assessment
    return None
