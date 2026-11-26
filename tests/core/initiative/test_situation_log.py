"""The situation log is bounded working memory, not a record."""

import pytest

from distr.core.initiative import situation_log as sl


@pytest.fixture(autouse=True)
def _clean():
    sl._reset_for_tests()
    yield
    sl._reset_for_tests()


def _situation(line="desktop: Warp", screens=None, app="Warp", title="pi"):
    return {"line": line, "app": app, "title": title, "screens": screens or []}


def test_records_and_reads_back():
    assert sl.record(_situation(), now=1000.0) is True
    rows = sl.recent(now=1000.0)
    assert len(rows) == 1
    assert rows[0]["line"] == "desktop: Warp"


def test_an_unchanged_situation_is_not_re_logged():
    sl.record(_situation(line="desktop: Warp"), now=1000.0)
    assert sl.record(_situation(line="desktop: Warp"), now=1001.0) is False
    assert len(sl.recent(now=1001.0)) == 1


def test_a_changed_situation_is_logged():
    sl.record(_situation(line="desktop: Warp"), now=1000.0)
    assert sl.record(_situation(line="desktop: Brave"), now=1001.0) is True
    assert len(sl.recent(now=1001.0)) == 2


def test_empty_or_malformed_situations_are_ignored():
    assert sl.record({}, now=1000.0) is False
    assert sl.record({"line": "   "}, now=1000.0) is False
    assert sl.record("not a dict", now=1000.0) is False
    assert sl.record(None, now=1000.0) is False
    assert sl.recent(now=1000.0) == []


def test_entries_older_than_max_age_are_pruned():
    sl.record(_situation(line="desktop: old"), now=1000.0)
    sl.record(_situation(line="desktop: new"), now=1000.0 + sl.MAX_AGE_S + 1)
    lines = [r["line"] for r in sl.recent(window_s=sl.MAX_AGE_S * 2, now=1000.0 + sl.MAX_AGE_S + 1)]
    assert lines == ["desktop: new"]


def test_the_log_is_capped_by_count():
    for i in range(sl.MAX_ENTRIES + 50):
        sl.record(_situation(line=f"desktop: app{i}"), now=1000.0 + i)
    assert len(sl.recent(window_s=sl.MAX_AGE_S * 2, now=1000.0 + sl.MAX_ENTRIES + 49)) <= sl.MAX_ENTRIES


def test_recent_honours_the_window_and_limit():
    for i in range(10):
        sl.record(_situation(line=f"desktop: app{i}"), now=1000.0 + i * 60)
    # window covers only the last 5 minutes
    rows = sl.recent(window_s=300.0, now=1000.0 + 9 * 60)
    assert len(rows) <= 6
    assert sl.recent(window_s=300.0, limit=2, now=1000.0 + 9 * 60)[-1]["line"] == "desktop: app9"


def test_screens_are_carried_through():
    sl.record(_situation(screens=[
        {"screen": 1, "app": "Warp", "title": "pi"},
        {"screen": 2, "app": "Brave", "title": ""},
    ]), now=1000.0)
    row = sl.recent(now=1000.0)[0]
    assert [s["app"] for s in row["screens"]] == ["Warp", "Brave"]


def test_screens_entries_without_an_app_are_dropped():
    sl.record(_situation(screens=[{"screen": 1, "app": ""}, {"screen": 2, "app": "Brave"}]), now=1000.0)
    row = sl.recent(now=1000.0)[0]
    assert [s["app"] for s in row["screens"]] == ["Brave"]


def test_summarise_is_empty_with_nothing_recorded():
    assert sl.summarise(now=1000.0) == ""


def test_summarise_names_the_apps_and_ages():
    sl.record(_situation(line="a", screens=[{"screen": 1, "app": "Warp", "title": "pi"}]), now=1000.0)
    sl.record(_situation(line="b", screens=[{"screen": 2, "app": "Photoshop", "title": "poster"}]), now=1030.0)
    digest = sl.summarise(now=1060.0)
    assert "Warp" in digest
    assert "Photoshop" in digest
    assert "-60s" in digest
    assert "-30s" in digest


def test_summarise_falls_back_to_the_app_when_there_are_no_screens():
    sl.record(_situation(line="a", app="Warp", title="pi", screens=[]), now=1000.0)
    digest = sl.summarise(now=1010.0)
    assert "Warp" in digest


def test_recent_returns_copies_not_internal_state():
    sl.record(_situation(), now=1000.0)
    rows = sl.recent(now=1000.0)
    rows[0]["line"] = "mutated"
    assert sl.recent(now=1000.0)[0]["line"] == "desktop: Warp"


def test_clear_empties_the_log():
    sl.record(_situation(), now=1000.0)
    sl.clear()
    assert sl.recent(now=1000.0) == []
