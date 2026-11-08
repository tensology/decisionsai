"""Narrow write boundary for projection backups and reporting corrections."""
from __future__ import annotations

import asyncio
from datetime import datetime

from fastapi import Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict

from distr.core.reports.work_evidence import read_work_evidence
from distr.core.reports.projection_store import (
    ProjectionStoreError,
    apply_correction,
    attach_reporting_corrections,
    create_backup,
    evidence,
    revoke_correction,
    restore_backup,
)
from distr.core.workflow.time_ledger import default_backup_dir


class BackupBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sources: list[str]
    window_start: str
    window_end: str
    confirm: str


class ApplyBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    backup_id: str
    source: str
    ref: str
    fingerprint: str
    reason: str
    provenance: str
    confirm: str


class RevokeBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    correction_id: int
    fingerprint: str
    backup_id: str
    confirm: str


class RestoreBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    backup_id: str
    refs: list[str]
    confirm: str


def _parse(value: str) -> datetime:
    from datetime import timezone
    text = value[:-1] + "+00:00" if value.endswith("Z") else value
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        raise ValueError("window timestamps must include a timezone offset")
    return parsed.astimezone(timezone.utc).replace(tzinfo=None)


def _run(func, *args):
    try:
        return JSONResponse(func(*args))
    except ProjectionStoreError as exc:
        return JSONResponse(exc.body(), status_code=exc.status)
    except ValueError as exc:
        return JSONResponse({"detail": {"code": "invalid_time", "message": str(exc)}}, status_code=400)


def register_routes(router, templates, get_session=None, backup_dir=None):
    del templates
    attach_reporting_corrections(get_session)
    directory = backup_dir or (default_backup_dir().parent / "projection_store_backups")
    if get_session is None:
        from distr.core.db import get_session as get_session
    session_factory = get_session

    @router.post("/workflows/studio/projection-store/backups")
    async def backup_projection_store(data: BackupBody):
        if data.confirm != "backup-projection-store":
            return JSONResponse({"detail": {"code": "confirmation", "message": "confirm must be backup-projection-store"}}, status_code=409)
        lo, hi = _parse(data.window_start), _parse(data.window_end)
        return await asyncio.to_thread(_run, create_backup, session_factory, directory, data.sources, lo, hi)

    @router.get("/workflows/studio/projection-store/evidence")
    async def projection_evidence(backup_id: str = Query(...)):
        return await asyncio.to_thread(_run, evidence, session_factory, directory, backup_id)

    @router.get("/workflows/studio/projection-store/work-evidence")
    async def projection_work_evidence(
        request: Request,
        start: str = Query(...),
        end: str = Query(...),
        ids: list[str] = Query(...),
    ):
        from datetime import date
        from distr.gui.web.security import (
            EVIDENCE_AUTH_HEADER,
            EVIDENCE_GET_PATH,
            evidence_header_authorizes_read,
            get_internal_api_token,
        )
        import secrets
        presented = request.headers.get(EVIDENCE_AUTH_HEADER, "")
        path = request.url.path
        evidence_ok = evidence_header_authorizes_read(path, request.method, presented)
        expected = get_internal_api_token()
        internal = request.headers.get("X-DecisionsAI-Internal-Token", "").strip()
        internal_ok = bool(expected and internal and secrets.compare_digest(internal, expected))
        if path != EVIDENCE_GET_PATH or not (evidence_ok or internal_ok):
            return JSONResponse({"detail": "Unauthorized"}, status_code=401)

        def read():
            return read_work_evidence(
                session_factory, start=date.fromisoformat(start), end=date.fromisoformat(end), ids=ids,
            )

        return await asyncio.to_thread(_run, read)

    @router.post("/workflows/studio/projection-store/apply")
    async def apply_projection_correction(data: ApplyBody):
        return await asyncio.to_thread(_run, apply_correction, session_factory, directory, data.model_dump())

    @router.post("/workflows/studio/projection-store/revoke")
    async def revoke_projection_correction(data: RevokeBody):
        return await asyncio.to_thread(_run, revoke_correction, session_factory, data.model_dump())

    @router.post("/workflows/studio/projection-store/restore")
    async def restore_projection_backup(data: RestoreBody):
        return await asyncio.to_thread(_run, restore_backup, session_factory, directory, data.model_dump())
