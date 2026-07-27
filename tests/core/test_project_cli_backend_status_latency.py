from __future__ import annotations

import time

from distr.core.project_cli_backends.base import BackendStatus


class _Backend:
    def __init__(self, backend_id: str, delay: float = 0.0) -> None:
        self.id = backend_id
        self.name = backend_id.title()
        self.description = f"{backend_id} backend"
        self.delay = delay
        self.calls = 0

    def setup_status(self) -> BackendStatus:
        self.calls += 1
        time.sleep(self.delay)
        return BackendStatus(
            id=self.id,
            name=self.name,
            installed=True,
            ready=True,
            state="ready",
            message="ready",
            can_receive_remote_handoff=True,
        )


def test_backend_inventory_has_one_bounded_deadline_not_serial_timeouts(monkeypatch):
    from distr.core.project_cli_backends import registry

    fast = _Backend("fast")
    slow = _Backend("slow", delay=0.3)
    monkeypatch.setattr(registry, "list_backends", lambda: [fast, slow])
    monkeypatch.setattr(registry, "normalize_backend_id", lambda value: value or "fast")
    registry._BACKEND_STATUS_CACHE.clear()

    started = time.monotonic()
    result = registry.get_backend_statuses(refresh=True, timeout_seconds=0.03)
    elapsed = time.monotonic() - started

    assert elapsed < 0.2
    assert result["backends"][0]["state"] == "ready"
    assert result["backends"][1]["state"] == "checking"
    assert result["backends"][1]["readiness_check"] == "timed_out"


def test_backend_inventory_uses_short_ttl_cache(monkeypatch):
    from distr.core.project_cli_backends import registry

    backend = _Backend("cached")
    monkeypatch.setattr(registry, "list_backends", lambda: [backend])
    monkeypatch.setattr(registry, "normalize_backend_id", lambda value: value or "cached")
    registry._BACKEND_STATUS_CACHE.clear()

    registry.get_backend_statuses(refresh=True)
    registry.get_backend_statuses()

    assert backend.calls == 1
