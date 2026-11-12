"""
Initiative attention routes — /initiative/attention

Durable "set aside" and routing state used by Initiative (ignored clients, focus,
email address -> project links) plus who is still waiting behind an ignore.
Added 12 Nov 2026 (Initiative audit fixes). Read with GET; writes are explicit
user actions from DecisionsAI (UI or agent tool).
"""
import logging

from fastapi.responses import JSONResponse
from pydantic import BaseModel

logger = logging.getLogger(__name__)


class _ClientPayload(BaseModel):
    client: str
    note: str = ""
    whole_domain: bool = False


class _EmailLinkPayload(BaseModel):
    address: str
    project: str
    note: str = ""


def register_routes(router, templates):

    @router.get("/initiative/attention")
    async def get_initiative_attention():
        try:
            from distr.core.initiative import attention
            return JSONResponse({**attention.snapshot(), "waiting": attention.waiting_tensions()})
        except Exception as e:
            logger.error("Failed to read Initiative attention: %s", e, exc_info=True)
            return JSONResponse({"error": str(e)}, status_code=500)

    @router.post("/initiative/attention/clients")
    async def ignore_initiative_client(payload: _ClientPayload):
        try:
            from distr.core.initiative import attention
            entry = attention.ignore_client(payload.client, label=payload.client, note=payload.note,
                                            whole_domain=payload.whole_domain)
            return JSONResponse({"success": True, "client": entry})
        except ValueError as e:
            return JSONResponse({"success": False, "error": str(e)}, status_code=400)

    @router.delete("/initiative/attention/clients/{client}")
    async def unignore_initiative_client(client: str):
        from distr.core.initiative import attention
        return JSONResponse({"success": attention.unignore_client(client)})

    @router.post("/initiative/attention/email-links")
    async def link_initiative_email(payload: _EmailLinkPayload):
        try:
            from distr.core.initiative import attention
            return JSONResponse({"success": True, "link": attention.link_address(payload.address, payload.project,
                                                                                  note=payload.note)})
        except ValueError as e:
            return JSONResponse({"success": False, "error": str(e)}, status_code=400)

    @router.delete("/initiative/attention/email-links")
    async def unlink_initiative_email(address: str, project: str = ""):
        try:
            from distr.core.initiative import attention
            return JSONResponse({"success": attention.unlink_address(address, project or None)})
        except ValueError as e:
            return JSONResponse({"success": False, "error": str(e)}, status_code=400)
