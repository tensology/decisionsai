"""Reports HTTP boundary. Read-only; no execution or schedule mutations."""
import asyncio
from fastapi import Query
from fastapi.responses import JSONResponse

def register_routes(router, templates):
    @router.get("/workflows/studio/reports")
    async def development_reports(limit: int = Query(100, ge=1, le=200)):
        from distr.core.reports.service import list_reports
        return JSONResponse(await asyncio.to_thread(list_reports, limit=limit))
