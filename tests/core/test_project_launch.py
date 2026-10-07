from distr.core.agent.services.llm.fast_action_detector import (
    ActionType,
    FastActionDetector,
)
from distr.core.agent.tool_intents import forced_tool_names_for_text
from distr.core.agent.tools.system.project_tools import _parse_project_lifecycle_request
from distr.core.project_startup_terminals import (
    ProjectTerminalActionResult,
    _get_live_project_runtime_snapshot,
    _rank_runtime_urls,
    launch_project_in_browser,
)


def test_project_launch_flow_prefers_frontend_and_routes_natural_phrases(monkeypatch):
    snapshot = {
        "sessions": [
            {
                "command": "python manage.py runserver",
                "urls": [{"url": "http://127.0.0.1:8000", "port": 8000}],
            },
            {
                "command": "cd frontend && npm run dev",
                "urls": [{"url": "http://127.0.0.1:5173", "port": 5173}],
            },
        ],
        "urls": [],
    }
    assert _rank_runtime_urls(snapshot)[0] == "http://127.0.0.1:5173"

    for text, expected_name, expected_launch in (
        ("start project Merrypak", "Merrypak", False),
        ("launch project Merrypak", "Merrypak", True),
        ("start and launch project Merrypak", "Merrypak", True),
        ("launch and start project Merrypak", "Merrypak", True),
        ("start project Merrypak and launch it", "Merrypak", True),
        ("start project Merrypak, then launch it", "Merrypak", True),
        ("launch the project", "", True),
    ):
        assert _parse_project_lifecycle_request(text) == (expected_name, expected_launch)
        detected = FastActionDetector().detect(text)
        assert detected.action_type is ActionType.PROJECT_LIFECYCLE
        assert detected.tool_name == "start_project"
        assert detected.tool_args == {"text": text}
        assert "start_project" in forced_tool_names_for_text(text)

    opened: list[str] = []
    monkeypatch.setattr(
        "distr.core.project_startup_terminals._load_project",
        lambda project_id: {"id": project_id, "name": "Merrypak"},
    )
    monkeypatch.setattr(
        "distr.core.project_startup_terminals.start_project_startup_terminals",
        lambda project_id, announce=False: ProjectTerminalActionResult(
            True, project_id, "Merrypak", "started", started=2
        ),
    )
    monkeypatch.setattr(
        "distr.core.terminal.get_project_runtime_snapshot",
        lambda project_id: snapshot,
    )

    result = launch_project_in_browser(
        7,
        announce=False,
        timeout_sec=0,
        discovery_delay_sec=0,
        opener=lambda url: opened.append(url) or True,
        snapshot_getter=lambda project_id: snapshot,
    )

    assert result.success is True
    assert result.opened is True
    assert result.url == "http://127.0.0.1:5173"
    assert opened == ["http://127.0.0.1:5173"]


def test_project_launch_reports_missing_url_as_failure(monkeypatch):
    monkeypatch.setattr(
        "distr.core.project_startup_terminals._load_project",
        lambda project_id: {"id": project_id, "name": "Tensology"},
    )
    monkeypatch.setattr(
        "distr.core.project_startup_terminals.start_project_startup_terminals",
        lambda project_id, announce=False: ProjectTerminalActionResult(
            True, project_id, "Tensology", "started", started=4
        ),
    )

    result = launch_project_in_browser(
        3,
        announce=False,
        timeout_sec=0,
        snapshot_getter=lambda project_id: {"sessions": [], "urls": []},
    )

    assert result.success is False
    assert result.action == "no_url"
    assert result.message == "Project Tensology is running, but no local URL appeared in its terminals."


def test_live_runtime_snapshot_reads_terminal_owner_process(monkeypatch):
    payload = {
        "sessions": [
            {
                "command": "npm run dev",
                "urls": [{"url": "http://127.0.0.1:5173", "port": 5173}],
            }
        ]
    }

    class _Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self):
            import json

            return json.dumps(payload).encode("utf-8")

    monkeypatch.setattr(
        "distr.core.web_runtime.resolve_local_web_base_url",
        lambda: "http://127.0.0.1:8765",
    )
    monkeypatch.setattr("urllib.request.urlopen", lambda request, timeout: _Response())

    snapshot = _get_live_project_runtime_snapshot(3)

    assert _rank_runtime_urls(snapshot) == ["http://127.0.0.1:5173"]
