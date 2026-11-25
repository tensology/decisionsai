"""Phase 0: per-screen desktop awareness (true perspective, not just frontmost)."""

import pytest

from distr.core import desktop_awareness as da


def test_display_for_point_picks_the_containing_display():
    displays = [
        (1, 0.0, 0.0, 1920.0, 1080.0),
        (2, 1920.0, 0.0, 2560.0, 1440.0),
        (3, 4480.0, 0.0, 1920.0, 1080.0),
    ]
    assert da._display_for_point(100, 100, displays) == 1
    assert da._display_for_point(2000, 200, displays) == 2
    assert da._display_for_point(5000, 500, displays) == 3


def test_display_for_point_falls_back_to_the_first_display():
    displays = [(1, 0.0, 0.0, 1920.0, 1080.0)]
    assert da._display_for_point(99_999, 99_999, displays) == 1
    assert da._display_for_point(0, 0, []) == 1


def test_format_screens_line_is_empty_for_a_single_screen():
    assert da._format_screens_line([{"screen": 1, "app": "Warp", "title": "x"}]) == ""
    assert da._format_screens_line([]) == ""


def test_format_screens_line_lists_every_screen():
    line = da._format_screens_line([
        {"screen": 1, "app": "Warp", "title": "pi"},
        {"screen": 2, "app": "ChatGPT", "title": "Audit"},
        {"screen": 3, "app": "Brave Browser", "title": ""},
    ])
    assert line.startswith("screens: ")
    assert "1=Warp" in line
    assert "2=ChatGPT" in line
    assert "3=Brave Browser" in line


def test_format_screens_line_skips_entries_without_an_app():
    line = da._format_screens_line([
        {"screen": 1, "app": "Warp", "title": ""},
        {"screen": 2, "app": "", "title": "ghost"},
        {"screen": 3, "app": "Brave Browser", "title": ""},
    ])
    assert "2=" not in line
    assert "3=Brave Browser" in line


def test_screens_scope_defaults_to_all_when_unset_or_invalid(monkeypatch):
    from distr.core import settings as settings_module

    monkeypatch.setattr(settings_module, "load_settings_from_db", lambda: {})
    assert da._screens_scope() == "all"

    monkeypatch.setattr(
        settings_module, "load_settings_from_db", lambda: {"initiative_awareness_screens": "nonsense"}
    )
    assert da._screens_scope() == "all"


def test_screens_scope_honours_focused(monkeypatch):
    from distr.core import settings as settings_module

    monkeypatch.setattr(
        settings_module, "load_settings_from_db", lambda: {"initiative_awareness_screens": "focused"}
    )
    assert da._screens_scope() == "focused"


def test_screens_scope_never_raises(monkeypatch):
    from distr.core import settings as settings_module

    def boom():
        raise RuntimeError("no db")

    monkeypatch.setattr(settings_module, "load_settings_from_db", boom)
    assert da._screens_scope() == "all"


def test_tier_a_screens_returns_a_list_and_never_raises():
    screens = da._tier_a_screens()
    assert isinstance(screens, list)
    for entry in screens:
        assert set(entry) >= {"screen", "app", "title"}
        assert isinstance(entry["screen"], int)
        assert entry["app"]


def test_snapshot_includes_screens_and_scope_is_respected(monkeypatch):
    monkeypatch.setattr(da, "_screens_scope", lambda: "all")
    monkeypatch.setattr(da, "_tier_a_screens", lambda: [
        {"screen": 1, "app": "Warp", "title": "a"},
        {"screen": 2, "app": "Brave", "title": "b"},
    ])
    monkeypatch.setattr(da, "_tier_a_frontmost_and_title", lambda: ("Warp", "a"))
    monkeypatch.setattr(da, "_tier_b_interactive_labels", lambda **k: ("", [], False))

    snap = da.compact_desktop_snapshot()
    assert len(snap["screens"]) == 2
    assert "screens: " in snap["line"]

    monkeypatch.setattr(da, "_screens_scope", lambda: "focused")
    snap2 = da.compact_desktop_snapshot()
    assert snap2["screens"] == []


def test_desktop_for_situational_exposes_screens(monkeypatch):
    monkeypatch.setattr(da, "get_cached_snapshot", lambda: {
        "line": "desktop: Warp",
        "content_hash": "abc",
        "app": "Warp",
        "title": "x",
        "screens": [{"screen": 1, "app": "Warp", "title": "x"}],
        "stale": False,
        "age_s": 1.0,
    })
    frag = da.desktop_for_situational()
    assert frag["screens"][0]["app"] == "Warp"
