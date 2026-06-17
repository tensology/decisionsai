"""Download manager API routes."""

from __future__ import annotations

from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from distr.core import download_jobs


class DownloadCreateRequest(BaseModel):
    urls: List[str] = Field(..., description="Video URLs to download")
    title: str = Field("", description="Optional label for the job")
    output_dir: Optional[str] = Field(None, description="Output folder (default ~/Downloads/DecisionsAI)")


def create_routes(templates_dir: Path, base_path: str = "") -> APIRouter:
    router = APIRouter()

    @router.get("/downloads")
    async def list_downloads(include_completed: bool = True):
        return {"jobs": download_jobs.list_jobs(include_completed=include_completed)}

    @router.get("/downloads/{job_id}")
    async def get_download(job_id: str):
        row = download_jobs.get_job(job_id)
        if not row:
            raise HTTPException(status_code=404, detail="Job not found")
        return row

    @router.post("/downloads")
    async def create_download(body: DownloadCreateRequest):
        urls = [u.strip() for u in (body.urls or []) if (u or "").strip()]
        if not urls:
            raise HTTPException(status_code=400, detail="urls is required")
        try:
            job_id = download_jobs.create_job(
                urls,
                title=body.title,
                output_dir=body.output_dir,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        download_jobs.start_ytdlp_job(job_id)
        return {
            "id": job_id,
            "manager_path": "/downloads/",
            "job": download_jobs.get_job(job_id),
        }

    @router.delete("/downloads/{job_id}")
    async def cancel_download(job_id: str):
        if not download_jobs.get_job(job_id):
            raise HTTPException(status_code=404, detail="Job not found")
        download_jobs.cancel_job(job_id)
        return {"id": job_id, "cancelled": True, "job": download_jobs.get_job(job_id)}

    return router
