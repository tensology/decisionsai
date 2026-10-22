"""HTTP routes for the Laya submission API.

POST requires the internal API token (enforced globally for non-GET /api/*
by the security middleware in server.py). GET is read-only status.
"""

from __future__ import annotations

import asyncio
from typing import Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

router = APIRouter(prefix="/api/laya", tags=["laya"])


class LayaSubmissionRequest(BaseModel):
    text: str = Field(..., min_length=1)
    source: Optional[str] = None
    project: Optional[str] = None  # project id or name hint
    thread_id: Optional[int] = None
    new_thread: bool = False


@router.post("/submissions")
async def create_submission(body: LayaSubmissionRequest):
    from distr.core import laya_submissions as svc

    try:
        sub = await asyncio.to_thread(
            svc.submit, body.text, source=body.source or "", project=body.project,
            thread_id=body.thread_id, new_thread=body.new_thread,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    code = 202 if sub.status != "failed" else 422
    return JSONResponse(sub.to_dict(), status_code=code)


@router.get("/submissions/{submission_id}")
async def get_submission(submission_id: str):
    from distr.core import laya_submissions as svc

    sub = svc.get_submission(submission_id)
    if sub is None:
        raise HTTPException(status_code=404, detail="Submission not found")
    return JSONResponse(sub.to_dict())
