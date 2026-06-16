"""Copy for workflow wait / IDE handoff notifications."""

from __future__ import annotations

import re
from typing import Any


def is_ide_handoff_result(result_text: str) -> bool:
    low = (result_text or "").lower()
    return any(
        token in low
        for token in (
            "waiting in ide",
            "ide opened",
            "work packet",
            "opened cursor",
            "opened codex",
            "cursor ide",
            "codex ide",
        )
    )


def ide_handoff_voice_text(*, result_text: str, step_name: str = "", ticket_title: str = "") -> str:
    low = (result_text or "").lower()
    backend = "Codex" if "codex" in low else "Cursor"
    subject = (ticket_title or step_name or "").strip()
    if subject and subject.lower() not in {"workflow step", "step"}:
        return f"I opened {backend} for {subject}."
    return f"I opened {backend}. Take a look when you're ready."


def wait_handoff_voice_text(*, step_name: str, result_text: str, ticket_title: str = "") -> str:
    if is_ide_handoff_result(result_text):
        return ide_handoff_voice_text(
            result_text=result_text,
            step_name=step_name,
            ticket_title=ticket_title,
        )
    label = (step_name or "This step").strip()
    return f"{label} needs your input."


def build_wait_handoff_text(
    step_name: str,
    result_text: str,
    run_id: int | None,
    *,
    ticket_title: str = "",
) -> dict[str, str]:
    """Build wait-state text for chat history, agent report, and optional TTS."""
    clean_result = (result_text or "").strip()
    if not clean_result:
        clean_result = "Step completed with no detailed output."
    summary = clean_result[:280]
    if len(clean_result) > 280:
        summary += "..."
    step_label = step_name or "workflow step"
    prompt = (
        f"{step_label} is waiting for your decision. "
        "Reply with what should happen next, for example: continue, retry, skip, or provide extra instructions."
    )
    tts = wait_handoff_voice_text(
        step_name=step_label,
        result_text=clean_result,
        ticket_title=ticket_title,
    )
    report = (
        f"[WORKFLOW_WAIT_HANDOFF]\n"
        f"step_name: {step_label}\n"
        f"run_id: __RUN_ID__\n"
        f"status: waiting_for_user_input\n"
        f"step_result_summary: {summary}\n"
        f"step_result_full: {clean_result[:1500]}\n\n"
        "Orchestrator instructions:\n"
        "1) Relay the step result faithfully; do not re-style or expand scope.\n"
        "2) Ask one clear follow-up question for user input.\n"
        "3) After user reply, call continue_workflow with that reply."
    )
    history_entry = (
        f"{clean_result}\n\n"
        f"[WAITING FOR INPUT]\n{prompt}"
    )
    if run_id is not None:
        history_entry = f"{history_entry}\nRun ID: {run_id}"
    return {
        "prompt": prompt,
        "tts": tts,
        "report": report,
        "history_entry": history_entry,
        "is_ide_handoff": is_ide_handoff_result(clean_result),
    }
