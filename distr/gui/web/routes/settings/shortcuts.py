"""
Shortcut key routes — /shortcuts
"""

from fastapi.responses import JSONResponse

from ._shared import ShortcutSettings, route_handler


def register_routes(router, templates):
    @router.get("/shortcuts")
    @route_handler("load shortcut settings")
    async def get_shortcut_settings():
        from distr.core.settings import load_settings_from_db

        settings = load_settings_from_db()
        return JSONResponse({
            "global_ptt_hotkey_enabled": settings.get("global_ptt_hotkey_enabled", True),
            "global_ptt_hotkey_primary": settings.get("global_ptt_hotkey_primary", "option"),
            "global_ptt_hotkey_secondary": settings.get("global_ptt_hotkey_secondary", "command"),
            "oracle_size_hotkey_decrease_modifier": settings.get("oracle_size_hotkey_decrease_modifier", "option_command"),
            "oracle_size_hotkey_decrease_key": settings.get("oracle_size_hotkey_decrease_key", "left_bracket"),
            "oracle_size_hotkey_increase_modifier": settings.get("oracle_size_hotkey_increase_modifier", "option_command"),
            "oracle_size_hotkey_increase_key": settings.get("oracle_size_hotkey_increase_key", "right_bracket"),
        })

    @router.post("/shortcuts")
    @route_handler("save shortcut settings")
    async def save_shortcut_settings_route(settings_data: ShortcutSettings):
        from distr.core.services.settings_service import save_shortcut_settings

        save_shortcut_settings(settings_data)
        return JSONResponse({"success": True, "message": "Shortcut settings saved"})
