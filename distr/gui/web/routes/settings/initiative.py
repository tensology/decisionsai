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
    "initiative_telegram_notify_suggestions",
    "initiative_recommendations",
    "initiative_own_channel",
    "initiative_owner_whatsapp",
]

VALID_LEVELS = ("observe", "operate", "own")
# Own outcomes update channel. "" = Telegram when it's on, otherwise log only.
VALID_OWN_CHANNELS = ("", "telegram", "whatsapp", "voice", "log")

DEFAULTS = {
    "initiative_level": "operate",
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
    "initiative_telegram_notify_suggestions": False,
    "initiative_recommendations": False,
    "initiative_own_channel": "",
    "initiative_owner_whatsapp": "",
}


class _InitiativePayload(BaseModel):
    initiative_level: str = "operate"
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
    initiative_telegram_notify_suggestions: bool = False
    initiative_recommendations: bool = False
    initiative_own_channel: str = ""
    initiative_owner_whatsapp: str = ""


class _AssistantChannelAnswer(BaseModel):
    ask_id: str
    channel: str


def register_routes(router, templates):

    @router.get("/initiative")
    async def get_initiative_settings():
        """Get initiative/proactivity settings."""
        try:
            from distr.core.settings import load_settings_from_db
            settings = load_settings_from_db()
            from distr.core.initiative.policy import migrate_initiative_level

            result = {}
            for key in INITIATIVE_FIELDS:
                val = settings.get(key)
                if val is None:
                    result[key] = DEFAULTS.get(key)
                elif isinstance(val, str) and val.lower() in ("true", "false"):
                    result[key] = val.lower() == "true"
                else:
                    result[key] = val
            result["initiative_level"] = migrate_initiative_level(str(result.get("initiative_level") or "operate"))
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
            from distr.core.initiative.policy import migrate_initiative_level

            level = migrate_initiative_level(str(data.get("initiative_level") or "").strip().lower())
            if level not in VALID_LEVELS:
                return JSONResponse(
                    {"success": False, "error": f"Unknown initiative level: {data.get('initiative_level')!r}"},
                    status_code=400,
                )
            data["initiative_level"] = level
            channel = str(data.get("initiative_own_channel") or "").strip().lower()
            if channel not in VALID_OWN_CHANNELS:
                return JSONResponse(
                    {"success": False, "error": f"Unknown update channel: {data.get('initiative_own_channel')!r}"},
                    status_code=400,
                )
            data["initiative_own_channel"] = channel
            owner = str(data.get("initiative_owner_whatsapp") or "").strip()
            digits = "".join(ch for ch in owner if ch.isdigit())
            if owner and len(digits) < 8:
                return JSONResponse(
                    {"success": False, "error": "That WhatsApp number looks too short. Include the country code."},
                    status_code=400,
                )
            data["initiative_owner_whatsapp"] = ("+" + digits) if digits else ""
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
            retained = queue.get_by_id(draft_id)
            if retained is not None:
                return JSONResponse(
                    {
                        "success": False,
                        "error": retained.reason or "Approval did not complete. The draft is retained for review.",
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

    @router.post("/initiative/drafts/reject-all")
    async def reject_all_drafts():
        """Reject and remove every pending draft action."""
        try:
            from distr.core.initiative.draft_queue import DraftQueue
            queue = DraftQueue()
            queue.expire_old()
            removed = queue.clear()
            return JSONResponse({
                "success": True,
                "message": f"Rejected {removed} pending draft(s)",
                "removed": removed,
            })
        except Exception as e:
            logger.error(f"Failed to reject all drafts: {e}", exc_info=True)
            return JSONResponse({"success": False, "error": str(e)}, status_code=500)

    # ------------------------------------------------------------------
    # "Be my assistant" in-app suggestions (setting key: initiative_recommendations)
    # ------------------------------------------------------------------

    @router.get("/initiative/assistant/nudges")
    async def get_assistant_nudges():
        """Open gentle suggestions from the assistant (newest first)."""
        try:
            from distr.core.initiative.outcome_flow import assistant_nudges
            return JSONResponse({"nudges": assistant_nudges()})
        except Exception as e:
            logger.error(f"Failed to load assistant suggestions: {e}", exc_info=True)
            return JSONResponse({"nudges": []})

    @router.post("/initiative/assistant/nudges/{nudge_id}/dismiss")
    async def dismiss_assistant_nudge(nudge_id: str):
        """Let a suggestion go."""
        try:
            from distr.core.initiative.outcome_flow import dismiss_assistant_nudge as _dismiss
            if _dismiss(nudge_id):
                return JSONResponse({"success": True})
            return JSONResponse({"success": False, "error": "Suggestion not found"}, status_code=404)
        except Exception as e:
            logger.error(f"Failed to dismiss assistant suggestion: {e}", exc_info=True)
            return JSONResponse({"success": False, "error": str(e)}, status_code=500)

    @router.post("/initiative/assistant/channel")
    async def answer_assistant_channel(payload: _AssistantChannelAnswer):
        """Answer "Where do you want suggestions like this?" from the in-app panel."""
        try:
            from distr.core.initiative.channel_select import answer
            res = answer(payload.ask_id.strip() or None, payload.channel.strip())
            if res.get("ok"):
                return JSONResponse({"success": True, "channel": res.get("channel"), "message": res.get("text")})
            return JSONResponse({"success": False, "error": res.get("text")}, status_code=409)
        except Exception as e:
            logger.error(f"Failed to save where suggestions go: {e}", exc_info=True)
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
