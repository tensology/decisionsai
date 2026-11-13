"""Reports HTTP boundary. No execution or schedule mutations.

The run/time/cost lists are read-only. The period report reads the same records,
and Build stores one analysis row (work_reports) per request.
"""
import asyncio
from datetime import datetime
from fastapi import Query
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict


class PeriodReportBuild(BaseModel):
    month: str | None = None
    start: str | None = None
    end: str | None = None
    projects: list[int] | None = None
    provider: str | None = None
    model: str | None = None
    include_automations: bool = False


class AssignProjectBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source: str
    ref: str
    project_id: int
    confirm: str
    source_start: str | None = None
    source_end: str | None = None


class CalendarEntryBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    entry_id: int | None = None
    draft_id: str | None = None
    version: str | None = None
    original_start: datetime | None = None
    original_end: datetime | None = None
    title: str
    start_at: datetime
    end_at: datetime
    project_id: int | None


class CalendarEntryDeleteBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    entry_id: int | None = None
    draft_id: str | None = None
    version: str | None = None
    original_start: datetime | None = None
    original_end: datetime | None = None


async def _calendar_call(operation, *args, **kwargs):
    from fastapi import HTTPException
    from distr.core.reports.calendar_sync import CalendarEditError
    from distr.core.tensology_client import TensologyApiError

    try:
        return await asyncio.to_thread(operation, *args, **kwargs)
    except CalendarEditError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc
    except TensologyApiError as exc:
        raise HTTPException(status_code=exc.status or 502, detail=f"Tensology calendar: {exc}") from exc


def _period_args(month, start, end, projects):
    from distr.core.reports.month import parse_project_ids, resolve_period
    try:
        if not any((month, start, end)):
            from distr.core.reports.calendar_review import load_review
            from distr.core.reports.month import today_sast
            review = load_review()
            if review and review['period']['end'] == today_sast().isoformat():
                start, end = review['period']['start'], review['period']['end']
        return resolve_period(month=month, start=start, end=end), parse_project_ids(projects)
    except ValueError as exc:
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail=str(exc)) from exc

