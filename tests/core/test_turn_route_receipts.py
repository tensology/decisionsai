"""2026-10-22: per-turn route label data (display-only) is recorded once per user turn."""
from types import SimpleNamespace

from distr.core.workflow import development_harness as harness


class _Query:
    def __init__(self, row):
        self.row = row

    def filter(self, *args, **kwargs):
        return self

    def order_by(self, *args, **kwargs):
        return self

    def first(self):
        return self.row


class _Db:
    def __init__(self, row_id):
        self.row = SimpleNamespace(id=row_id) if row_id else None

    def query(self, *args, **kwargs):
        return _Query(self.row)


def _receipt(mode="auto", backend="pi", model="deepseek-v4.1-flash:cloud", complexity="medium"):
    return {
        "complexity": complexity,
        "configured": {"route_mode": mode},
        "selected": {"backend": backend, "provider": "ollama", "model": model},
    }


def test_records_auto_turn_keyed_by_latest_user_row():
    development = {}
    harness._remember_turn_route(_Db(1771), 1746, development, _receipt())
    entry = development["turn_routes"]["1771"]
    assert entry["route_mode"] == "auto"
    assert entry["complexity"] == "medium"
    assert entry["backend"] == "pi"
    assert entry["model"] == "deepseek-v4.1-flash:cloud"


def test_first_decision_for_a_turn_is_kept():
    development = {}
    harness._remember_turn_route(_Db(10), 1, development, _receipt(model="first"))
    harness._remember_turn_route(_Db(10), 1, development, _receipt(model="second"))
    assert development["turn_routes"]["10"]["model"] == "first"


def test_manual_turn_is_marked_fixed():
    development = {}
    harness._remember_turn_route(_Db(11), 1, development, _receipt(mode="manual", backend="cursor", model="grok-4.7-medium"))
    assert development["turn_routes"]["11"]["route_mode"] == "manual"


def test_ignores_missing_receipt_or_user_row():
    development = {}
    harness._remember_turn_route(_Db(None), 1, development, _receipt())
    harness._remember_turn_route(_Db(5), 1, development, None)
    harness._remember_turn_route(_Db(5), 1, development, {"complexity": "low"})
    assert "turn_routes" not in development


def test_history_is_capped(monkeypatch):
    monkeypatch.setattr(harness, "TURN_ROUTE_LIMIT", 3)
    development = {}
    for row in range(1, 6):
        harness._remember_turn_route(_Db(row), 1, development, _receipt())
    assert sorted(development["turn_routes"], key=int) == ["3", "4", "5"]
