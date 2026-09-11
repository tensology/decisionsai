"""Structured context assembly for native Development turns."""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Any

from distr.core.chat import ChatService


_MAX_CONTEXT_CHARS = 32_000
_MAX_MESSAGE_CHARS = 6_000


def _is_persisted_provider_failure(role: str, content: str) -> bool:
    if role != "assistant":
        return False
    clean = content.lstrip().lower()
    return clean.startswith("error code:") or clean.startswith("provider error:")


def development_system_instruction(
    metadata: dict[str, Any],
    *,
    project_folder: str,
    autonomy_level: str,
) -> str:
    mode = (
        "Inspect and plan with read-only project tools. Do not modify files or run mutating commands."
        if autonomy_level == "plan"
        else "Work as the active coding agent. Inspect the project architecture first, edit with scoped tools, run relevant checks, and continue until the request is handled or a user decision is required."
    )
    scope = [
        f"Project folder: {project_folder}" if project_folder else "",
        f"Board: {metadata.get('board_key')}" if metadata.get("board_key") else "",
        f"Thread ticket: {metadata.get('board_ticket_key') or metadata.get('ticket_id')}"
        if metadata.get("board_ticket_key") or metadata.get("ticket_id")
        else "",
    ]
    identity = metadata.get("active_runtime_identity") if isinstance(metadata.get("active_runtime_identity"), dict) else {}
    runtime_identity = (
        "Active runtime identity for this turn:\n"
        f"Agent: {identity.get('agent') or 'Decisions Development agent'}\n"
        f"Provider: {identity.get('provider') or 'runtime-selected'}\n"
        f"Model: {identity.get('model') or 'runtime-selected'}\n"
        f"Turn runtime: {identity.get('turn_runtime') or 'runtime-selected'}\n"
        f"Configured routing backend: {identity.get('routing_backend') or 'runtime-selected'}\n"
        f"Route mode: {identity.get('route_mode') or 'auto'}\n"
        "When asked about your provider, model, or execution identity, answer from this block. "
        "Do not infer it from project notes or earlier messages. The turn runtime and configured routing backend are not the model provider. "
        "Do not expand, reinterpret, or invent descriptions for these identifiers."
    )
    selected_skills = [str(skill_id).strip() for skill_id in (metadata.get("turn_skill_ids") or []) if str(skill_id).strip()]
    skill_instruction = (
        "The user selected these skills for this turn: "
        + ", ".join(selected_skills)
        + ". Read each exact skill with read_harness_skill before acting, then apply its instructions. Do not search or list the skill catalog first."
        if selected_skills
        else ""
    )
    return "\n".join(
        part
        for part in (
            "You are the native Decisions Development agent.",
            mode,
            "This Development thread is the ticket and current work item. Work on it directly. Do not create another ticket, issue, ticket file, workflow, workflow run, or workflow step unless the user explicitly requests a separate artifact.",
            "Use only the project tools advertised by Decisions. Treat every tool observation as authoritative. Verify material changes before claiming completion.",
            "Use installed skills only when the user selected one or the request genuinely requires specialized guidance that the project tools do not provide. Routine code edits, searches, and tests do not require a skill lookup. If discovery is necessary, make one focused list_harness_skills query and read only a relevant result.",
            "Keep the turn lean. Use targeted reads and searches, select no more than two specialist skills unless the user explicitly asks for more, and do not put raw large JSON, logs, or full audit output into model context. Save large output as an artifact and return only the verdict, relevant excerpts, and path.",
            "Run the smallest verification that proves the requested change. Expand the test or review scope only when risk, a failure, or the user's request requires it.",
            "A direct modification request is authorization to implement it. Do not finish by offering to make the requested change or asking which obvious target to use when repository and runtime evidence identify it. If cosmetic details are omitted, make the smallest conventional implementation and report the choice.",
            skill_instruction,
            runtime_identity,
            *scope,
        )
        if part
    )


def build_development_context(
    chat_id: int,
    prompt: str,
    metadata: dict[str, Any],
    *,
    project_folder: str,
    autonomy_level: str,
    attachments: list[dict[str, Any]] | None = None,
) -> tuple[dict[str, Any], ...]:
    """Return checkpoint-aware structured messages ending in the current prompt."""
    history = ChatService.get_chat_history(int(chat_id))
    if history and history[-1].get("role") == "user" and str(history[-1].get("content") or "").strip() == prompt.strip():
        history = history[:-1]
    selected: list[dict[str, Any]] = []
    remaining = _MAX_CONTEXT_CHARS
    for raw in reversed(history[-48:]):
        role = str(raw.get("role") or "user")
        if role not in {"system", "user", "assistant"}:
            continue
        content = str(raw.get("content") or "").strip()
        if not content or remaining <= 0 or _is_persisted_provider_failure(role, content):
            continue
        content = content[-min(len(content), _MAX_MESSAGE_CHARS, remaining):]
        remaining -= len(content)
        selected.append({"role": role, "content": content})
    selected.reverse()
    clean_attachments = [item for item in (attachments or []) if isinstance(item, dict) and item.get("path")]
    attachment_lines = [
        f"- {item.get('name') or Path(str(item['path'])).name} ({item.get('mime_type') or 'application/octet-stream'}): {item['path']}"
        for item in clean_attachments
    ]
    user_text = prompt
    if attachment_lines:
        user_text += (
            "\n\nAttachments available for this turn:\n"
            + "\n".join(attachment_lines)
            + "\nUse image input when present. Use read_attachment for readable non-image files. Do not claim an attachment is unavailable before using the provided input or tool."
        )
    user_content: str | list[dict[str, Any]] = user_text
    image_blocks: list[dict[str, Any]] = []
    for item in clean_attachments:
        mime_type = str(item.get("mime_type") or "").lower()
        path = Path(str(item.get("path") or "")).expanduser()
        if not mime_type.startswith("image/") or not path.is_file() or path.stat().st_size > 20 * 1024 * 1024:
            continue
        encoded = base64.b64encode(path.read_bytes()).decode("ascii")
        image_blocks.append({"type": "image_url", "image_url": {"url": f"data:{mime_type};base64,{encoded}"}})
    if image_blocks:
        user_content = [{"type": "text", "text": user_text}, *image_blocks]
    return tuple(
        [
            {
                "role": "system",
                "content": development_system_instruction(
                    metadata,
                    project_folder=project_folder,
                    autonomy_level=autonomy_level,
                ),
            },
            *selected,
            {"role": "user", "content": user_content},
        ]
    )
