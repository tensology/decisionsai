"""
Snippets routes — /snippets CRUD
"""
from fastapi import HTTPException
from fastapi.responses import JSONResponse

from ._shared import logger, SnippetUpdate, route_handler


def _snippet_text(payload: SnippetUpdate) -> str:
    if payload.text is not None:
        return payload.text or ""
    return payload.description or ""


def _snippet_title(text: str) -> str:
    first_line = (text or "").strip().splitlines()[0].strip() if (text or "").strip() else ""
    return (first_line[:80] if first_line else "Snippet")


def _snippet_response(snippet):
    text = snippet.description or ""
    return {
        "id": snippet.id,
        "title": snippet.title or "",
        "text": text,
        "description": text,
        "additional_trigger_words": snippet.additional_trigger_words or "[]",
        "remote_hotkey": snippet.remote_hotkey or "",
    }


def _snippet_summary_response(snippet, preview_limit: int = 160):
    text = snippet.description or ""
    preview = text[:preview_limit]
    if len(text) > preview_limit:
        preview = preview.rstrip() + "…"
    return {
        "id": snippet.id,
        "title": snippet.title or "",
        "text": preview,
        "description": preview,
        "preview": preview,
        "has_full_text": len(text) <= preview_limit,
        "additional_trigger_words": snippet.additional_trigger_words or "[]",
        "remote_hotkey": snippet.remote_hotkey or "",
    }


def _next_default_remote_hotkey(session, Snippet) -> str:
    used = {
        str(value or "").strip().lower()
        for (value,) in session.query(Snippet.remote_hotkey).all()
        if str(value or "").strip()
    }
    for idx in range(1, 10):
        candidate = f"ctrl+shift+{idx}"
        if candidate not in used:
            return candidate
    return ""


def _normalize_remote_hotkey(remote_hotkey: str | None) -> str:
    from distr.core.hotkeys import parse_remote_hotkey

    key_aliases = {
        "left_arrow": "left",
        "right_arrow": "right",
        "up_arrow": "up",
        "down_arrow": "down",
        "escape": "esc",
    }
    combo = parse_remote_hotkey(remote_hotkey)
    if not combo:
        return ""
    modifier, key = combo
    ordered_mods = []
    for token in ("control", "option", "shift", "command"):
        if token in str(modifier or "").split("_"):
            ordered_mods.append(
                {
                    "control": "ctrl",
                    "option": "alt",
                    "shift": "shift",
                    "command": "cmd",
                }[token]
            )
    normalized_key = key_aliases.get(str(key or "").strip().lower(), str(key or "").strip().lower())
    if not ordered_mods or not normalized_key:
        return ""
    return "+".join([*ordered_mods, normalized_key])


def _remote_hotkey_signature(remote_hotkey: str | None) -> tuple[tuple[str, ...], str] | None:
    from distr.core.hotkeys import parse_remote_hotkey

    key_aliases = {
        "left": "left_arrow",
        "right": "right_arrow",
        "up": "up_arrow",
        "down": "down_arrow",
        "esc": "escape",
    }
    combo = parse_remote_hotkey(remote_hotkey)
    if not combo:
        return None
    modifier, key = combo
    mods = tuple(token for token in ("control", "option", "shift", "command") if token in str(modifier or "").split("_"))
    normalized_key = key_aliases.get(str(key or "").strip().lower(), str(key or "").strip().lower())
    if not mods or not normalized_key:
        return None
    return (mods, normalized_key)


def _stored_shortcut_signature(modifier: str | None, key: str | None) -> tuple[tuple[str, ...], str] | None:
    mods = tuple(token for token in ("control", "option", "shift", "command") if token in str(modifier or "").split("_"))
    normalized_key = str(key or "").strip().lower()
    if not mods or not normalized_key:
        return None
    return (mods, normalized_key)


