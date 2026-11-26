"""Which stack holds a project's pages - asked before anything is stencilled."""

import json

import pytest

from distr.core.planning import stack_detect as sd


def _django_api(tmp_path):
    (tmp_path / "manage.py").write_text("")
    app = tmp_path / "app"
    app.mkdir()
    (app / "views.py").write_text(
        "from django.http import JsonResponse\n"
        "def locale(request):\n    return JsonResponse({'locale': 'en'})\n"
    )


def _django_pages(tmp_path):
    (tmp_path / "manage.py").write_text("")
    app = tmp_path / "app"
    app.mkdir()
    (app / "views.py").write_text(
        "from django.shortcuts import render\n"
        "def home(request):\n    return render(request, 'home.html')\n"
    )
    t = tmp_path / "app" / "templates"
    t.mkdir()
    (t / "home.html").write_text("<html><body><form><input name='q'></form></body></html>")


def _react(tmp_path, dep="react"):
    (tmp_path / "package.json").write_text(json.dumps({"dependencies": {dep: "^1"}}))
    src = tmp_path / "src"
    src.mkdir(exist_ok=True)
    (src / "App.tsx").write_text("export default function App(){ return <div/> }")


def _pyqt(tmp_path):
    (tmp_path / "main.py").write_text(
        "from PyQt6.QtWidgets import QMainWindow\nclass W(QMainWindow):\n    pass\n"
    )


# ------------------------------------------------------------------ detection

def test_a_django_api_is_not_a_page_source(tmp_path):
    _django_api(tmp_path)
    stacks = sd.detect_stacks(tmp_path)
    django = next(s for s in stacks if s.name == "django")
    assert django.data_only == 1
    assert django.pages == 0
    assert django.serves_pages is False
    assert "API" in django.notes[0]


def test_a_django_project_with_templates_serves_pages(tmp_path):
    _django_pages(tmp_path)
    django = next(s for s in sd.detect_stacks(tmp_path) if s.name == "django")
    assert django.pages >= 1
    assert django.serves_pages is True


def test_react_is_detected_with_its_framework(tmp_path):
    _react(tmp_path)
    assert next(s.name for s in sd.detect_stacks(tmp_path) if s.serves_pages) == "react"
    _react(tmp_path, dep="vue")
    assert next(s.name for s in sd.detect_stacks(tmp_path) if s.serves_pages) == "vue"


def test_pyqt_is_detected_as_a_desktop_app(tmp_path):
    _pyqt(tmp_path)
    pyqt = next(s for s in sd.detect_stacks(tmp_path) if s.name == "pyqt")
    assert pyqt.serves_pages is True
    assert "Desktop app" in pyqt.notes[0]


def test_rust_templates_are_counted(tmp_path):
    (tmp_path / "Cargo.toml").write_text("[package]\nname='x'")
    t = tmp_path / "templates"
    t.mkdir()
    (t / "index.html").write_text("<html></html>")
    rust = next(s for s in sd.detect_stacks(tmp_path) if s.name == "rust")
    assert rust.pages == 1


def test_plain_html_is_a_stack_when_there_is_no_django(tmp_path):
    t = tmp_path / "templates"
    t.mkdir()
    (t / "a.html").write_text("<html></html>")
    assert any(s.name == "html" for s in sd.detect_stacks(tmp_path))


def test_django_templates_are_not_double_counted_as_plain_html(tmp_path):
    _django_pages(tmp_path)
    assert not any(s.name == "html" for s in sd.detect_stacks(tmp_path))


def test_dependency_folders_are_ignored(tmp_path):
    junk = tmp_path / "node_modules" / "pkg"
    junk.mkdir(parents=True)
    (junk / "Thing.tsx").write_text("export default () => null")
    assert sd.detect_stacks(tmp_path) == []


# ------------------------------------------------------------------ the verdict

def test_the_verdict_names_the_page_stack(tmp_path):
    _django_api(tmp_path)
    _react(tmp_path)
    verdict = sd.summary(sd.detect_stacks(tmp_path))["verdict"]
    assert "react" in verdict
    assert "django" in verdict


def test_the_verdict_says_so_when_nothing_serves_pages(tmp_path):
    _django_api(tmp_path)
    verdict = sd.summary(sd.detect_stacks(tmp_path))["verdict"]
    assert "data-only" in verdict


def test_no_stack_is_not_an_error(tmp_path):
    assert sd.detect_stacks(tmp_path) == []
    assert "generic scan" in sd.summary([])["verdict"]
    assert sd.detect_stacks(None) == []
    assert sd.detect_stacks("") == []


def test_page_stack_picks_the_page_bearing_one(tmp_path):
    _django_api(tmp_path)
    _react(tmp_path)
    assert sd.page_stack(tmp_path).name == "react"


def test_a_broken_project_does_not_raise(tmp_path, monkeypatch):
    """Detection is a hint; a failure must not take the scan down with it."""
    _react(tmp_path)
    import pathlib as _p

    monkeypatch.setattr(_p.Path, "rglob", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    assert sd.detect_stacks(tmp_path) == []
