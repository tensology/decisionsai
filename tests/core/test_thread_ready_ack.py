"""Acknowledgement of finished Development threads (folder orange state)."""
from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import create_engine, text

from distr.core.workflow.thread_ready_ack import (
    ensure_chat_ready_acknowledged_at,
    is_ready_unaddressed,
)


def test_unaddressed_only_for_settled_statuses_with_missing_or_stale_ack():
    ready = datetime(2026, 11, 11, 13, 0, 0)
    later = ready + timedelta(minutes=5)
    earlier = ready - timedelta(minutes=5)

    assert is_ready_unaddressed("completed", ready, None) is True
    assert is_ready_unaddressed("failed", ready, None) is True
    assert is_ready_unaddressed("blocked", ready, None) is True
    assert is_ready_unaddressed("completed", ready, earlier) is True
    assert is_ready_unaddressed("completed", ready, later) is False
    assert is_ready_unaddressed("completed", ready, ready) is False
    assert is_ready_unaddressed("running", ready, None) is False
    assert is_ready_unaddressed("completed_noop", ready, None) is False
    assert is_ready_unaddressed("cancelled", ready, None) is False
    assert is_ready_unaddressed("completed", None, later) is False


def test_ensure_column_seeds_only_on_first_add(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'ack.db'}")
    with engine.connect() as conn:
        conn.execute(text("CREATE TABLE chats (id INTEGER PRIMARY KEY, title TEXT, ready_placeholder TEXT)"))
        conn.execute(text("INSERT INTO chats (id, title) VALUES (1, 'historical'), (2, 'also historical')"))
        conn.commit()

    ensure_chat_ready_acknowledged_at(engine)
    with engine.connect() as conn:
        cols = {row[1] for row in conn.execute(text("PRAGMA table_info(chats)"))}
        assert "ready_acknowledged_at" in cols
        rows = list(conn.execute(text("SELECT id, ready_acknowledged_at FROM chats ORDER BY id")))
    assert all(value is not None for _id, value in rows)

    # A later completion is stored as NULL and must stay NULL on subsequent startups.
    with engine.connect() as conn:
        conn.execute(text("INSERT INTO chats (id, title, ready_acknowledged_at) VALUES (3, 'new completion', NULL)"))
        conn.commit()

    ensure_chat_ready_acknowledged_at(engine)
    with engine.connect() as conn:
        stamped = {
            row[0]: row[1]
            for row in conn.execute(text("SELECT id, ready_acknowledged_at FROM chats ORDER BY id"))
        }
    assert stamped[1] is not None
    assert stamped[2] is not None
    assert stamped[3] is None


# 2026-11-12 (Paul, Merrypak 575): the first-startup seed stored zone-aware stamps
# ("2026-11-11T19:34:17.847Z") while run completed_at is naive UTC. Comparing them
# raised TypeError, acknowledge-ready returned 500, and the project folder stayed
# orange after every ready thread under it had been opened.
def test_unaddressed_compares_zone_aware_acks_with_naive_completions():
    from datetime import timezone

    completed = datetime(2026, 11, 12, 7, 3, 24)
    seeded = datetime(2026, 11, 11, 19, 34, 17, tzinfo=timezone.utc)
    later = datetime(2026, 11, 12, 9, 36, 0, tzinfo=timezone.utc)
    assert is_ready_unaddressed("blocked", completed, seeded) is True
    assert is_ready_unaddressed("blocked", completed, later) is False
    # +02:00 (SAST) 09:00 is 07:00 UTC: still before the 07:03 completion.
    sast = timezone(timedelta(hours=2))
    assert is_ready_unaddressed("blocked", completed, datetime(2026, 11, 12, 9, 0, tzinfo=sast)) is True
    assert is_ready_unaddressed("blocked", completed, datetime(2026, 11, 12, 9, 4, tzinfo=sast)) is False


def test_opening_a_thread_with_a_seeded_zone_aware_ack_clears_ready(monkeypatch):
    from contextlib import contextmanager
    from datetime import timezone
    from types import SimpleNamespace

    from distr.core.workflow import development_threads, thread_ready_ack

    chat = SimpleNamespace(
        id=575, parent_id=None,
        ready_acknowledged_at=datetime(2026, 11, 11, 19, 34, 17, 847000, tzinfo=timezone.utc),
    )
    completed = datetime(2026, 11, 12, 7, 3, 24, 586604)

    class Session:
        commits = 0

        def get(self, _model, chat_id):
            return chat if int(chat_id) == 575 else None

        def commit(self):
            Session.commits += 1

        def refresh(self, _row):
            return None

    @contextmanager
    def provider():
        yield Session()

    monkeypatch.setattr(development_threads, "is_development_thread", lambda _db, _chat: True)
    monkeypatch.setattr(
        thread_ready_ack, "latest_run_snapshot",
        lambda _db, _chat: {"status": "blocked", "completed_at": completed, "completed_at_iso": completed.isoformat()},
    )
    opened = datetime(2026, 11, 12, 9, 36, 0)
    result = thread_ready_ack.acknowledge_ready_thread(575, now=opened, _session_provider=provider)

    assert result["acknowledged"] is True
    assert result["ready_acknowledged_at"] == opened.isoformat()
    assert Session.commits == 1
    assert thread_ready_ack.is_ready_unaddressed("blocked", completed, chat.ready_acknowledged_at) is False