def _reserved_shortcut_signatures(settings: dict) -> dict[tuple[tuple[str, ...], str], str]:
    signatures: dict[tuple[tuple[str, ...], str], str] = {}

    def _add(label: str, modifier: str, key: str) -> None:
        signature = _stored_shortcut_signature(modifier, key)
        if signature:
            signatures[signature] = label

    _add("Previous skin", settings.get("skin_nav_hotkey_previous_modifier", "control_command"), settings.get("skin_nav_hotkey_previous_key", "left_arrow"))
    _add("Next skin", settings.get("skin_nav_hotkey_next_modifier", "control_command"), settings.get("skin_nav_hotkey_next_key", "right_arrow"))
    for idx in range(1, 10):
        _add(f"Skin {idx}", settings.get("skin_select_hotkey_modifier", "option_command"), str(idx))
    _add("Chat launcher", settings.get("web_hotkey_chat_modifier", "option_command"), settings.get("web_hotkey_chat_key", "c"))
    _add("Projects launcher", settings.get("web_hotkey_projects_modifier", "option_command"), settings.get("web_hotkey_projects_key", "j"))
    _add("Actions launcher", settings.get("web_hotkey_actions_modifier", "option_command"), settings.get("web_hotkey_actions_key", "a"))
    _add("Snippets launcher", settings.get("web_hotkey_snippets_modifier", "option_command"), settings.get("web_hotkey_snippets_key", "n"))
    _add("Workflows launcher", settings.get("web_hotkey_workflows_modifier", "option_command"), settings.get("web_hotkey_workflows_key", "w"))
    _add("Automations launcher", settings.get("web_hotkey_automations_modifier", "option_command"), settings.get("web_hotkey_automations_key", "o"))
    _add("Ticket board launcher", settings.get("web_hotkey_ticket_board_modifier", "option_command"), settings.get("web_hotkey_ticket_board_key", "t"))
    _add("IRC launcher", settings.get("web_hotkey_irc_modifier", "option_command"), settings.get("web_hotkey_irc_key", "i"))
    _add("Preferences launcher", settings.get("web_hotkey_preferences_modifier", "option_command"), settings.get("web_hotkey_preferences_key", "grave"))
    _add("Oracle size decrease", settings.get("oracle_size_hotkey_decrease_modifier", "control_command"), settings.get("oracle_size_hotkey_decrease_key", "down_arrow"))
    _add("Oracle size increase", settings.get("oracle_size_hotkey_increase_modifier", "control_command"), settings.get("oracle_size_hotkey_increase_key", "up_arrow"))
    _add("Recording", settings.get("recording_hotkey_modifier", "option_command"), settings.get("recording_hotkey_key", "s"))
    return signatures


def _validate_snippet_remote_hotkey(
    remote_hotkey: str | None,
    *,
    settings: dict,
    existing_snippets,
    current_snippet_id: int | None = None,
) -> str:
    normalized = _normalize_remote_hotkey(remote_hotkey)
    if not normalized:
        return ""

    signature = _remote_hotkey_signature(normalized)
    if not signature:
        raise ValueError("Invalid snippet hotkey.")

    reserved = _reserved_shortcut_signatures(settings)
    reserved_label = reserved.get(signature)
    if reserved_label:
        raise ValueError(f"Snippet hotkey overlaps {reserved_label}. Choose a different shortcut combo.")

    for snippet in existing_snippets:
        if getattr(snippet, "id", None) == current_snippet_id:
            continue
        if _remote_hotkey_signature(getattr(snippet, "remote_hotkey", "")) == signature:
            raise ValueError(
                f"Snippet hotkey overlaps Snippet {getattr(snippet, 'id', '?')}. Choose a different shortcut combo."
            )
    return normalized


def _notify_snippet_hotkeys_changed() -> None:
    try:
        from distr.core.services.settings_service import _run_on_qt_main_thread, _safe_emit
        from distr.core.signals import signal_manager

        def _do():
            _safe_emit(
                signal_manager.shortcut_settings_changed,
                label="snippet_hotkeys_changed",
            )

        _run_on_qt_main_thread(_do, label="snippet_hotkeys_changed")
    except Exception as exc:
        logger.debug("Could not notify live hotkey listener about snippet change: %s", exc)


