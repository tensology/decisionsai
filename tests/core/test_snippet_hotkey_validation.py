from types import SimpleNamespace

import pytest


def test_rejects_snippet_hotkey_that_overlaps_skin_navigation():
    from distr.gui.web.routes.settings import snippets as snippets_routes

    settings = {
        "skin_nav_hotkey_previous_modifier": "control_command",
        "skin_nav_hotkey_previous_key": "left_arrow",
        "skin_nav_hotkey_next_modifier": "control_command",
        "skin_nav_hotkey_next_key": "right_arrow",
        "skin_select_hotkey_modifier": "option_command",
        "web_hotkey_chat_modifier": "control_shift",
        "web_hotkey_chat_key": "c",
        "web_hotkey_projects_modifier": "control_shift",
        "web_hotkey_projects_key": "j",
        "web_hotkey_actions_modifier": "control_shift",
        "web_hotkey_actions_key": "a",
        "web_hotkey_snippets_modifier": "control_shift",
        "web_hotkey_snippets_key": "n",
        "web_hotkey_workflows_modifier": "control_shift",
        "web_hotkey_workflows_key": "w",
        "web_hotkey_automations_modifier": "control_shift",
        "web_hotkey_automations_key": "o",
        "web_hotkey_ticket_board_modifier": "control_shift",
        "web_hotkey_ticket_board_key": "t",
        "web_hotkey_irc_modifier": "control_shift",
        "web_hotkey_irc_key": "i",
        "web_hotkey_preferences_modifier": "control_shift",
        "web_hotkey_preferences_key": "p",
    }

    with pytest.raises(ValueError, match="Previous skin"):
        snippets_routes._validate_snippet_remote_hotkey(
            "ctrl+cmd+left",
            settings=settings,
            existing_snippets=[],
        )


def test_rejects_duplicate_snippet_hotkey_from_another_snippet():
    from distr.gui.web.routes.settings import snippets as snippets_routes

    with pytest.raises(ValueError, match="Snippet 9"):
        snippets_routes._validate_snippet_remote_hotkey(
            "ctrl+shift+9",
            settings={},
            existing_snippets=[SimpleNamespace(id=9, remote_hotkey="ctrl+shift+9")],
        )


def test_allows_existing_snippet_to_keep_its_current_hotkey():
    from distr.gui.web.routes.settings import snippets as snippets_routes

    normalized = snippets_routes._validate_snippet_remote_hotkey(
        "command+control+left",
        settings={
            "skin_nav_hotkey_previous_modifier": "control_command",
            "skin_nav_hotkey_previous_key": "right_arrow",
        },
        existing_snippets=[SimpleNamespace(id=4, remote_hotkey="ctrl+cmd+left")],
        current_snippet_id=4,
    )

    assert normalized == "ctrl+cmd+left"
