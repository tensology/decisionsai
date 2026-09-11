from distr.core.agent.tools.system.project_tools import OpenProjectTool


def _project(backend=""):
    return {
        "id": 7,
        "name": "Decisions",
        "folder_location": "/tmp/decisions",
        "coding_backend": backend,
    }


def test_open_project_execution_backend_does_not_override_global_editor(monkeypatch):
    monkeypatch.setattr(
        "distr.core.agent.services.rag.project.get_active_project",
        lambda: _project("cursor"),
    )
    monkeypatch.setattr("distr.core.settings.load_settings_from_db", lambda: {"default_project_editor": "codex"})
    monkeypatch.setattr("shutil.which", lambda command: f"/usr/local/bin/{command}")
    calls = []
    monkeypatch.setattr("subprocess.run", lambda command, **kwargs: calls.append(command))

    result = OpenProjectTool()._run("Open the project")

    assert calls == [["codex", "app", "/tmp/decisions"]]
    assert "Codex" in result


def test_open_project_explicit_codex_override_wins(monkeypatch):
    monkeypatch.setattr(
        "distr.core.agent.services.rag.project.get_active_project",
        lambda: _project("cursor"),
    )
    monkeypatch.setattr("distr.core.settings.load_settings_from_db", lambda: {"default_project_editor": "cursor"})
    monkeypatch.setattr("shutil.which", lambda command: f"/usr/local/bin/{command}")
    calls = []
    monkeypatch.setattr("subprocess.run", lambda command, **kwargs: calls.append(command))

    result = OpenProjectTool()._run("Open this project in Codex")

    assert calls == [["codex", "app", "/tmp/decisions"]]
    assert "Codex" in result


def test_open_project_uses_global_default_without_project_override(monkeypatch):
    monkeypatch.setattr(
        "distr.core.agent.services.rag.project.get_active_project",
        lambda: _project("pi"),
    )
    monkeypatch.setattr("distr.core.settings.load_settings_from_db", lambda: {"default_project_editor": "codex"})
    monkeypatch.setattr("shutil.which", lambda command: f"/usr/local/bin/{command}")
    calls = []
    monkeypatch.setattr("subprocess.run", lambda command, **kwargs: calls.append(command))

    OpenProjectTool()._run("Open the project")

    assert calls == [["codex", "app", "/tmp/decisions"]]
