"""The suggestion store: survives restarts, and a mute must stick."""

import pytest
from sqlalchemy import create_engine, text

from distr.core.initiative import suggestion_store as store


@pytest.fixture()
def db(monkeypatch, tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'suggestions.db'}")
    monkeypatch.setattr(store, "engine", engine)
    store.ensure_tables()
    return engine


def test_records_and_lists_newest_first(db):
    first = store.add(kind="idea", text="First idea", confidence=0.7)
    second = store.add(kind="risk", text="Second idea", confidence=0.9)
    assert first and second and second > first
    rows = store.recent()
    assert [r["text"] for r in rows] == ["Second idea", "First idea"]
    assert rows[0]["kind"] == "risk"


def test_unusable_suggestions_are_not_recorded(db):
    assert store.add(kind="idea", text="", confidence=0.9) is None
    assert store.add(kind="idea", text="   ", confidence=0.9) is None
    assert store.recent() == []


def test_confidence_is_clamped(db):
    store.add(kind="idea", text="over", confidence=5)
    store.add(kind="idea", text="under", confidence=-3)
    scores = {r["text"]: r["confidence"] for r in store.recent()}
    assert scores["over"] == 1.0
    assert scores["under"] == 0.0


def test_kind_is_normalised(db):
    store.add(kind="  Dropped   Thread ", text="x", confidence=0.5)
    assert store.recent()[0]["kind"] == "dropped thread"


def test_rubric_round_trips(db):
    store.add(kind="idea", text="x", confidence=0.5, rubric={"impact": 5})
    assert store.recent()[0]["rubric"] == {"impact": 5}


def test_dismiss_hides_from_the_inbox_but_keeps_the_row(db):
    sid = store.add(kind="idea", text="dismiss me", confidence=0.8)
    assert store.dismiss(sid) is True
    assert store.recent() == []
    assert len(store.recent(include_dismissed=True)) == 1
    # dismissing twice is a no-op
    assert store.dismiss(sid) is False


def test_dismiss_rejects_junk_ids(db):
    assert store.dismiss("not-an-id") is False
    assert store.dismiss(None) is False


def test_mute_and_unmute(db):
    assert store.is_muted("naming") is False
    assert store.mute_kind("Naming") is True
    assert store.is_muted("naming") is True
    assert store.is_muted("NAMING") is True
    assert store.muted_kinds() == ["naming"]
    assert store.unmute_kind("naming") is True
    assert store.is_muted("naming") is False


def test_muting_twice_is_idempotent(db):
    store.mute_kind("naming")
    store.mute_kind("naming")
    assert store.muted_kinds() == ["naming"]


def test_mute_lookup_fails_closed(db, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("db gone")

    monkeypatch.setattr(store, "engine", type("E", (), {"begin": staticmethod(boom)})())
    assert store.is_muted("anything") is True


def test_recent_texts_feeds_the_assessor(db):
    store.add(kind="idea", text="already said this", confidence=0.9)
    assert store.recent_texts() == ["already said this"]


def test_prune_drops_rows_past_the_keep_window(db):
    old = store.add(kind="idea", text="ancient", confidence=0.5)
    with db.begin() as conn:
        conn.execute(
            text("UPDATE initiative_suggestions SET created_at = :t WHERE id = :id"),
            {"t": 1_000_000.0, "id": old},
        )
    store.add(kind="idea", text="fresh", confidence=0.5)
    removed = store.prune(keep_days=30, now=1_000_000.0 + 31 * 86400)
    assert removed == 1
    assert [r["text"] for r in store.recent()] == ["fresh"]


def test_prune_caps_the_table_size(db, monkeypatch):
    monkeypatch.setattr(store, "MAX_ROWS", 3)
    for i in range(6):
        store.add(kind="idea", text=f"idea {i}", confidence=0.5)
    store.prune(keep_days=3650)
    rows = store.recent(limit=50)
    assert len(rows) == 3
    assert [r["text"] for r in rows] == ["idea 5", "idea 4", "idea 3"]


def test_store_failures_never_raise(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("db gone")

    monkeypatch.setattr(store, "engine", type("E", (), {"begin": staticmethod(boom)})())
    assert store.add(kind="idea", text="x", confidence=0.5) is None
    assert store.recent() == []
    assert store.recent_texts() == []
    assert store.dismiss(1) is False
    assert store.mute_kind("x") is False
    assert store.muted_kinds() == []
    assert store.prune() == 0
