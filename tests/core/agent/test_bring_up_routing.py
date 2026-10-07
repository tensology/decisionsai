from distr.core.agent.services.llm.fast_action_detector import FastActionDetector
from distr.core.agent.tool_intents import forced_tool_names_for_text
from distr.core.agent.tools.input.navigation import SmartOpenTool


def _detect(text: str):
    return FastActionDetector().detect(text)


def test_bring_up_ticket_board_list_goes_to_open_board():
    result = _detect(
        "Can you bring up the web UI interface, the development section, the ticket boards or whatever?"
    )
    assert result.tool_name == "create_ticket"
    assert result.tool_args.get("action") == "open_board"


def test_bring_up_development_goes_to_open_page():
    result = _detect("Bring up the development section")
    assert result.tool_name == "open_page"
    assert result.tool_args.get("page") == "development"


def test_bring_up_web_ui_goes_to_open_page():
    result = _detect("Can you bring up the web UI?")
    assert result.tool_name == "open_page"
    assert result.tool_args.get("page") == "development"


def test_bring_up_named_board_keeps_board_name():
    result = _detect("Bring up the Player One ticket board")
    assert result.tool_name == "create_ticket"
    assert result.tool_args.get("action") == "open_board"
    assert "player one" in result.tool_args.get("board_name", "").lower()


def test_bring_up_project_goes_to_open_project():
    result = _detect("Bring up the project")
    assert result.tool_name == "open_project"


def test_bring_up_brave_is_short_app_open():
    result = _detect("Please bring up Brave")
    assert result.tool_name == "smart_open"
    assert result.tool_args.get("target") == "Brave"


def test_switch_to_chrome_is_an_app_focus_command():
    result = _detect("Can you switch to Chrome?")

    assert result.tool_name == "smart_open"
    assert result.tool_args.get("target") == "Chrome"


def test_bring_up_google_is_site_open():
    result = _detect("Bring up Google")
    assert result.tool_name == "open_window"
    assert result.tool_args.get("app_name") == "google"


def test_reload_brave_focuses_named_browser_and_restores_previous_app():
    result = _detect("Can you reload Brave?")

    assert result.tool_name == "media_control"
    assert result.tool_args == {
        "action": "refresh",
        "target_app": "Brave",
        "restore_focus": True,
    }


def test_refresh_page_in_chrome_focuses_named_browser():
    result = _detect("Refresh the page in Chrome")

    assert result.tool_name == "media_control"
    assert result.tool_args["target_app"] == "Chrome"


def test_intents_force_page_and_board_for_spoken_list():
    text = "Can you bring up the web UI interface, the development section, the ticket boards or whatever?"
    forced = forced_tool_names_for_text(text)
    assert "create_ticket" in forced
    assert "open_page" in forced


def test_smart_open_does_not_open_a_random_file_when_app_launch_fails(monkeypatch):
    monkeypatch.setattr(
        SmartOpenTool,
        "_open_application",
        lambda self, app_name, text: "Error: Unable to find application named 'Brave'",
    )

    def boom(self, target):
        raise AssertionError("must not fuzzy-open a file for an app name")

    monkeypatch.setattr(SmartOpenTool, "_open_file", boom)
    result = SmartOpenTool()._run(target="Brave", text="open it up in brave")
    assert "Unable to find application" in result
