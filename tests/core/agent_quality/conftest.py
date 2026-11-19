"""Run coroutines on a private loop in a worker thread.

Earlier tests in the full suite leave an event loop running on the main thread,
which makes plain asyncio.run() fail with "cannot be called from a running
event loop". These tests must not depend on suite order.
"""

from __future__ import annotations

import asyncio
import threading

import pytest


def run_isolated(coro):
    box = {}

    def worker():
        loop = asyncio.new_event_loop()
        try:
            box["value"] = loop.run_until_complete(coro)
        except BaseException as exc:  # re-raised in the test thread
            box["error"] = exc
        finally:
            loop.close()

    thread = threading.Thread(target=worker)
    thread.start()
    thread.join()
    if "error" in box:
        raise box["error"]
    return box.get("value")


@pytest.fixture(autouse=True)
def _isolated_asyncio_run(monkeypatch):
    import asyncio as _asyncio

    monkeypatch.setattr(_asyncio, "run", lambda coro, **_: run_isolated(coro))
    yield
