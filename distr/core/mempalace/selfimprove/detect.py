"""Frustration signals from normalized turns: regex, repeats, tool failures (+ optional Laya)."""

from __future__ import annotations

import re
from typing import Any

CORRECTION = re.compile(
    r"(?ix)\b(that'?s\s+(wrong|not\s+(it|right|what\s+i))|not\s+what\s+i\s+(asked|said|meant|wanted)|"
    r"i\s+(already\s+)?(said|told\s+you|asked)|you\s+(didn'?t|did\s+not|forgot|ignored|missed|broke)|"
    r"why\s+(did|are|would)\s+you|stop\s+(doing|that|it)|don'?t\s+do\s+that|wrong\s+(again|file|one)|"
    r"still\s+(not|doesn'?t|isn'?t|broken|failing|wrong)|doesn'?t\s+work|isn'?t\s+working|not\s+working|"
    r"undo\s+(that|this)|revert\s+(that|this)|read\s+(it\s+)?again|try\s+again|for\s+the\s+(second|third|last)\s+time|"
    r"how\s+many\s+times|wtf|ffs|come\s+on|seriously\??|useless|you'?re\s+(wrong|not\s+listening))\b"
)
NEGATION_START = re.compile(r"(?i)^\s*(no+[,.! ]|nope\b|wrong\b|incorrect\b|nah\b)")
SKILL_REF = re.compile(r"(?:skills|\.agents/skills|\.claude/skills|\.codex/skills)/([A-Za-z0-9._-]+)/SKILL\.md|\bskill[\"':= ]+([a-z0-9][a-z0-9_-]{2,})", re.I)

TAGS = ("correction", "wrong_answer", "repeated_ask", "tool_failure", "ignored_instruction", "escalation")

LAYA_QUESTIONS = {
    "frustrated": {
        "type": "noul",
        "instructions": "Is this user message correcting, complaining about, or expressing frustration with the AI assistant's previous work or answer?",
    },
    "severity": {
        "type": "score",
        "instructions": "How strongly is the user dissatisfied with the assistant here?",
        "criteria": ["Not dissatisfied; a normal request.", "Mild correction or nudge.",
                     "Clear frustration or a repeated correction.", "Strong frustration, work was broken or ignored."],
    },
    "kind": {
        "type": "choice",
        "instructions": "What best describes this user message?",
        "criteria": {
            "normal": "A normal request or reply with no complaint.",
            "correction": "Corrects a factual or technical mistake the assistant made.",
            "ignored_instruction": "Says the assistant ignored or forgot an earlier instruction.",
            "repeated_ask": "Repeats a request the assistant did not fulfil.",
            "tool_failure": "Reports that something the assistant ran or built fails or errors.",
        },
    },
}


def _tokens(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]{3,}", text.lower())}


def jaccard(a: str, b: str) -> float:
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def skill_from(text: str) -> str:
    m = SKILL_REF.search(text or "")
    if not m:
        return ""
    return (m.group(1) or m.group(2) or "").lower()


def regex_signal(text: str, prev_user: list[str]) -> dict[str, Any] | None:
    tags: list[str] = []
    sev = 0
    if CORRECTION.search(text):
        tags.append("correction")
        sev = max(sev, 2)
    if NEGATION_START.search(text):
        tags.append("wrong_answer")
        sev = max(sev, 1)
    if any(jaccard(text, p) >= 0.6 for p in prev_user[-5:]) and len(_tokens(text)) >= 4:
        tags.append("repeated_ask")
        sev = max(sev, 2)
    letters = [c for c in text if c.isalpha()]
    if (len(letters) > 12 and sum(c.isupper() for c in letters) / len(letters) > 0.6) or "!!" in text:
        tags.append("escalation")
        sev = max(sev, 3)
    if re.search(r"(?i)\b(i (already )?told you|ignored|forgot)\b", text):
        tags.append("ignored_instruction")
    if not tags:
        return None
    return {"tags": sorted(set(tags)), "severity": min(4, sev + (1 if len(tags) > 1 else 0)), "detector": "regex"}


def combine(regex: dict | None, laya: dict | None, *, threshold: float = 0.6) -> dict[str, Any] | None:
    if laya is None:
        return regex
    p = float(laya.get("frustrated", 0.0))
    if p < threshold and regex is None:
        return None
    if p < 0.25 and regex is not None and regex["severity"] <= 1:
        return None  # Laya confidently says no; drop weak regex-only hits
    tags = set((regex or {}).get("tags", []))
    kind = laya.get("kind")
    if kind and kind != "normal":
        tags.add(kind)
    if not tags:
        tags.add("correction")
    sev = max((regex or {}).get("severity", 0), int(round(float(laya.get("severity", 0)))) + 1)
    return {"tags": sorted(tags), "severity": min(4, sev), "detector": "laya+regex" if regex else "laya",
            "laya_p": round(p, 3)}
