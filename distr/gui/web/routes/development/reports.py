"""Reports HTTP boundary. Read-only; no execution or schedule mutations."""
import asyncio
from fastapi import Query
from fastapi.responses import JSONResponse

def register_routes(router, templates):
    @router.get("/workflows/studio/reports")
    async def development_reports(limit: int = Query(100, ge=1, le=200)):
        from distr.core.reports.service import list_reports
        return JSONResponse(await asyncio.to_thread(list_reports, limit=limit))

    @router.get("/workflows/studio/reports/time")
    async def development_time_reports(limit: int = Query(100, ge=1, le=200)):
        from distr.core.reports.service import list_time_entries
        return JSONResponse(await asyncio.to_thread(list_time_entries, limit=limit))

    @router.get("/workflows/studio/reports/costs")
    async def development_cost_reports(
        limit: int = Query(100, ge=1, le=500),
        display: str | None = Query(None, description="blended|explicit; overrides settings for this view"),
        project_id: int | None = Query(None),
        client_key: str | None = Query(None),
        run_id: int | None = Query(None),
        source: str | None = Query(None),
        rollups: bool = Query(False, description="Include by_project / by_client / by_day rollups"),
    ):
        from distr.core.reports.service import cost_rollups, list_cost_entries

        payload = await asyncio.to_thread(
            list_cost_entries,
            limit=limit,
            display=display,
            project_id=project_id,
            client_key=client_key,
            run_id=run_id,
            source=source,
        )
        if rollups:
            payload["rollups"] = await asyncio.to_thread(cost_rollups, display=display or payload.get("display"))
        return JSONResponse(payload)
