"""Incoming HTTP boundary. Existing URLs remain compatible."""
from fastapi import Request
from fastapi.responses import JSONResponse
import asyncio
import logging

logger = logging.getLogger(__name__)


def register_routes(router, templates):
    @router.get("/workflows/intake/inbox")
    async def workflow_intake_inbox(limit: int = 40):
        """Mission Control inbox for channel WorkIntake decisions."""
        try:
            from distr.core.work_intake import get_work_intake_service

            items = await asyncio.to_thread(get_work_intake_service().list_inbox, limit=limit)
            return JSONResponse({"items": items})
        except Exception as e:
            logger.error("Workflow intake inbox failed: %s", e, exc_info=True)
            return JSONResponse({"detail": str(e)}, status_code=500)


    @router.get("/workflows/studio/incoming")
    async def workflow_studio_incoming(limit: int = 100):
        """Unified channel messages and board links for Development."""
        try:
            from distr.core.incoming.service import list_development_incoming

            return JSONResponse(await asyncio.to_thread(list_development_incoming, limit=limit))
        except Exception as e:
            logger.error("Development incoming load failed: %s", e, exc_info=True)
            return JSONResponse({"detail": str(e)}, status_code=500)


    @router.post("/workflows/intake/inbox/{event_id}/action")
    async def workflow_intake_inbox_action(event_id: int, request: Request):
        """Continue / stop / steer / push / dismiss an inbox item."""
        try:
            body = await request.json()
        except Exception:
            body = {}
        action = str((body or {}).get("action") or "").strip()
        message = str((body or {}).get("message") or "").strip()
        try:
            from distr.core.work_intake import get_work_intake_service

            result = await asyncio.to_thread(
                get_work_intake_service().resolve_inbox_item,
                int(event_id),
                action=action,
                message=message,
            )
            status = 200 if result.get("success") else 400
            return JSONResponse(result, status_code=status)
        except Exception as e:
            logger.error("Workflow intake inbox action failed: %s", e, exc_info=True)
            return JSONResponse({"success": False, "error": str(e)}, status_code=500)


    @router.post("/workflows/intake/ingest")
    async def workflow_intake_ingest(request: Request):
        """Web Mission Control entry into the shared WorkIntake pipeline."""
        try:
            body = await request.json()
        except Exception:
            body = {}
        try:
            from distr.core.work_intake import WorkIntake, get_work_intake_service

            payload = dict(body or {})
            payload.setdefault("source", "web")
            decision = await asyncio.to_thread(
                get_work_intake_service().ingest,
                WorkIntake.from_payload(payload),
            )
            return JSONResponse({"success": True, "decision": decision.to_dict()})
        except Exception as e:
            logger.error("Workflow intake ingest failed: %s", e, exc_info=True)
            return JSONResponse({"success": False, "error": str(e)}, status_code=500)


    @router.post("/workflows/incoming/gmail/ingest")
    async def workflow_incoming_gmail_ingest(request: Request):
        """Promote a displayed Gmail thread into WorkIntake(source=gmail)."""
        try:
            body = await request.json()
        except Exception:
            body = {}
        try:
            from distr.core.incoming.service import ingest_gmail_as_work

            decision = await asyncio.to_thread(
                ingest_gmail_as_work,
                subject=str((body or {}).get("subject") or ""),
                body=str((body or {}).get("text") or (body or {}).get("body") or ""),
                message_id=str((body or {}).get("source_message_id") or (body or {}).get("message_id") or ""),
                thread_id=str((body or {}).get("source_thread_id") or (body or {}).get("thread_id") or ""),
                sender=str((body or {}).get("sender") or ""),
                board_hint=str((body or {}).get("board_hint") or ""),
                project_hint=str((body or {}).get("project_hint") or ""),
                force_ticket=bool((body or {}).get("force_ticket", True)),
            )
            return JSONResponse({"success": True, "decision": decision})
        except Exception as e:
            logger.error("Gmail WorkIntake ingest failed: %s", e, exc_info=True)
            return JSONResponse({"success": False, "error": str(e)}, status_code=500)


