"""
Masko skin generation API routes.

Provides endpoints for credit balance, style listing, skin generation,
progress polling, cancellation, and retry.
"""
import uuid
from pathlib import Path
from typing import Dict, List, Literal, Optional

from fastapi import HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from ._shared import logger, route_handler


# ---------------------------------------------------------------------------
# Pydantic request/response models
# ---------------------------------------------------------------------------

class GenerateRequest(BaseModel):
    skin_name: str
    description: str
    style: str
    mode: Literal["static", "animated"]


class GenerateResponse(BaseModel):
    generation_id: str
    total_jobs: int
    estimated_credits: int


class GenerationStatusResponse(BaseModel):
    status: Literal["pending", "in_progress", "complete", "failed", "cancelled"]
    completed_jobs: int
    total_jobs: int
    current_hook: Optional[str]
    hook_statuses: Dict[str, str]
    errors: List[str]
    skin_name: Optional[str]


class RetryRequest(BaseModel):
    generation_id: str
    hooks: Optional[List[str]] = None


class CreditBalanceResponse(BaseModel):
    credits: int


class StylesResponse(BaseModel):
    styles: List[dict]


# ---------------------------------------------------------------------------
# Module-level generator registry
# ---------------------------------------------------------------------------

_generators: Dict[str, object] = {}  # generation_id -> SkinGenerator


def _get_masko_client():
    """Create a MaskoClient from current settings."""
    from distr.core.settings import load_settings_from_db
    from distr.core.integrations.masko import MaskoClient

    settings = load_settings_from_db()
    key = (settings.get("masko_key") or "").strip()
    enabled = settings.get("masko_enabled", False)

    if not enabled or not key:
        raise HTTPException(status_code=400, detail="Masko is not enabled or API key not configured")

    return MaskoClient(key)


def _estimate_credits(mode: str) -> int:
    """Calculate estimated credits for a generation mode."""
    if mode == "static":
        return 12  # 1 credit per image × 12 hooks
    else:
        # 252 for poses (21 × 12) + 120 for transitions (5 × 4s × 6 forward transitions)
        return 252 + 120


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


def register_routes(router, templates):

    @router.get("/masko/credits")
    @route_handler("fetch Masko credit balance")
    async def get_masko_credits():
        """Fetch current Masko credit balance."""
        client = _get_masko_client()
        try:
            credits = client.get_credits()
            return JSONResponse({"credits": credits})
        except Exception as e:
            raise HTTPException(status_code=400, detail=str(e))

    @router.get("/masko/styles")
    @route_handler("fetch Masko styles")
    async def get_masko_styles():
        """Fetch available Masko generation styles."""
        client = _get_masko_client()
        try:
            styles = client.list_styles()
            return JSONResponse({
                "styles": [
                    {"id": s.id, "name": s.name, "preview_url": s.preview_url}
                    for s in styles
                ]
            })
        except Exception as e:
            raise HTTPException(status_code=400, detail=str(e))

    @router.post("/skins/generate")
    @route_handler("start skin generation")
    async def start_skin_generation(data: GenerateRequest):
        """Start a Masko skin generation job."""
        from distr.core.integrations.masko.generator import SkinGenerator
        from distr.core.integrations.masko.models import sanitize_skin_name
        from distr.core.paths import AVATARS_DIR

        # Validate inputs
        sanitized = sanitize_skin_name(data.skin_name)
        if not sanitized:
            raise HTTPException(
                status_code=400,
                detail="Skin name is invalid — it contains only special characters or whitespace. Please choose a different name.",
            )

        skin_dir = Path(AVATARS_DIR) / sanitized
        if skin_dir.exists():
            raise HTTPException(
                status_code=400,
                detail=f"A skin folder named '{sanitized}' already exists. Please choose a different name.",
            )

        if not data.description.strip():
            raise HTTPException(status_code=400, detail="Character description cannot be empty.")

        client = _get_masko_client()
        generator = SkinGenerator(client, AVATARS_DIR)

        generation_id = str(uuid.uuid4())
        _generators[generation_id] = generator

        estimated_credits = _estimate_credits(data.mode)

        generator.start(
            generation_id=generation_id,
            name=data.skin_name,
            description=data.description.strip(),
            style=data.style,
            mode=data.mode,
        )

        return JSONResponse({
            "generation_id": generation_id,
            "total_jobs": 12,
            "estimated_credits": estimated_credits,
        })

    @router.get("/skins/generate/status")
    @route_handler("poll skin generation status")
    async def get_skin_generation_status(id: str):
        """Poll the status of an in-progress skin generation.

        Returns immediately with current in-memory state (non-blocking).
        """
        generator = _generators.get(id)
        if generator is None:
            raise HTTPException(status_code=404, detail=f"Generation '{id}' not found")

        status = generator.get_status(id)
        return JSONResponse({
            "status": status.status,
            "completed_jobs": status.completed_jobs,
            "total_jobs": status.total_jobs,
            "current_hook": status.current_hook,
            "hook_statuses": status.hook_statuses,
            "errors": status.errors,
            "skin_name": status.skin_name,
        })

    @router.post("/skins/generate/cancel")
    @route_handler("cancel skin generation")
    async def cancel_skin_generation(body: dict):
        """Cancel an in-progress skin generation."""
        generation_id = body.get("id") or body.get("generation_id", "")
        generator = _generators.get(generation_id)
        if generator is None:
            raise HTTPException(status_code=404, detail=f"Generation '{generation_id}' not found")

        generator.cancel(generation_id)
        return JSONResponse({"success": True, "message": "Generation cancelled"})

    @router.post("/skins/generate/retry")
    @route_handler("retry failed skin generation hooks")
    async def retry_skin_generation(data: RetryRequest):
        """Retry failed hooks for an in-progress generation."""
        generator = _generators.get(data.generation_id)
        if generator is None:
            raise HTTPException(
                status_code=404,
                detail=f"Generation '{data.generation_id}' not found",
            )

        generator.retry_failed(data.generation_id, hooks=data.hooks)

        # Return updated status
        status = generator.get_status(data.generation_id)
        return JSONResponse({
            "status": status.status,
            "completed_jobs": status.completed_jobs,
            "total_jobs": status.total_jobs,
            "current_hook": status.current_hook,
            "hook_statuses": status.hook_statuses,
            "errors": status.errors,
            "skin_name": status.skin_name,
        })