"""Initiative test isolation (added 12 Nov 2026 with the durable attention store)."""
import pytest


@pytest.fixture(autouse=True)
def _isolated_attention_store(tmp_path, monkeypatch):
    """Each test gets its own attention store (ignored clients, asks already made, address links)."""
    store = tmp_path / "attention" / "initiative_attention.json"
    store.parent.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("distr.core.initiative.attention.store_path", lambda: store)
    yield store
