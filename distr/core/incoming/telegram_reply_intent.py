"""Work out what a Telegram message from Paul is meant to do while a client reply is being reworked.

A held client-reply card waits for "What should I change?". The next message,
typed or spoken, is not always the answer: it can be a dictated replacement, a
note about something else, a reply to a newer client message, "cancel", or too
garbled to act on. A strong model reads the message with the draft and the
client's newer messages; if no model is reachable a conservative rule set
decides. Nothing here sends anything.
"""

from __future__ import annotations

import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger(__name__)

INTENTS = ("edit_draft", "dictated_message", "other_topic", "cancel", "send", "unclear")

# Strongest first. Only models present in the cached OpenRouter catalogue are tried.
STRONG_OPENROUTER_MODELS = ("x-ai/grok-4.7", "anthropic/claude-opus-5.5", "openai/gpt-5.6-sol")
MODEL_TIMEOUT_S = 25.0
_CATALOGUE = Path(__file__).resolve().parents[3] / "models" / "openrouter_models.json"


def _catalogue_ids() -> set[str]:
    try:
        data = json.loads(_CATALOGUE.read_text(encoding="utf-8"))
        rows = data if isinstance(data, list) else data.get("data") or data.get("models") or []
        return {str(r.get("id")) for r in rows if isinstance(r, dict) and r.get("id")}
    except Exception:
        return set()


def strong_model_candidates(settings: dict[str, Any]) -> list[tuple[str, str]]:
    """Provider/model pairs for interpreting a message, strongest first.

    The Settings orchestrator model wins when set. Then the strongest catalogue
    models when OpenRouter is enabled with a key. The chat model is the last resort.
    """
    from distr.core.llm_factory import normalize_provider
    from distr.core.read_response import resolve_oracle_model

    out: list[tuple[str, str]] = []

    def add(provider: Any, model: Any) -> None:
        provider, model = str(provider or "").strip(), str(model or "").strip()
        if provider and model and (normalize_provider(provider), model) not in out:
            out.append((normalize_provider(provider), model))

    add(settings.get("orchestrator_provider"), settings.get("orchestrator_model"))
    if settings.get("openrouter_enabled") and str(settings.get("openrouter_key") or "").strip():
        known = _catalogue_ids()
        for model in STRONG_OPENROUTER_MODELS:
            if not known or model in known:
                add("openrouter", model)
    try:
        add(*resolve_oracle_model(settings))
    except Exception:
        logger.debug("Chat model unavailable for intent fallback", exc_info=True)
    return out


def _default_complete(provider: str, model: str, messages, settings) -> str:
    from distr.core.llm_factory import create_stream

    return "".join(create_stream(provider, model, messages, settings))


def complete_strong(messages: list[dict[str, str]], settings: dict[str, Any], *,
                    complete: Callable | None = None, timeout: float = MODEL_TIMEOUT_S,
                    candidates: list[tuple[str, str]] | None = None) -> tuple[str, str]:
    """Run ``messages`` on the strongest reachable model. Returns (text, "provider/model").

    Each model gets ``timeout`` seconds; a slow or failing one falls to the next.
    Raises RuntimeError when none answer.
    """
    run = complete or _default_complete
    errors: list[str] = []
    for provider, model in (candidates if candidates is not None else strong_model_candidates(settings)):
        pool = ThreadPoolExecutor(max_workers=1)
        try:
            text = str(pool.submit(run, provider, model, messages, settings).result(timeout=timeout) or "").strip()
            if text:
                return text, f"{provider}/{model}"
            errors.append(f"{model}: empty")
        except FutureTimeout:
            errors.append(f"{model}: timed out")
        except Exception as exc:  # noqa: BLE001 - any provider failure moves to the next model
            errors.append(f"{model}: {type(exc).__name__}")
        finally:
            pool.shutdown(wait=False, cancel_futures=True)
    raise RuntimeError("No model answered (" + "; ".join(errors) + ")")


# ---------------------------------------------------------------------------
# Intent
# ---------------------------------------------------------------------------

