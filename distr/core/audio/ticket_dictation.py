"""Fast ticket-style cleanup for one-shot dictation."""

from __future__ import annotations

import json
import logging
import re
import urllib.request
from urllib.error import URLError

logger = logging.getLogger(__name__)

DEFAULT_TICKET_PROMPT = (
    "Rewrite the dictated text as a concise implementation ticket. "
    "Keep the user's intent, remove filler, and output only the ticket text "
    "with Title, Summary, and Acceptance Criteria, separated by blank lines."
)


def rewrite_dictation_as_ticket(text: str, settings: dict | None = None) -> str:
    """Return a cleaned ticket, preferring a tiny local model when configured."""
    raw = _clean_text(text)
    if not raw:
        return ""

    settings = settings or {}
    prompt = (settings.get("dictation_ticket_prompt") or DEFAULT_TICKET_PROMPT).strip()
    model = (settings.get("dictation_ticket_model") or "qwen2.5:0.5b").strip()
    timeout = _float_setting(settings.get("dictation_ticket_timeout"), 1.2)

    if bool(settings.get("dictation_ticket_use_llm", True)):
        rewritten = _rewrite_with_ollama(raw, prompt, model, timeout)
        if rewritten:
            return normalize_ticket_format(rewritten)

    return normalize_ticket_format(_format_ticket_locally(raw))


def _rewrite_with_ollama(text: str, prompt: str, model: str, timeout: float) -> str:
    try:
        payload = {
            "model": model,
            "stream": False,
            "options": {
                "temperature": 0.1,
                "num_predict": 220,
            },
            "messages": [
                {"role": "system", "content": prompt},
                {"role": "user", "content": text},
            ],
        }
        req = urllib.request.Request(
            "http://127.0.0.1:11434/api/chat",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=max(0.2, timeout)) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        content = ((data.get("message") or {}).get("content") or "").strip()
        return _normalize_model_ticket(content)
    except (TimeoutError, URLError, OSError, json.JSONDecodeError) as exc:
        logger.debug("Ticket dictation: local model unavailable/slow: %s", exc)
    except Exception as exc:
        logger.debug("Ticket dictation: rewrite failed: %s", exc, exc_info=True)
    return ""


def _normalize_model_ticket(text: str) -> str:
    cleaned = _strip_code_fences(text)
    if not cleaned:
        return ""
    if "summary" in cleaned.lower() or "acceptance" in cleaned.lower():
        return normalize_ticket_format(cleaned)
    return _format_ticket_locally(cleaned)


def normalize_ticket_format(text: str) -> str:
    """Keep ticket output readable with real section breaks."""
    cleaned = _strip_code_fences(text).replace("\r\n", "\n").replace("\r", "\n").strip()
    if not cleaned:
        return ""
    cleaned = re.sub(
        r"\s+(?=(?:Title|Summary|Acceptance Criteria):)",
        "\n",
        cleaned,
        flags=re.IGNORECASE,
    )

    lines = [line.strip() for line in cleaned.split("\n")]
    compact: list[str] = []
    previous_blank = False
    for line in lines:
        if not line:
            if not previous_blank:
                compact.append("")
            previous_blank = True
            continue
        compact.append(line)
        previous_blank = False

    section_heads = ("title:", "summary:", "acceptance criteria:", "acceptance:", "criteria:")
    out: list[str] = []
    for idx, line in enumerate(compact):
        lower = line.lower()
        is_head = any(lower.startswith(head) for head in section_heads)
        if is_head and out and out[-1] != "":
            out.append("")
        out.append(line)
        next_line = compact[idx + 1] if idx + 1 < len(compact) else None
        if is_head and next_line not in (None, ""):
            out.append("")

    formatted = "\n".join(out).strip()
    formatted = re.sub(r"\n{3,}", "\n\n", formatted)
    return formatted


def _format_ticket_locally(text: str) -> str:
    cleaned = _clean_text(text)
    title = _make_title(cleaned)
    summary = _sentence_case(cleaned)
    criteria = _acceptance_criteria(cleaned)
    return (
        f"Title: {title}\n\n"
        f"Summary:\n\n{summary}\n\n"
        "Acceptance Criteria:\n\n"
        + "\n".join(f"- {item}" for item in criteria)
    )


def _acceptance_criteria(text: str) -> list[str]:
    lowered = text.lower()
    criteria = ["The requested change is implemented cleanly."]
    if any(word in lowered for word in ("setting", "preferences", "shortcut", "hotkey")):
        criteria.append("The relevant setting or shortcut can be configured from Preferences.")
    if any(word in lowered for word in ("fast", "latency", "quick", "instant")):
        criteria.append("The flow stays low-latency and falls back gracefully if model cleanup is unavailable.")
    criteria.append("The result is easy to verify in the target workflow.")
    return criteria


def _make_title(text: str) -> str:
    words = re.findall(r"[A-Za-z0-9][A-Za-z0-9'-]*", text)
    stop = {"i", "need", "want", "to", "be", "able", "the", "a", "an", "and", "it", "for"}
    useful = [w for w in words if w.lower() not in stop]
    title_words = (useful or words)[:9]
    title = " ".join(title_words).strip()
    return _sentence_case(title).rstrip(".") or "New Ticket"


def _sentence_case(text: str) -> str:
    stripped = text.strip()
    if not stripped:
        return ""
    return stripped[0].upper() + stripped[1:]


def _clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip())


def _strip_code_fences(text: str) -> str:
    return re.sub(r"^```(?:\w+)?\s*|\s*```$", "", (text or "").strip()).strip()


def _float_setting(value, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default
