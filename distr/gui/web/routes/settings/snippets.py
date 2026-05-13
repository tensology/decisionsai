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


def register_routes(router, templates):

    @router.post("/snippets")
    @route_handler("create snippet")
    async def create_snippet(payload: SnippetUpdate):
        """Create a new snippet"""
        from distr.core.db import get_session, Snippet
        text = _snippet_text(payload)
        with get_session() as session:
            snippet = Snippet(
                title=payload.title or _snippet_title(text),
                description=text,
                additional_trigger_words=payload.additional_trigger_words or "[]",
                remote_hotkey=payload.remote_hotkey or "",
            )
            session.add(snippet)
            session.commit()
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
            return JSONResponse({"success": True})
