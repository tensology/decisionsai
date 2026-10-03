"""Apply the community humanizer skill to short client updates.

The skill at plugins/community-skills-pack/skills/humanizer/SKILL.md is a
writing prompt. This draft path does not call a model. It loads that skill
text and applies the hard rules it lists (AI vocabulary, filler swaps, dashes,
bold, curly quotes, chatbot closers), which is the same no-LLM humanizing
this module already did, now driven by the skill file.
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

# Single words the skill calls out that are safe to drop from a short client note.
# Common English the skill also lists (actually, key, valuable, highlight) is left alone.
_SAFE_SINGLE = {
    "delve",
    "landscape",
    "tapestry",
    "underscore",
    "pivotal",
    "crucial",
    "vital",
    "robust",
    "seamless",
    "leverage",
    "utilize",
    "facilitate",
    "encompass",
    "showcase",
    "showcasing",
    "foster",
    "fostering",
    "testament",
    "notably",
    "furthermore",
    "moreover",
    "additionally",
    "ultimately",
    "comprehensive",
    "innovative",
    "vibrant",
    "emphasizing",
    "enduring",
    "garner",
    "intricate",
    "intricacies",
    "groundbreaking",
    "breathtaking",
    "renowned",
    "nestled",
}

_EM_DASH = re.compile(r"\s*(?:—|–|--)\s*")
_MULTI_SPACE = re.compile(r"[ \t]{2,}")
_RULE_OF_THREE = re.compile(
    r"\b([\w'-]+),\s+([\w'-]+),\s+and\s+([\w'-]+)\b",
    re.I,
)
_BOLD = re.compile(r"\*\*(.+?)\*\*")
_CURLY = str.maketrans({"\u201c": '"', "\u201d": '"', "\u2018": "'", "\u2019": "'"})
_CHATBOT = re.compile(
    r"(?:"
    r"i hope this helps!?|"
    r"of course!|"
    r"certainly!|"
    r"you'?re absolutely right!?|"
    r"please\s+don'?t\s+hesitate(?:\s+to\s+[\w\s]+)?|"
    r"feel\s+free\s+to\s+reach\s+out|"
    r"i\s+hope\s+this\s+(?:email|message)\s+finds\s+you\s+well|"
    r"as\s+per\s+my\s+last|"
    r"circling\s+back|"
    r"touching\s+base|"
    r"let me know if you(?:'d| would) like(?: me)? to[^.!\n]*|"
    r"would you like(?: me)? to[^.!\n]*|"
    r"cutting[- ]edge|"
    r"game[- ]changer"
    r")",
    re.I,
)
_LIST_PREFIX = re.compile(
    r"^\*\*(?:High-frequency AI words|Words to watch|Phrases to watch):\*\*\s*(.*)$"
)
_FILLER_PAIR = re.compile(r'^- "(.+?)"\s*→\s*"(.+?)"\s*$')


def humanizer_skill_path() -> Path | None:
    """Existing community humanizer skill. No new voice file."""
    candidates: list[Path] = []
    try:
        from distr.core.plugins.paths import community_skills_dir

        candidates.append(community_skills_dir() / "humanizer" / "SKILL.md")
    except Exception:
        pass
    candidates.append(
        Path(__file__).resolve().parents[3]
        / "plugins"
        / "community-skills-pack"
        / "skills"
        / "humanizer"
        / "SKILL.md"
    )
    for path in candidates:
        if path.is_file():
            return path
    return None


@lru_cache(maxsize=1)
def _skill_rules() -> tuple[tuple[re.Pattern[str], ...], tuple[tuple[re.Pattern[str], str], ...]]:
    phrases: set[str] = set(_SAFE_SINGLE)
    replacements: list[tuple[str, str]] = []
    path = humanizer_skill_path()
    if path is not None:
        for line in path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            listed = _LIST_PREFIX.match(stripped)
            if listed:
                body = listed.group(1).replace("**", "")
                for part in body.split(","):
                    item = re.sub(r"\s*\(.*?\)", "", part)
                    item = re.sub(r"\s+", " ", item).strip(" .")
                    if not item or "/" in item:
                        continue
                    if " " in item or item.lower() in _SAFE_SINGLE:
                        phrases.add(item)
                continue
            pair = _FILLER_PAIR.match(stripped)
            if pair:
                replacements.append((pair.group(1), pair.group(2)))
    ordered = sorted(phrases, key=len, reverse=True)
    phrase_res = tuple(
        re.compile(rf"\b{re.escape(item)}\b", re.I) for item in ordered if item
    )
    filler_res = tuple(
        (re.compile(re.escape(src), re.I), dst) for src, dst in replacements if src
    )
    return phrase_res, filler_res


def humanize_client_message(text: str) -> str:
    """Make a client update sound human using the humanizer skill's hard rules."""
    out = str(text or "").strip()
    if not out:
        return ""
    phrase_res, filler_res = _skill_rules()
    for pattern, replacement in filler_res:
        out = pattern.sub(replacement, out)
    out = _EM_DASH.sub(", ", out)
    out = _CHATBOT.sub("", out)
    for pattern in phrase_res:
        out = pattern.sub("", out)
    out = _BOLD.sub(r"\1", out)
    out = out.translate(_CURLY)
    out = _RULE_OF_THREE.sub(r"\1 and \2", out)
    out = out.replace("..", ".")
    out = re.sub(r"\s+([,.;:!?])", r"\1", out)
    out = re.sub(r"\(\s*\)", "", out)
    out = _MULTI_SPACE.sub(" ", out)
    lines = [ln.strip(" ,") for ln in out.splitlines()]
    lines = [ln for ln in lines if ln]
    out = "\n".join(lines).strip(" ,")
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
    """Draft a client-facing update, then run it through the humanizer skill rules."""
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
    raw = f"{greeting} {title} is done. {summary}.{time_bit} Have a look when you have a minute."
    return humanize_client_message(raw)
