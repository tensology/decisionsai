#!/usr/bin/env python3
"""Apply humanizer-style cleanup to CHANGELOG.md (mechanical pass)."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CHANGELOG = ROOT / "CHANGELOG.md"

AI_WORDS = {
    " additionally": ",",
    " crucial": " important",
    " delve": " dig",
    " fostering": " building",
    " garnered": " got",
    " highlighting": " showing",
    " landscape": " space",
    " pivotal": " big",
    " showcase": " show",
    " underscores": " shows",
    " vibrant": " lively",
    " comprehensive": " full",
    " enhanced": " improved",
    " streamlined": " simplified",
    " robust": " solid",
    " leverage": " use",
    " utilize": " use",
}


def fix_quotes(text: str) -> str:
    return (
        text.replace("\u201c", '"')
        .replace("\u201d", '"')
        .replace("\u2018", "'")
        .replace("\u2019", "'")
    )


def fix_bold_label_lines(text: str) -> str:
    # **Label** — body  ->  Label: body
    text = re.sub(r"^\*\*([^*]+)\*\* [—–] ", r"\1: ", text, flags=re.M)
    # **Label** – body (en dash)
    text = re.sub(r"^\*\*([^*]+)\*\* – ", r"\1: ", text, flags=re.M)
    return text


def fix_inline_em_dashes(text: str) -> str:
  # Spaced em dash -> comma (keep date ranges like March 24–25)
    def repl(m: re.Match[str]) -> str:
        before = m.group(1)
        after = m.group(2)
        if re.search(r"\d$", before) and re.match(r"\d", after):
            return f"{before}-{after}"
        if after and after[0].islower():
            return f"{before}, {after}"
        return f"{before}. {after}"

    text = re.sub(r"([^—\n])—([^—\n])", repl, text)
    # Remaining spaced en dashes used like em dashes (not in headings)
    text = re.sub(r"([^–\n])–([^–\n])", repl, text)
    return text


def soften_ai_vocab(text: str) -> str:
    lower = text.lower()
    for old, new in AI_WORDS.items():
        # case-insensitive replace preserving rough case of first letter
        pattern = re.compile(re.escape(old.strip()), re.I)

        def sub_fn(m: re.Match[str]) -> str:
            s = m.group(0)
            rep = new.strip()
            if s[0].isupper():
                rep = rep[0].upper() + rep[1:]
            return rep

        text = pattern.sub(sub_fn, text)
    return text


def unwrap_paragraph_bold_openers(text: str) -> str:
    # **Sentence opener.** Rest... -> Sentence opener. Rest...
    return re.sub(
        r"^\*\*([^*]+)\.\*\* ",
        r"\1. ",
        text,
        flags=re.M,
    )


def humanize_notes(text: str) -> str:
    old = """## Notes

DecisionsAI is like having a smart assistant that can control your computer with your voice. Think of it like Siri or Alexa, but it can actually DO things on your computer - not just answer questions. 

**What makes it special:**
- You can talk to it naturally, like talking to an agent
- It can control your mouse and keyboard with voice commands
- It can help with tasks by explaining things, summarizing text, and even writing code
- It can take a screenshot of your screen and tell you what's on it.
- It works mostly offline (if you use Ollama), so you don't need internet
- It can automate boring tasks like sending emails or creating documents

**Common Use Cases:**
- **Writing Papers**: Use dictation mode to speak your essay, then ask the AI to improve it
- **Research**: Ask the AI to search the web and summarize information
- **Coding**: Ask the AI to write code or help debug programs
- **Accessibility**: Great for people who have difficulty typing or using a mouse
- **Productivity**: Automate repetitive tasks like organizing files or sending emails

**Getting Help:**
- Say "what can you do?" to see all available features
- Check the Settings window to configure AI models and speech recognition
- The chat window shows your conversation history and lets you type messages too"""
    new = """## Notes

DecisionsAI is a voice-first assistant that can also type, click, run workflows, and talk to your coding tools. Less "answer my question," more "help me get something done on this machine."

You can dictate, drive the mouse and keyboard, run multi-step workflows, hand tickets to Cursor or Codex, and get screenshots or test output back on a board. Local models (Ollama) work offline if you set them up that way.

Say "what can you do?" in chat for a live list. Settings is where models, voice, and API keys live. The chat window keeps history if you prefer typing."""
    if old in text:
        text = text.replace(old, new)
    return text


def main() -> None:
    text = CHANGELOG.read_text(encoding="utf-8")
    text = fix_quotes(text)
    text = fix_bold_label_lines(text)
    text = unwrap_paragraph_bold_openers(text)
    text = fix_inline_em_dashes(text)
    text = soften_ai_vocab(text)
    text = humanize_notes(text)
    # No em/en dashes left (humanizer hard rule)
    if "—" in text or "–" in text:
        remaining = text.count("—") + text.count("–")
        print(f"warning: {remaining} dash chars remain; fixing literally")
        text = text.replace("—", ", ").replace("–", "-")
    CHANGELOG.write_text(text, encoding="utf-8")
    print(f"wrote {CHANGELOG}")


if __name__ == "__main__":
    main()
