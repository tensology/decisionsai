"""P5: the project's own design decisions seed the library's base template."""

import pytest

from distr.core.planning import base_template as bt
from distr.core.planning import library, plan_store, project_theme as pt


def _tokens_css(tmp_path, body):
    css = tmp_path / "src" / "styles"
    css.mkdir(parents=True, exist_ok=True)
    (css / "tokens.css").write_text(body, encoding="utf-8")
    return tmp_path


def test_tokens_are_read_from_the_projects_css(tmp_path):
    _tokens_css(tmp_path, """
        :root {
          --accent: #2563eb;
          --text: #0f172a;
          --muted: #64748b;
          --border: #e2e8f0;
          --card: #ffffff;
          --background: #f8fafc;
          font-family: 'Quicksand', system-ui, sans-serif;
        }
    """)
    manifest = pt.derive_base_template(tmp_path)
    tokens = manifest["config"]["tokens"]
    assert tokens["color.accent"] == "#2563eb"
    assert tokens["color.ink"] == "#0f172a"
    assert tokens["color.line"] == "#e2e8f0"
    assert "Quicksand" in tokens["font.family"]


def test_a_tailwind_config_is_a_fallback(tmp_path):
    (tmp_path / "tailwind.config.js").write_text(
        "module.exports = { theme: { extend: { colors: { accent: '#ff0000', card: '#ffffff' } } } }"
    )
    tokens = pt.derive_base_template(tmp_path)["config"]["tokens"]
    assert tokens["color.accent"] == "#ff0000"


def test_css_wins_over_tailwind(tmp_path):
    _tokens_css(tmp_path, ":root { --accent: #111111; --text: #222222; }")
    (tmp_path / "tailwind.config.js").write_text("module.exports={theme:{extend:{colors:{accent:'#ff0000'}}}}")
    assert pt.derive_base_template(tmp_path)["config"]["tokens"]["color.accent"] == "#111111"


def test_non_colours_are_not_taken_as_colours(tmp_path):
    _tokens_css(tmp_path, ":root { --accent: calc(1px + 2px); --text: 0 0% 100%; }")
    assert pt.derive_base_template(tmp_path) is None


def test_too_little_state_is_no_theme(tmp_path):
    """A wrong theme is worse than a generic one."""
    _tokens_css(tmp_path, ":root { --accent: #2563eb; }")
    assert pt.derive_base_template(tmp_path) is None


def test_no_project_is_not_an_error(tmp_path):
    assert pt.derive_base_template(None) is None
    assert pt.derive_base_template("") is None
    assert pt.derive_base_template(tmp_path / "nope") is None


def test_dependency_folders_are_ignored(tmp_path):
    junk = tmp_path / "node_modules" / "pkg"
    junk.mkdir(parents=True)
    (junk / "x.css").write_text(":root{--accent:#fff;--text:#000}")
    assert pt.derive_base_template(tmp_path) is None


# ------------------------------------------------------------------ appliying it

def test_the_derived_theme_drives_the_render(tmp_path):
    _tokens_css(tmp_path, ":root { --accent: #2563eb; --text: #0f172a; --muted: #64748b; }")
    manifest = pt.derive_base_template(tmp_path)
    base = bt.parse_base(manifest["config"], name=manifest["name"])
    css = bt.to_css(base)
    assert "#2563eb" in css
    # and it is the accent the components actually use
    assert "--wf-color-accent: #2563eb;" in css
    assert ".node.link a { color: var(--wf-color-accent)" in css


def test_saving_writes_it_into_the_projects_library(tmp_path):
    _tokens_css(tmp_path, ":root { --accent: #2563eb; --text: #0f172a; }")
    planning = tmp_path / "planning"
    result = pt.save_as_project_entry(tmp_path, planning)
    assert result["saved"] is True
    found = library.entries(user_root=planning / "library", builtin=tmp_path / "none")
    assert [e.name for e in found] == [result["name"]]
    assert found[0].kind == "base-template"


def test_saving_without_a_theme_is_not_an_error(tmp_path):
    assert pt.save_as_project_entry(tmp_path, tmp_path / "planning")["saved"] is False
