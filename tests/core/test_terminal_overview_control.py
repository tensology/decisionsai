import json

from distr.core.agent.tools.integrations.terminal_overview import TerminalOverviewTool
from distr.core.db import get_session
from distr.core.db.projects import Project


def test_terminal_control_lists_projects_and_saves_explicit_command_lines(monkeypatch):
    with get_session() as db:
        project = Project(
            name="Terminal project",
            folder_location="/tmp",
            startup_instructions="npm run dev",
        )
        db.add(project)
        db.commit()
        project_id = int(project.id)

    monkeypatch.setattr(
        "distr.core.terminal.get_startup_sessions_for_project",
        lambda project_id, purpose="startup": [],
    )
    tool = TerminalOverviewTool()

    listed = json.loads(tool._run(action="list"))
    saved = json.loads(
        tool._run(
            action="save",
            project_id=project_id,
            startup_instructions="npm run dev\npython worker.py",
        )
    )

    row = next(item for item in listed["projects"] if item["project_id"] == project_id)
    assert row["commands"] == ["npm run dev"]
    assert row["running_count"] == 0
    assert saved["commands"] == ["npm run dev", "python worker.py"]
    with get_session() as db:
        assert db.get(Project, project_id).startup_instructions == "npm run dev\npython worker.py"


def test_terminal_control_start_and_stop_use_explicit_project(monkeypatch):
    from distr.core.project_startup_terminals import ProjectTerminalActionResult

    calls = []
    with get_session() as db:
        project = Project(name="Managed terminal", folder_location="/tmp")
        db.add(project)
        db.commit()
        project_id = int(project.id)

    monkeypatch.setattr(
        "distr.core.project_startup_terminals.start_project_startup_terminals",
        lambda target, **kwargs: calls.append(("start", target, kwargs)) or ProjectTerminalActionResult(
            True, target, "Managed terminal", "started", started=2
        ),
    )
    monkeypatch.setattr(
        "distr.core.project_startup_terminals.stop_project_startup_terminals",
        lambda target, **kwargs: calls.append(("stop", target, kwargs)) or ProjectTerminalActionResult(
            True, target, "Managed terminal", "stopped", stopped=2
        ),
    )

    tool = TerminalOverviewTool()
    started = json.loads(tool._run(action="start", project_id=project_id))
    stopped = json.loads(tool._run(action="stop", project_id=project_id))

    assert started["started"] == 2
    assert stopped["stopped"] == 2
    assert calls[0][0:2] == ("start", project_id)
    assert calls[1][0:2] == ("stop", project_id)
