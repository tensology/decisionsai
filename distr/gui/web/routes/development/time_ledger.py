"""HTTP boundary for the durable time ledger. No thread, ticket, or timer side effects."""
from __future__ import annotations

import asyncio

from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, StrictInt

from distr.core.workflow.time_ledger import (
    LedgerError,
    attach_reader,
    create_entry,
    default_backup_dir,
    delete_entry,
    export_entries,
    read_backup,
    restore_backup,
)


class BackupRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    durable_ids: list[str] = Field(min_length=1, max_length=100)


class CreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    started_at: str
    ended_at: str
    project_id: StrictInt | None = None
    source: str
    label: str
    seconds: StrictInt | None = None
    idempotency_key: str | None = None


class DeleteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    durable_id: str
    confirm: str
    fingerprint: str
    backup_id: str
    window_start: str
    window_end: str


class RestoreRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    backup_id: str
    confirm: str


def _run(func, *args):
    try:
        return JSONResponse(func(*args))
    except LedgerError as exc:
        return JSONResponse(exc.body(), status_code=exc.status)


def register_routes(router, templates, get_session=None, backup_dir=None):
    del templates
    attach_reader()
    session_factory = get_session or _session
    directory = backup_dir or default_backup_dir()

    @router.post("/workflows/studio/time-ledger/backups")
    async def export_time_ledger(data: BackupRequest):
        return await asyncio.to_thread(_run, export_entries, session_factory, directory, data.durable_ids)

    @router.get("/workflows/studio/time-ledger/backups/{backup_id}")
    async def read_time_ledger_backup(backup_id: str):
        return await asyncio.to_thread(_run, read_backup, directory, backup_id)

    @router.post("/workflows/studio/time-ledger/entries")
    async def create_historical_time_entry(data: CreateRequest):
        body = data.model_dump(exclude_unset=True)
        return await asyncio.to_thread(_run, create_entry, session_factory, body)

    @router.post("/workflows/studio/time-ledger/delete")
    async def delete_time_ledger_entry(data: DeleteRequest):
        return await asyncio.to_thread(_run, delete_entry, session_factory, directory, data.model_dump())

    @router.post("/workflows/studio/time-ledger/restore")
    async def restore_time_ledger(data: RestoreRequest):
        return await asyncio.to_thread(_run, restore_backup, session_factory, directory, data.model_dump())


def _session():
    from distr.core.db import get_session
    return get_session()
