from __future__ import annotations

import logging

import pytest

from distr.app.agent_worker import run_agent_session


class _DeadManagerProxy:
    """A Manager-like proxy whose __len__ fails the way a dead manager does."""

    def __init__(self, exc):
        self._exc = exc

    def __len__(self):
        raise self._exc


@pytest.mark.parametrize(
    "exc",
    [
        ConnectionRefusedError(61, "Connection refused"),
        EOFError("EOF when reading a line"),
        BrokenPipeError(32, "Broken pipe"),
        OSError(61, "Connection refused"),
    ],
    ids=["ConnectionRefusedError", "EOFError", "BrokenPipeError", "OSError"],
)
def test_run_agent_session_exits_cleanly_when_manager_is_gone(monkeypatch, caplog, exc):
    exits = []

    def fake_exit(code):
        exits.append(code)
        raise SystemExit(code)

    monkeypatch.setattr("distr.app.agent_worker.os._exit", fake_exit)
    monkeypatch.setattr("distr.app.worker_logging.setup_worker_logging", lambda: None)

    caplog.set_level(logging.DEBUG)
    with pytest.raises(SystemExit) as raised:
        run_agent_session({}, screen_info_cache=_DeadManagerProxy(exc))

    assert raised.value.code == 0
    assert exits == [0]
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "shared-memory manager is gone" in warnings[0].getMessage()
    assert type(exc).__name__ in warnings[0].getMessage()
    assert warnings[0].exc_info is None or warnings[0].exc_info == (None, None, None)
    assert not any(r.levelno >= logging.ERROR for r in caplog.records)
