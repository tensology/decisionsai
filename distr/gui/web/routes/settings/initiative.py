"""
Initiative routes — /initiative
"""
import logging
from fastapi.responses import JSONResponse
from pydantic import BaseModel

logger = logging.getLogger(__name__)

INITIATIVE_FIELDS = [
    "initiative_level",
    "initiative_allow_telegram",
    "initiative_allow_routine_tasks",
    "initiative_scan_boards",
    "initiative_scan_external_boards",
    "initiative_scan_email",
    "initiative_scan_whatsapp",
    "initiative_scan_telegram",
    "initiative_suggest_backlog_promotion",
    "initiative_allow_ticket_lane_moves",
    "initiative_allow_workflow_start",
    "initiative_allow_project_cli",
    "initiative_ask_external_comms",
    "initiative_ask_file_changes",
    "initiative_ask_sensitive",
]

DEFAULTS = {
    "initiative_level": "assist",
    "initiative_allow_telegram": False,
    "initiative_allow_routine_tasks": False,
    "initiative_scan_boards": True,
    "initiative_scan_external_boards": False,
    "initiative_scan_email": False,
    "initiative_scan_whatsapp": True,
    "initiative_scan_telegram": True,
    "initiative_suggest_backlog_promotion": True,
    "initiative_allow_ticket_lane_moves": False,
    "initiative_allow_workflow_start": False,
    "initiative_allow_project_cli": False,
    "initiative_ask_external_comms": True,
    "initiative_ask_file_changes": True,
    "initiative_ask_sensitive": True,
}


class _InitiativePayload(BaseModel):
    initiative_level: str = "assist"
    initiative_allow_telegram: bool = False
    initiative_allow_routine_tasks: bool = False
    initiative_scan_boards: bool = True
    initiative_scan_external_boards: bool = False
    initiative_scan_email: bool = False
    initiative_scan_whatsapp: bool = True
    initiative_scan_telegram: bool = True
    initiative_suggest_backlog_promotion: bool = True
    initiative_allow_ticket_lane_moves: bool = False
    initiative_allow_workflow_start: bool = False
    initiative_allow_project_cli: bool = False
    initiative_ask_external_comms: bool = True
    initiative_ask_file_changes: bool = True
    initiative_ask_sensitive: bool = True


def register_routes(router, templates):

    @router.get("/initiative")
    async def get_initiative_settings():
        """Get initiative/proactivity settings."""
        try:
            from distr.core.settings import load_settings_from_db
            settings = load_settings_from_db()
            result = {}
            for key in INITIATIVE_FIELDS:
                val = settings.get(key)
                if val is None:
                    result[key] = DEFAULTS.get(key)
                elif isinstance(val, str) and val.lower() in ("true", "false"):
                    result[key] = val.lower() == "true"
                else:
                    result[key] = val
            return JSONResponse(result)
        except Exception as e:
            logger.error(f"Failed to load initiative settings: {e}", exc_info=True)
            return JSONResponse(DEFAULTS)

    @router.post("/initiative")
    async def save_initiative_settings(payload: _InitiativePayload):
        """Save initiative/proactivity settings."""
        try:
            from distr.core.settings import save_settings_to_db
            data = payload.model_dump()
            save_settings_to_db(data)
            return JSONResponse({"success": True})
        except Exception as e:
            logger.error(f"Failed to save initiative settings: {e}", exc_info=True)
            return JSONResponse({"success": False, "error": str(e)}, status_code=500)

    # ------------------------------------------------------------------
    # Draft queue endpoints
    # ------------------------------------------------------------------

    @router.get("/initiative/drafts")
    async def get_pending_drafts():
        """Get all pending initiative draft actions."""
        try:
            from distr.core.initiative.draft_queue import DraftQueue
            queue = DraftQueue()
            queue.expire_old()
            import dataclasses
            entries = queue.get_all()
            return JSONResponse([dataclasses.asdict(e) for e in entries])
        except Exception as e:
            logger.error(f"Failed to load drafts: {e}", exc_info=True)
            return JSONResponse([])

    @router.post("/initiative/drafts/{draft_id}/approve")
    async def approve_draft(draft_id: str):
        """Approve a pending draft action (runs ``execute_payload`` when set)."""
        try:
            from distr.core.initiative.draft_execute import approve_draft_in_queue
            from distr.core.initiative.draft_queue import DraftQueue

            queue = DraftQueue()
            removed = approve_draft_in_queue(queue, draft_id)
            if removed:
                return JSONResponse({"success": True, "message": "Draft approved and removed"})
            still_there = queue.get_by_id(draft_id) is not None
            if still_there:
                return JSONResponse(
                    {
                        "success": False,
                        "error": "Approval failed — install step did not complete (draft left in queue).",
                    },
                    status_code=400,
                )
            return JSONResponse({"success": False, "error": "Draft not found"}, status_code=404)
        except Exception as e:
            logger.error(f"Failed to approve draft: {e}", exc_info=True)
            return JSONResponse({"success": False, "error": str(e)}, status_code=500)

    @router.post("/initiative/drafts/{draft_id}/reject")
    async def reject_draft(draft_id: str):
        """Reject a pending draft action."""
        try:
            from distr.core.initiative.draft_queue import DraftQueue
            queue = DraftQueue()
            removed = queue.remove(draft_id)
            if removed:
                return JSONResponse({"success": True, "message": "Draft rejected and removed"})
            return JSONResponse({"success": False, "error": "Draft not found"}, status_code=404)
        except Exception as e:
            logger.error(f"Failed to reject draft: {e}", exc_info=True)
            return JSONResponse({"success": False, "error": str(e)}, status_code=500)

    @router.get("/initiative/status")
    async def get_initiative_status():
        """Return current initiative service cycle status (for debugging)."""
        try:
            from PyQt6.QtWidgets import QApplication
            app = QApplication.instance()
            svc = getattr(app, "initiative_service", None)
            if svc is None:
                return JSONResponse({
                    "status": "idle",
                    "running": False,
                    "cycle_count": 0,
                    "last_error": None,
                    "consecutive_failures": 0,
                    "last_success_at": None,
                    "last_failure_at": None,
                })
            return JSONResponse(svc.get_status())
        except Exception as e:
            logger.error(f"Failed to get initiative status: {e}", exc_info=True)
            return JSONResponse({"status": "error", "running": False, "cycle_count": 0, "last_error": str(e)})
