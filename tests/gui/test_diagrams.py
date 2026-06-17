"""Tests for Mermaid diagram storage API."""

import time

from distr.gui.web.routes.diagrams import get_diagram, store_diagram, _purge_expired, _diagram_store, _diagram_lock, list_diagram_history


def test_store_and_get_diagram_roundtrip():
    with _diagram_lock:
        _diagram_store.clear()
    diagram_id = store_diagram("flowchart TD\n  A-->B", title="Test flow")
    row = get_diagram(diagram_id)
    assert row is not None
    assert row["title"] == "Test flow"
    assert "A-->B" in row["code"]


def test_diagram_history_persisted():
    from distr.gui.web.routes import diagrams as diagrams_mod

    diagrams_mod._save_history_rows([])
    diagram_id = store_diagram("sequenceDiagram\n  A->>B: hi", title="History test")
    history = list_diagram_history()
    assert any(item.get("id") == diagram_id for item in history)


def test_expired_diagram_falls_back_to_history():
    from distr.gui.web.routes import diagrams as diagrams_mod

    diagrams_mod._save_history_rows([])
    with _diagram_lock:
        _diagram_store.clear()
    diagram_id = store_diagram("flowchart LR\n  X-->Y", title="Expire me")
    with _diagram_lock:
        _diagram_store[diagram_id]["expires_at"] = time.time() - 1
        _purge_expired()
    row = get_diagram(diagram_id)
    assert row is not None
    assert row["title"] == "Expire me"