_SYSTEM = (
    "You read one Telegram message from Paul, a solo developer. He may have typed it or "
    "dictated it, so expect mishearings, filler, false starts and missing punctuation. "
    "Paul is reviewing a held draft reply to a client. He tapped Rework and was asked "
    "'What should I change?'. Decide what the message is for.\n"
    "Return only JSON: {\"intent\": one of edit_draft | dictated_message | other_topic | cancel | send | unclear, "
    "\"instruction\": string, \"message\": string, \"question\": string}.\n"
    "- edit_draft: a change to the draft, including adding something Paul wants said to the client "
    "(for example an answer to the client's newer message that belongs in the same reply). "
    "instruction = the change as a short, clean imperative with mishearings fixed.\n"
    "- dictated_message: Paul dictated the full replacement message. message = that text, cleaned.\n"
    "- other_topic: about other work, a question to the assistant, or anything not about this draft.\n"
    "- cancel: he does not want to send this reply (leave it, never mind, hold off).\n"
    "- send: he wants it sent now.\n"
    "- unclear: you cannot tell. question = one short question that settles it, naming the options.\n"
    "Never invent facts. Prefer unclear over a wrong guess about what to send to a client."
)

_CANCEL_RE = re.compile(r"(?is)^\s*(?:ok(?:ay)?[, ]+)?(?:no[, ]+)?(?:cancel(?: it| that)?|never ?mind|forget it|leave it|hold off|don'?t send(?: it| that)?|not yet)\W*$")
_SEND_RE = re.compile(r"(?is)^\s*(?:ok(?:ay)?[, ]+)?(?:yes[, ]+)?(?:please )?(?:send(?: it| that| the message)?(?: now)?|go ahead and send(?: it)?)\W*$")
_EDIT_HINT = re.compile(
    r"(?is)\b(?:add|remove|delete|drop|say|mention|change|update|rewrite|reword|shorten|shorter|longer|"
    r"include|leave out|call (?:him|her|them)|tell (?:him|her|them)|let (?:him|her|them) know|please at the end)\b"
)


def heuristic_intent(text: str, *, other_work: bool = False) -> dict[str, str]:
    """Conservative fallback when no model answers: never guess about other topics."""
    raw = str(text or "").strip()
    if _CANCEL_RE.match(raw):
        return {"intent": "cancel", "instruction": "", "message": "", "question": ""}
    if _SEND_RE.match(raw):
        return {"intent": "send", "instruction": "", "message": "", "question": ""}
    if other_work:
        return {"intent": "other_topic", "instruction": "", "message": "", "question": ""}
    if _EDIT_HINT.search(raw) or len(raw.split()) <= 25:
        return {"intent": "edit_draft", "instruction": raw, "message": "", "question": ""}
    return {"intent": "unclear", "instruction": "", "message": "",
            "question": "Is that a change to this draft, or something else? Tell me what to change."}


def _parse(raw: str) -> dict[str, str] | None:
    match = re.search(r"\{.*\}", str(raw or ""), re.S)
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
    except ValueError:
        return None
    intent = str(data.get("intent") or "").strip().lower()
    if intent not in INTENTS:
        return None
    return {"intent": intent, "instruction": str(data.get("instruction") or "").strip(),
            "message": str(data.get("message") or "").strip(), "question": str(data.get("question") or "").strip()}


def classify_rework_reply(text: str, *, draft: str, client: str = "", answering: str = "",
                          newer_inbound: list[str] | None = None, other_work: bool = False,
                          settings: dict[str, Any] | None = None,
                          complete: Callable | None = None) -> dict[str, str]:
    """Return {"intent", "instruction", "message", "question", "model"} for Paul's reply."""
    if settings is None:
        from distr.core.settings import load_settings_from_db

        settings = load_settings_from_db()
    context = {
        "client": client, "answering": answering, "draft": draft,
        "client_messages_newer_than_the_draft_was_written": list(newer_inbound or [])[-3:],
        "pauls_message": str(text or "").strip(),
    }
    messages = [{"role": "system", "content": _SYSTEM},
                {"role": "user", "content": json.dumps(context, ensure_ascii=False)}]
    try:
        raw, model = complete_strong(messages, settings, complete=complete)
        parsed = _parse(raw)
        if parsed:
            if parsed["intent"] == "edit_draft" and not parsed["instruction"]:
                parsed["instruction"] = str(text or "").strip()
            if parsed["intent"] == "dictated_message" and not parsed["message"]:
                parsed["intent"], parsed["instruction"] = "edit_draft", str(text or "").strip()
            return {**parsed, "model": model}
        logger.warning("Rework intent reply was not valid JSON; using rules")
    except Exception as exc:  # noqa: BLE001
        logger.warning("Rework intent model unavailable (%s); using rules", exc)
    return {**heuristic_intent(text, other_work=other_work), "model": "rules"}
