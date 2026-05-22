import threading
import pytest
from datetime import datetime, timezone, timedelta

from distr.core.initiative.draft_queue import DraftEntry, DraftQueue
from distr.core.initiative.tiers import PermissionTier


def make_entry(entry_id="test-id", hours_until_expiry=24):
    now = datetime.now(tz=timezone.utc)
    return DraftEntry(
        id=entry_id,
        action_type="suggestion",
        description="Test entry",
        draft="draft content",
        reason="test reason",
        created_at=now.isoformat(),
        expires_at=(now + timedelta(hours=hours_until_expiry)).isoformat(),
    )


@pytest.fixture
def tmp_queue(tmp_path):
    path = str(tmp_path / "drafts.json")
    return DraftQueue(path=path)


class TestDraftQueueAdd:
    def test_add_single_entry(self, tmp_queue):
        entry = make_entry("e1")
        tmp_queue.add(entry)
        assert len(tmp_queue.get_all()) == 1

    def test_add_multiple_entries(self, tmp_queue):
        for i in range(5):
            tmp_queue.add(make_entry(f"e{i}"))
        assert len(tmp_queue.get_all()) == 5


class TestDraftQueueRemove:
    def test_remove_existing(self, tmp_queue):
        entry = make_entry("e1")
        tmp_queue.add(entry)
        result = tmp_queue.remove("e1")
        assert result is True
        assert len(tmp_queue.get_all()) == 0

    def test_remove_nonexistent(self, tmp_queue):
        result = tmp_queue.remove("nonexistent")
        assert result is False

    def test_clear_removes_all_entries(self, tmp_path):
        path = str(tmp_path / "drafts.json")
        q = DraftQueue(path=path)
        q.add(make_entry("e1"))
        q.add(make_entry("e2"))

        assert q.clear() == 2
        assert q.get_all() == []
        assert DraftQueue(path=path).get_all() == []

    def test_clear_empty_queue(self, tmp_queue):
        assert tmp_queue.clear() == 0
        assert tmp_queue.get_all() == []


class TestDraftQueueExpiry:
    def test_expire_old_removes_expired(self, tmp_path):
        path = str(tmp_path / "drafts.json")
        q = DraftQueue(path=path)
        now = datetime.now(tz=timezone.utc)
        expired_entry = DraftEntry(
            id="expired",
            action_type="suggestion",
            description="expired",
            draft="",
            reason="test",
            created_at=(now - timedelta(hours=50)).isoformat(),
            expires_at=(now - timedelta(hours=2)).isoformat(),
        )
        live_entry = make_entry("live", hours_until_expiry=24)
        q.add(expired_entry)
        q.add(live_entry)
        count = q.expire_old()
        assert count == 1
        remaining = [e.id for e in q.get_all()]
        assert "expired" not in remaining
        assert "live" in remaining

    def test_expire_old_idempotent(self, tmp_path):
        path = str(tmp_path / "drafts.json")
        q = DraftQueue(path=path)
        now = datetime.now(tz=timezone.utc)
        expired_entry = DraftEntry(
            id="expired",
            action_type="suggestion",
            description="expired",
            draft="",
            reason="test",
            created_at=(now - timedelta(hours=50)).isoformat(),
            expires_at=(now - timedelta(hours=2)).isoformat(),
        )
        q.add(expired_entry)
        q.expire_old()
        count2 = q.expire_old()
        assert count2 == 0


class TestDraftQueuePersistence:
    def test_persistence_round_trip(self, tmp_path):
        path = str(tmp_path / "drafts.json")
        q1 = DraftQueue(path=path)
        entry = make_entry("persist-test")
        q1.add(entry)
        # Reload
        q2 = DraftQueue(path=path)
        loaded = q2.get_all()
        assert len(loaded) == 1
        assert loaded[0].id == "persist-test"
        assert loaded[0].description == entry.description
        assert loaded[0].permission_tier == PermissionTier.APPROVE

    def test_empty_queue_no_file_error(self, tmp_path):
        path = str(tmp_path / "nonexistent.json")
        q = DraftQueue(path=path)
        assert q.get_all() == []


class TestDraftQueueThreadSafety:
    def test_concurrent_adds(self, tmp_path):
        path = str(tmp_path / "drafts.json")
        q = DraftQueue(path=path)
        errors = []

        def add_entry(i):
            try:
                q.add(make_entry(f"thread-{i}"))
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=add_entry, args=(i,)) for i in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == []
        assert len(q.get_all()) == 20
