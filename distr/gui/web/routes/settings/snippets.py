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
        text = _snippet_text(payload)
        with get_session() as session:
            remote_hotkey = (payload.remote_hotkey or "").strip() or _next_default_remote_hotkey(session, Snippet)
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
                snippet.remote_hotkey = payload.remote_hotkey or ""
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
