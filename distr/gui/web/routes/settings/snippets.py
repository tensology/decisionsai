"""
Snippets routes — /snippets CRUD
"""
from fastapi import HTTPException
from fastapi.responses import JSONResponse

from ._shared import logger, SnippetUpdate, route_handler


def register_routes(router, templates):

    @router.post("/snippets")
    @route_handler("create snippet")
    async def create_snippet(payload: SnippetUpdate):
        """Create a new snippet"""
        from distr.core.db import get_session, Snippet
        with get_session() as session:
            snippet = Snippet(
                title=payload.title or "New Snippet",
                description=payload.description or "",
                additional_trigger_words=payload.additional_trigger_words or "[]",
            )
            session.add(snippet)
            session.commit()
            return JSONResponse({
                "id": snippet.id,
                "title": snippet.title,
                "description": snippet.description,
                "additional_trigger_words": snippet.additional_trigger_words,
            })

    @router.get("/snippets")
    @route_handler("load snippets")
    async def get_snippets_list():
        """Get list of snippets for the Snippets page"""
        from distr.core.db import get_session, Snippet
        with get_session() as session:
            snippets = session.query(Snippet).order_by(Snippet.modified_date.desc()).all()
            return JSONResponse([{
                "id": s.id, "title": s.title or "",
                "description": s.description or "",
                "additional_trigger_words": s.additional_trigger_words or "[]",
            } for s in snippets])

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
            if payload.description is not None:
                snippet.description = payload.description
            if payload.additional_trigger_words is not None:
                snippet.additional_trigger_words = payload.additional_trigger_words
            session.commit()
            return JSONResponse({"success": True})

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