def register_routes(router, templates):
    @router.get("/workflows/studio/reports/source-thread")
    async def report_source_thread(ref: str, before: int | None = Query(None, ge=0), start: str | None = None, end: str | None = None, all_messages: bool = False):
        from fastapi import HTTPException
        from distr.core.reports.source_thread import read_source_thread
        try:
            from distr.core.reports.month import _parse_ts
            lo, hi = _parse_ts(start) if start else None, _parse_ts(end) if end else None
            if (start or end) and (lo is None or hi is None or hi <= lo):
                raise ValueError('Provide a valid start and end for the time entry.')
            return JSONResponse(await asyncio.to_thread(read_source_thread, ref, before=before, start=lo, end=hi, all_messages=all_messages),
                                headers={"Cache-Control": "no-store"})
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @router.get("/workflows/studio/reports")
    async def development_reports(limit: int = Query(100, ge=1, le=200)):
        from distr.core.reports.service import list_reports
        return JSONResponse(await asyncio.to_thread(list_reports, limit=limit))

    @router.get("/workflows/studio/reports/time")
    async def development_time_reports(limit: int = Query(100, ge=1, le=200)):
        from distr.core.reports.service import list_time_entries
        return JSONResponse(await asyncio.to_thread(list_time_entries, limit=limit))

    @router.get("/workflows/studio/reports/time-window")
    async def development_time_window(
        start: str = Query(..., description="YYYY-MM-DD Africa/Johannesburg, inclusive"),
        end: str = Query(..., description="YYYY-MM-DD Africa/Johannesburg, inclusive"),
    ):
        """Stored timer rows in the window, including rows History hides. Read only."""
        from datetime import date
        from fastapi import HTTPException
        from distr.core.reports.time_window import list_timer_rows

        try:
            start_day, end_day = date.fromisoformat(start), date.fromisoformat(end)
            payload = await asyncio.to_thread(list_timer_rows, start_day, end_day, None)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return JSONResponse(payload)

    @router.get("/workflows/studio/reports/history")
    async def development_work_history(limit: int = Query(100, ge=1, le=200)):
        from distr.core.reports.service import list_work_history
        return JSONResponse(await asyncio.to_thread(list_work_history, limit=limit))

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

    @router.post("/workflows/studio/reports/assign-project")
    async def development_assign_project(data: AssignProjectBody):
        from distr.core.db import get_session
        from distr.core.reports.projection_store import ProjectionStoreError, assign_project_manual

        try:
            return JSONResponse(await asyncio.to_thread(assign_project_manual, get_session, data.model_dump()))
        except ProjectionStoreError as exc:
            return JSONResponse(exc.body(), status_code=exc.status)

    @router.post("/workflows/studio/reports/calendar-entry")
    async def development_save_calendar_entry(data: CalendarEntryBody):
        from distr.core.reports.calendar_sync import save_entry

        return JSONResponse(await _calendar_call(save_entry, **data.model_dump()),
                            headers={"Cache-Control": "no-store"})

    @router.post("/workflows/studio/reports/calendar-entry/delete")
    async def development_delete_calendar_entry(data: CalendarEntryDeleteBody):
        from distr.core.reports.calendar_sync import delete_entry

        return JSONResponse(await _calendar_call(delete_entry, **data.model_dump()),
                            headers={"Cache-Control": "no-store"})

    @router.get("/workflows/studio/reports/period")
    async def development_period_report(
        month: str | None = Query(None, description="YYYY-MM calendar month"),
        start: str | None = Query(None, description="YYYY-MM-DD (Africa/Johannesburg)"),
        end: str | None = Query(None, description="YYYY-MM-DD inclusive"),
        projects: str | None = Query(None, description="Comma-separated project ids; empty = all; 0 = unassigned"),
        saved_projects: str | None = Query(None, description="Project selection for the last built report"),
        include_automations: bool = Query(False),
        calendar_only: bool = Query(False, description="Live time-entry refresh: reuse recent local collation, re-read Tensology"),
    ):
        from distr.core.reports.month import get_period_report

        period, project_ids = _period_args(month, start, end, projects)
        _, saved_project_ids = _period_args(month, start, end, saved_projects) if saved_projects is not None else (period, None)
        return JSONResponse(await _calendar_call(
            get_period_report, period, project_ids, saved_project_ids=saved_project_ids, include_automations=include_automations,
            calendar_only=calendar_only),
            headers={"Cache-Control": "no-store"})

    @router.post("/workflows/studio/reports/period/build")
    async def development_period_report_build(data: PeriodReportBuild):
        from fastapi import HTTPException
        from distr.core.reports.month import build_period_report

        period, project_ids = _period_args(data.month, data.start, data.end, data.projects)
        try:
            payload = await _calendar_call(
                build_period_report, period, project_ids, provider=data.provider, model=data.model,
                include_automations=data.include_automations)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return JSONResponse(payload)

    @router.get("/workflows/studio/reports/period/pdf")
    async def development_period_report_pdf(
        month: str | None = Query(None),
        start: str | None = Query(None),
        end: str | None = Query(None),
        projects: str | None = Query(None),
        include_automations: bool = Query(False),
        download: bool = Query(False),
    ):
        from distr.core.reports.month import get_period_report, render_pdf

        period, project_ids = _period_args(month, start, end, projects)
        snapshot = await _calendar_call(
            get_period_report, period, project_ids, include_automations=include_automations, invoice_only=True)
        pdf = await asyncio.to_thread(render_pdf, snapshot)
        filename = f"work-report-{period.start.isoformat()}-to-{period.end.isoformat()}.pdf"
        disposition = "attachment" if download else "inline"
        return Response(content=pdf, media_type="application/pdf",
                        headers={"Content-Disposition": f'{disposition}; filename="{filename}"'})
