"""Schedule block routes for the automations calendar."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from distr.core.db import get_session
from distr.core.db.schedule_blocks import ScheduleBlock
from distr.core.services import schedule_blocks as schedule_service


class ScheduleBlockPayload(BaseModel):
    title: str = Field(default="Untitled block")
    start_at: str
    end_at: str
    board_id: Optional[int] = None
    board_provider: str = Field(default="local")
    external_board_id: Optional[str] = None
    ticket_id: Optional[int] = None
    external_ticket_key: Optional[str] = None
    project_id: Optional[int] = None


class ScheduleBlockUpdate(BaseModel):
    title: Optional[str] = None
    start_at: Optional[str] = None
    end_at: Optional[str] = None
    board_id: Optional[int] = None
    board_provider: Optional[str] = None
    external_board_id: Optional[str] = None
    ticket_id: Optional[int] = None
    external_ticket_key: Optional[str] = None
    project_id: Optional[int] = None


class TimerStartPayload(BaseModel):
    title: str = Field(default="Timer")
    board_id: Optional[int] = None
    board_provider: str = Field(default="local")
    external_board_id: Optional[str] = None
    ticket_id: Optional[int] = None
    external_ticket_key: Optional[str] = None
    project_id: Optional[int] = None


def _parse_range(start: str, end: str) -> tuple[datetime, datetime]:
    try:
        start_dt = datetime.fromisoformat(start.replace("Z", ""))
        end_dt = datetime.fromisoformat(end.replace("Z", ""))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Invalid start or end datetime.") from exc
    if end_dt <= start_dt:
        end_dt = start_dt + timedelta(days=1)
    return start_dt.replace(microsecond=0), end_dt.replace(microsecond=0)


def create_routes() -> APIRouter:
    router = APIRouter()

    @router.get("/schedule-blocks")
    async def list_schedule_blocks(
        start: str = Query(...),
        end: str = Query(...),
    ):
        start_dt, end_dt = _parse_range(start, end)
        with get_session() as session:
            blocks = schedule_service.blocks_for_range(session, start_dt, end_dt)
            return JSONResponse({
                "blocks": [
                    schedule_service.serialize_block(session, block, extend_running_end=True)
                    for block in blocks
                ],
            })

    @router.post("/schedule-blocks")
    async def create_schedule_block(payload: ScheduleBlockPayload):
        with get_session() as session:
            if schedule_service.running_timer(session):
                raise HTTPException(status_code=409, detail="Stop the running timer before creating blocks.")
            try:
                block = schedule_service.create_block(
                    session,
                    title=payload.title,
                    start_at=payload.start_at,
                    end_at=payload.end_at,
                    board_id=payload.board_id,
                    board_provider=payload.board_provider,
                    external_board_id=payload.external_board_id,
                    ticket_id=payload.ticket_id,
                    external_ticket_key=payload.external_ticket_key,
                    project_id=payload.project_id,
                )
            except ValueError as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc
            return JSONResponse({
                "success": True,
                "block": schedule_service.serialize_block(session, block),
            })

    @router.patch("/schedule-blocks/{block_id}")
    async def update_schedule_block(block_id: int, payload: ScheduleBlockUpdate):
        with get_session() as session:
            block = session.query(ScheduleBlock).filter(ScheduleBlock.id == block_id).first()
            if not block:
                raise HTTPException(status_code=404, detail="Schedule block not found.")
            if schedule_service.running_timer(session) and not block.is_timer_running:
                raise HTTPException(status_code=409, detail="Stop the running timer before editing other blocks.")
            try:
                updated = schedule_service.update_block(session, block, payload.model_dump(exclude_unset=True))
            except ValueError as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc
            return JSONResponse({
                "success": True,
                "block": schedule_service.serialize_block(session, updated, extend_running_end=True),
            })

    @router.delete("/schedule-blocks/{block_id}")
    async def delete_schedule_block(block_id: int):
        with get_session() as session:
            block = session.query(ScheduleBlock).filter(ScheduleBlock.id == block_id).first()
            if not block:
                raise HTTPException(status_code=404, detail="Schedule block not found.")
            if schedule_service.running_timer(session) and not block.is_timer_running:
                raise HTTPException(status_code=409, detail="Stop the running timer before deleting other blocks.")
            session.delete(block)
            session.commit()
            return JSONResponse({"success": True})

    @router.post("/schedule-blocks/{block_id}/naturalize-time")
    async def naturalize_schedule_block(block_id: int):
        with get_session() as session:
            block = session.query(ScheduleBlock).filter(ScheduleBlock.id == block_id).first()
            if not block:
                raise HTTPException(status_code=404, detail="Schedule block not found.")
            if block.is_timer_running:
                raise HTTPException(status_code=409, detail="Stop the timer before naturalizing this block.")
            ok, message, updated = schedule_service.apply_naturalize(session, block)
            if not ok:
                raise HTTPException(status_code=409, detail=message)
            return JSONResponse({
                "success": True,
                "message": message,
                "block": schedule_service.serialize_block(session, updated),
            })

    @router.get("/schedule-blocks/timer")
    async def schedule_block_timer_status():
        with get_session() as session:
            block = schedule_service.running_timer(session)
            return JSONResponse({
                "running": bool(block),
                "block": schedule_service.serialize_block(session, block, extend_running_end=True) if block else None,
            })

    @router.post("/schedule-blocks/timer/start")
    async def start_schedule_block_timer(payload: TimerStartPayload):
        with get_session() as session:
            try:
                block = schedule_service.start_timer(
                    session,
                    title=payload.title,
                    board_id=payload.board_id,
                    board_provider=payload.board_provider,
                    external_board_id=payload.external_board_id,
                    ticket_id=payload.ticket_id,
                    external_ticket_key=payload.external_ticket_key,
                    project_id=payload.project_id,
                )
            except ValueError as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc
            return JSONResponse({
                "success": True,
                "block": schedule_service.serialize_block(session, block, extend_running_end=True),
            })

    @router.post("/schedule-blocks/timer/stop")
    async def stop_schedule_block_timer():
        with get_session() as session:
            block = schedule_service.stop_timer(session)
            if not block:
                return JSONResponse({"success": True, "block": None})
            return JSONResponse({
                "success": True,
                "block": schedule_service.serialize_block(session, block),
            })

    return router
