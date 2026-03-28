"""
Initiative routes — /initiative
"""
import logging
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)

INITIATIVE_FIELDS = [
    "initiative_level",
    "initiative_allow_telegram",
    "initiative_allow_routine_tasks",
    "initiative_ask_external_comms",
    "initiative_ask_file_changes",
    "initiative_ask_sensitive",
]

DEFAULTS = {
    "initiative_level": "assist",
    "initiative_allow_telegram": False,
    "initiative_allow_routine_tasks": False,
    "initiative_ask_external_comms": True,
    "initiative_ask_file_changes": True,
    "initiative_ask_sensitive": True,
}


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
    async def save_initiative_settings(payload: dict):
        """Save initiative/proactivity settings."""
        try:
            from distr.core.settings import save_settings_to_db
            data = {}
            for key in INITIATIVE_FIELDS:
                if key in payload:
                    data[key] = payload[key]
            save_settings_to_db(data)
            return JSONResponse({"success": True})
        except Exception as e:
            logger.error(f"Failed to save initiative settings: {e}", exc_info=True)
            return JSONResponse({"success": False, "error": str(e)}, status_code=500)
