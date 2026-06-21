"""
Shortcut key routes — /shortcuts
"""

from fastapi.responses import JSONResponse
from fastapi import HTTPException

from ._shared import ShortcutSettings, route_handler
from distr.core.hotkeys import (
    DEFAULTS as HOTKEY_DEFAULTS,
    modifier_options,
    ptt_modifier_options,
    key_options,
)


def register_routes(router, templates):
    @router.get("/shortcuts")
    @route_handler("load shortcut settings")
    async def get_shortcut_settings():
        from distr.core.settings import load_settings_from_db

        settings = load_settings_from_db()
        return JSONResponse({
            "global_ptt_hotkey_enabled": settings.get("global_ptt_hotkey_enabled", True),
            "global_ptt_hotkey_combo": settings.get("global_ptt_hotkey_combo", HOTKEY_DEFAULTS["global_ptt_hotkey_combo"]),
            "oracle_size_hotkey_decrease_modifier": settings.get("oracle_size_hotkey_decrease_modifier", HOTKEY_DEFAULTS["oracle_size_hotkey_decrease_modifier"]),
            "oracle_size_hotkey_decrease_key": settings.get("oracle_size_hotkey_decrease_key", HOTKEY_DEFAULTS["oracle_size_hotkey_decrease_key"]),
            "oracle_size_hotkey_increase_modifier": settings.get("oracle_size_hotkey_increase_modifier", HOTKEY_DEFAULTS["oracle_size_hotkey_increase_modifier"]),
            "oracle_size_hotkey_increase_key": settings.get("oracle_size_hotkey_increase_key", HOTKEY_DEFAULTS["oracle_size_hotkey_increase_key"]),
            "recording_hotkey_enabled": settings.get("recording_hotkey_enabled", True),
            "recording_hotkey_modifier": settings.get("recording_hotkey_modifier", HOTKEY_DEFAULTS["recording_hotkey_modifier"]),
            "recording_hotkey_key": settings.get("recording_hotkey_key", HOTKEY_DEFAULTS["recording_hotkey_key"]),
            "skin_nav_hotkey_previous_modifier": settings.get("skin_nav_hotkey_previous_modifier", HOTKEY_DEFAULTS["skin_nav_hotkey_previous_modifier"]),
            "skin_nav_hotkey_previous_key": settings.get("skin_nav_hotkey_previous_key", HOTKEY_DEFAULTS["skin_nav_hotkey_previous_key"]),
            "skin_nav_hotkey_next_modifier": settings.get("skin_nav_hotkey_next_modifier", HOTKEY_DEFAULTS["skin_nav_hotkey_next_modifier"]),
            "skin_nav_hotkey_next_key": settings.get("skin_nav_hotkey_next_key", HOTKEY_DEFAULTS["skin_nav_hotkey_next_key"]),
            "skin_select_hotkey_modifier": settings.get("skin_select_hotkey_modifier", HOTKEY_DEFAULTS["skin_select_hotkey_modifier"]),
            "web_hotkey_chat_modifier": settings.get("web_hotkey_chat_modifier", HOTKEY_DEFAULTS["web_hotkey_chat_modifier"]),
            "web_hotkey_chat_key": settings.get("web_hotkey_chat_key", HOTKEY_DEFAULTS["web_hotkey_chat_key"]),
            "web_hotkey_projects_modifier": settings.get("web_hotkey_projects_modifier", HOTKEY_DEFAULTS["web_hotkey_projects_modifier"]),
            "web_hotkey_projects_key": settings.get("web_hotkey_projects_key", HOTKEY_DEFAULTS["web_hotkey_projects_key"]),
            "web_hotkey_actions_modifier": settings.get("web_hotkey_actions_modifier", HOTKEY_DEFAULTS["web_hotkey_actions_modifier"]),
            "web_hotkey_actions_key": settings.get("web_hotkey_actions_key", HOTKEY_DEFAULTS["web_hotkey_actions_key"]),
            "web_hotkey_snippets_modifier": settings.get("web_hotkey_snippets_modifier", HOTKEY_DEFAULTS["web_hotkey_snippets_modifier"]),
            "web_hotkey_snippets_key": settings.get("web_hotkey_snippets_key", HOTKEY_DEFAULTS["web_hotkey_snippets_key"]),
            "web_hotkey_workflows_modifier": settings.get("web_hotkey_workflows_modifier", HOTKEY_DEFAULTS["web_hotkey_workflows_modifier"]),
            "web_hotkey_workflows_key": settings.get("web_hotkey_workflows_key", HOTKEY_DEFAULTS["web_hotkey_workflows_key"]),
            "web_hotkey_ticket_board_modifier": settings.get("web_hotkey_ticket_board_modifier", HOTKEY_DEFAULTS["web_hotkey_ticket_board_modifier"]),
            "web_hotkey_ticket_board_key": settings.get("web_hotkey_ticket_board_key", HOTKEY_DEFAULTS["web_hotkey_ticket_board_key"]),
            "web_hotkey_irc_modifier": settings.get("web_hotkey_irc_modifier", HOTKEY_DEFAULTS["web_hotkey_irc_modifier"]),
            "web_hotkey_irc_key": settings.get("web_hotkey_irc_key", HOTKEY_DEFAULTS["web_hotkey_irc_key"]),
            "web_hotkey_preferences_modifier": settings.get("web_hotkey_preferences_modifier", HOTKEY_DEFAULTS["web_hotkey_preferences_modifier"]),
            "web_hotkey_preferences_key": settings.get("web_hotkey_preferences_key", HOTKEY_DEFAULTS["web_hotkey_preferences_key"]),
            "dictation_hotkey_enabled": settings.get("dictation_hotkey_enabled", HOTKEY_DEFAULTS["dictation_hotkey_enabled"]),
            "dictation_hotkey_modifier": settings.get("dictation_hotkey_modifier", HOTKEY_DEFAULTS["dictation_hotkey_modifier"]),
            "dictation_hotkey_key": settings.get("dictation_hotkey_key", HOTKEY_DEFAULTS["dictation_hotkey_key"]),
            "ticket_dictation_hotkey_enabled": settings.get("ticket_dictation_hotkey_enabled", HOTKEY_DEFAULTS["ticket_dictation_hotkey_enabled"]),
            "ticket_dictation_hotkey_modifier": settings.get("ticket_dictation_hotkey_modifier", HOTKEY_DEFAULTS["ticket_dictation_hotkey_modifier"]),
            "ticket_dictation_hotkey_key": settings.get("ticket_dictation_hotkey_key", HOTKEY_DEFAULTS["ticket_dictation_hotkey_key"]),
            "dictation_ticket_use_llm": settings.get("dictation_ticket_use_llm", True),
            "dictation_ticket_model": settings.get("dictation_ticket_model", "qwen2.5:0.5b"),
            "dictation_ticket_timeout": settings.get("dictation_ticket_timeout", "1.2"),
            "dictation_ticket_prompt": settings.get("dictation_ticket_prompt", ""),
            "shortcut_options": {
                "ptt_modifiers": ptt_modifier_options(),
                "modifiers": modifier_options(),
                "keys": key_options(),
            },
        })

    @router.post("/shortcuts")
    @route_handler("save shortcut settings")
    async def save_shortcut_settings_route(settings_data: ShortcutSettings):
        from distr.core.services.settings_service import save_shortcut_settings

        try:
            saved_settings = save_shortcut_settings(settings_data)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        return JSONResponse({
            "success": True,
            "message": "Shortcut settings saved",
            "settings": saved_settings,
        })
