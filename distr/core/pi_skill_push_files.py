"""Filesystem helpers for pushing Pi skills — no LangChain/agent imports."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

USER_INTENT_FILENAME = "USER_INTENT.md"


def write_pi_skill_user_intent(dest_skill_dir: Path, skill_id: str, instructions: str) -> Optional[Path]:
    """Persist 'Use this skill to' text as markdown beside SKILL.md for cold Pi CLI starts."""
    path = dest_skill_dir / USER_INTENT_FILENAME
    text = (instructions or "").strip()
    if not text:
        if path.exists():
            try:
                path.unlink()
            except OSError:
                logger.warning("Could not remove %s", path)
        return None
    body = (
        "# Use this skill to\n\n"
        "The following was saved when this skill was pushed from **Decisions AI** into "
        f"`.pi/skills/{skill_id}/`. It lives on disk next to `SKILL.md`, so the Pi CLI can read it "
        "when you open the project — even if no agent was running when you pushed.\n\n"
        "---\n\n"
        f"{text}\n\n"
        "---\n\n"
        f"*File: `{USER_INTENT_FILENAME}` — safe to edit.*\n"
    )
    path.write_text(body, encoding="utf-8")
    return path
