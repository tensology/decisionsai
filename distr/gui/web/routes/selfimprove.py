"""HTTP routes for the self-improvement approval queue.

GET lists proposals; POST approve/reject need the internal API token (enforced
globally for non-GET /api/* by the security middleware in server.py).
Approving applies the diff to the working tree only; never commits or pushes.
"""

from __future__ import annotations

import asyncio
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter(prefix="/api/selfimprove", tags=["selfimprove"])


class Decision(BaseModel):
    allow_dirty: bool = False
    note: Optional[str] = None


@router.get("/proposals")
async def list_proposals(status: str = "pending"):
    from distr.core.mempalace.selfimprove import approvals

    return {"proposals": approvals.list_proposals(None if status == "all" else status)}


@router.post("/proposals/{pid}/approve")
async def approve(pid: str, body: Decision | None = None):
    from distr.core.mempalace.selfimprove import approvals

    try:
        return await asyncio.to_thread(approvals.approve, pid, allow_dirty=bool(body and body.allow_dirty))
    except KeyError:
        raise HTTPException(404, f"no proposal {pid}")
    except ValueError as exc:
        raise HTTPException(409, str(exc))


@router.post("/proposals/{pid}/reject")
async def reject(pid: str, body: Decision | None = None):
    from distr.core.mempalace.selfimprove import approvals

    try:
        return await asyncio.to_thread(approvals.reject, pid, note=(body.note or "") if body else "")
    except KeyError:
        raise HTTPException(404, f"no proposal {pid}")
    except ValueError as exc:
        raise HTTPException(409, str(exc))
