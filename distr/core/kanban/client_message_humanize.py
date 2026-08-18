"""Strip AI-writing tells from short client updates (humanizer hard rules, no LLM)."""

from __future__ import annotations

import re

_AI_WORDS = re.compile(
    r"\b("
    r"delve|landscape|tapestry|underscore|pivotal|crucial|vital|robust|"
    r"seamless|leverage|utilize|facilitate|encompass|showcase|showcasing|foster|"
    r"testament|notably|furthermore|moreover|additionally|ultimately|"
    r"comprehensive|innovative|cutting[- ]edge|game[- ]changer|"
    r"please\s+don'?t\s+hesitate|feel\s+free\s+to\s+reach\s+out|"
    r"I\s+hope\s+this\s+(?:email|message)\s+finds\s+you\s+well|"
    r"as\s+per\s+my\s+last|circling\s+back|touching\s+base"
    r")\b",
    re.I,
)
_EM_DASH = re.compile(r"[—–]+")
_MULTI_SPACE = re.compile(r"[ \t]{2,}")
_RULE_OF_THREE = re.compile(
    r"\b([\w'-]+),\s+([\w'-]+),\s+and\s+([\w'-]+)\b",
    re.I,
)


def humanize_client_message(text: str) -> str:
    """Make a client update sound human: short, direct, no chatbot filler."""
    out = str(text or "").strip()
    if not out:
        return ""
    out = _EM_DASH.sub(", ", out)
    out = _AI_WORDS.sub("", out)
    out = re.sub(r"\s{2,}", " ", out)
    # Soften rule-of-three lists into two items when all three are fluff-adjacent.
    out = _RULE_OF_THREE.sub(r"\1 and \2", out)
    out = out.replace("..", ".")
    out = _MULTI_SPACE.sub(" ", out).strip(" ,")
    # Prefer short paragraphs.
    lines = [ln.strip() for ln in out.splitlines() if ln.strip()]
    out = "\n".join(lines)
    if len(out) > 480:
        out = out[:477].rstrip(" ,.;:") + "..."
    return out


def build_client_work_update(
    *,
    contact: str,
    work_title: str,
    result_summary: str,
    time_spent: str = "",
) -> str:
    """Draft a client-facing update, then humanize it."""
    name = re.sub(r"\s+", " ", (contact or "").strip())
    title = re.sub(r"\s+", " ", (work_title or "the work").strip())
    title = re.sub(r"^[A-Z][A-Z0-9]+-\d+:\s*", "", title).strip() or "the work"
    summary = re.sub(r"[`*_#]+", "", result_summary or "")
    summary = re.sub(r"https?://\S+", "", summary)
    summary = re.sub(r"(?i)\b(?:run|ticket|step)\s*#?\d+\b", "", summary)
    summary = re.sub(r"\s+", " ", summary).strip(" .")
    if not summary or summary.startswith(("{", "[")) or len(summary) > 280:
        summary = "That work is done and checked on our side"
    greeting = f"Hi {name}," if name and not name.isdigit() else "Hi,"
    time_bit = f" We spent about {time_spent} on it." if time_spent else ""
    raw = (
        f"{greeting} quick update on {title}. {summary}."
        f"{time_bit} Ready for you to look at when you have a minute. "
        "Tell me if you want anything changed."
    )
    return humanize_client_message(raw)