def register_routes(router, templates):

    @router.post("/snippets")
    @route_handler("create snippet")
    async def create_snippet(payload: SnippetUpdate):
        """Create a new snippet"""
        from distr.core.db import get_session, Snippet
        from distr.core.services.settings_service import load_settings_from_db
        text = _snippet_text(payload)
        with get_session() as session:
            remote_hotkey = _validate_snippet_remote_hotkey(
                (payload.remote_hotkey or "").strip() or _next_default_remote_hotkey(session, Snippet),
                settings=load_settings_from_db(),
                existing_snippets=session.query(Snippet).all(),
            )
            snippet = Snippet(
                title=payload.title or _snippet_title(text),
                description=text,
                additional_trigger_words=payload.additional_trigger_words or "[]",
                remote_hotkey=remote_hotkey,
            )
            session.add(snippet)
            session.commit()
            _notify_snippet_hotkeys_changed()
            return JSONResponse(_snippet_response(snippet))

    @router.get("/snippets/summary")
    @route_handler("load snippet summaries")
    async def get_snippets_summary():
        """Lightweight snippet list for remote UI — omits large bodies."""
        from distr.core.db import get_session, Snippet
        with get_session() as session:
            snippets = session.query(Snippet).order_by(Snippet.modified_date.desc()).all()
            return JSONResponse([_snippet_summary_response(s) for s in snippets])

    @router.get("/snippets/{snippet_id}")
    @route_handler("load snippet")
    async def get_snippet(snippet_id: int):
        """Load one snippet with full text."""
        from distr.core.db import get_session, Snippet
        with get_session() as session:
            snippet = session.query(Snippet).filter(Snippet.id == snippet_id).first()
            if not snippet:
                raise HTTPException(status_code=404, detail="Snippet not found")
            return JSONResponse(_snippet_response(snippet))

    @router.get("/snippets")
    @route_handler("load snippets")
    async def get_snippets_list():
        """Get list of snippets for the Snippets page"""
        from distr.core.db import get_session, Snippet
        with get_session() as session:
            snippets = session.query(Snippet).order_by(Snippet.modified_date.desc()).all()
            return JSONResponse([_snippet_response(s) for s in snippets])

    @router.put("/snippets/{snippet_id}")
    @route_handler("update snippet")
    async def update_snippet(snippet_id: int, payload: SnippetUpdate):
        """Update a snippet by id"""
        from distr.core.db import get_session, Snippet
        from distr.core.services.settings_service import load_settings_from_db
        with get_session() as session:
            snippet = session.query(Snippet).filter(Snippet.id == snippet_id).first()
            if not snippet:
                raise HTTPException(status_code=404, detail="Snippet not found")
            if payload.title is not None:
                snippet.title = payload.title
            if payload.text is not None or payload.description is not None:
                text = _snippet_text(payload)
                snippet.description = text
                if payload.title is None:
                    snippet.title = _snippet_title(text)
            if payload.additional_trigger_words is not None:
                snippet.additional_trigger_words = payload.additional_trigger_words
            if payload.remote_hotkey is not None:
                snippet.remote_hotkey = _validate_snippet_remote_hotkey(
                    payload.remote_hotkey or "",
                    settings=load_settings_from_db(),
                    existing_snippets=session.query(Snippet).all(),
                    current_snippet_id=snippet_id,
                )
            session.commit()
            _notify_snippet_hotkeys_changed()
            return JSONResponse(_snippet_response(snippet))

    @router.delete("/snippets/{snippet_id}")
    @route_handler("delete snippet")
    async def delete_snippet(snippet_id: int):
        """Delete a snippet by id"""
        from distr.core.db import get_session, Snippet
        with get_session() as session:
            snippet = session.query(Snippet).filter(Snippet.id == snippet_id).first()
            if not snippet:
                raise HTTPException(status_code=404, detail="Snippet not found")
            session.delete(snippet)
            session.commit()
            _notify_snippet_hotkeys_changed()
            return JSONResponse({"success": True})
