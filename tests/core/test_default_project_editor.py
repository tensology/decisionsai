from distr.gui.web.routes.settings import projects


def test_default_project_editor_accepts_only_codex_or_cursor():
    assert projects._normalize_default_project_editor("codex") == "codex"
    assert projects._normalize_default_project_editor("cursor") == "cursor"
    assert projects._normalize_default_project_editor("pi") == "codex"
    assert projects._normalize_default_project_editor("") == "codex"


def test_project_create_backend_preserves_execution_backend_independently_of_editor(monkeypatch):
    monkeypatch.setattr(
        "distr.core.settings.load_settings_from_db",
        lambda: {"default_project_editor": "cursor"},
    )

    assert projects._project_create_backend({}) == "pi"
    assert projects._project_create_backend({"coding_backend": "codex"}) == "codex"
    assert projects._project_create_backend({"coding_backend": "pi"}) == "pi"
