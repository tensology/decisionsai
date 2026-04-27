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
            "recording_hotkey_enabled": settings.get("recording_hotkey_enabled", True),
            "recording_hotkey_modifier": settings.get("recording_hotkey_modifier", "option_command"),
            "recording_hotkey_key": settings.get("recording_hotkey_key", "s"),
            "skin_nav_hotkey_previous_modifier": settings.get("skin_nav_hotkey_previous_modifier", "option_command"),
            "skin_nav_hotkey_previous_key": settings.get("skin_nav_hotkey_previous_key", "left_arrow"),
            "skin_nav_hotkey_next_modifier": settings.get("skin_nav_hotkey_next_modifier", "option_command"),
            "skin_nav_hotkey_next_key": settings.get("skin_nav_hotkey_next_key", "right_arrow"),
            "skin_select_hotkey_modifier": settings.get("skin_select_hotkey_modifier", "option_command"),
            "web_hotkey_chat_modifier": settings.get("web_hotkey_chat_modifier", "option_command"),
            "web_hotkey_chat_key": settings.get("web_hotkey_chat_key", "c"),
            "web_hotkey_projects_modifier": settings.get("web_hotkey_projects_modifier", "option_command"),
            "web_hotkey_projects_key": settings.get("web_hotkey_projects_key", "j"),
            "web_hotkey_actions_modifier": settings.get("web_hotkey_actions_modifier", "option_command"),
            "web_hotkey_actions_key": settings.get("web_hotkey_actions_key", "a"),
            "web_hotkey_snippets_modifier": settings.get("web_hotkey_snippets_modifier", "option_command"),
            "web_hotkey_snippets_key": settings.get("web_hotkey_snippets_key", "n"),
            "web_hotkey_workflows_modifier": settings.get("web_hotkey_workflows_modifier", "option_command"),
            "web_hotkey_workflows_key": settings.get("web_hotkey_workflows_key", "w"),
            "web_hotkey_preferences_modifier": settings.get("web_hotkey_preferences_modifier", "option_command"),
            "web_hotkey_preferences_key": settings.get("web_hotkey_preferences_key", "grave"),
        })

    @router.post("/shortcuts")
    @route_handler("save shortcut settings")
    async def save_shortcut_settings_route(settings_data: ShortcutSettings):
        from distr.core.services.settings_service import save_shortcut_settings

        save_shortcut_settings(settings_data)
        return JSONResponse({"success": True, "message": "Shortcut settings saved"})
