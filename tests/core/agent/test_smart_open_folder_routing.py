from distr.core.agent.tools.input.navigation import SmartOpenTool


def test_known_folder_detection_does_not_capture_an_editor_request_containing_folder_name(monkeypatch):
    monkeypatch.setattr("os.path.isdir", lambda path: True)

    result = SmartOpenTool()._open_known_folder("Open the project in Codex from my Documents folder")

    assert result is None


def test_structured_editor_request_does_not_open_target_folder(monkeypatch):
    monkeypatch.setattr(
        "distr.core.agent.tools.system.project_tools.OpenProjectTool._run",
        lambda self, text="", **kwargs: "Opened project in Codex",
    )

    for request in (
        "Open the project from my Documents folder",
        "Launch the project in Cursor from Documents",
        "Show the project in Codex from Documents",
    ):
        result = SmartOpenTool()._run(target="Documents", text=request)
        assert result == "Opened project in Codex"
