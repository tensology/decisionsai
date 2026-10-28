"""Reconfiguring logging must not close handlers the main process still uses.

The main process installs ONE FileHandler shared by "distr", the root logger and
"distr.agent.activity". The worker setup used to close every handler it detached,
which closed that shared file. Every later log emit then raised
"I/O operation on closed file" and the running app went silent.
"""

from __future__ import annotations

import logging


def _snapshot(*names):
    return {name: list(logging.getLogger(name).handlers) for name in names}


def _restore(snapshot):
    for name, handlers in snapshot.items():
        logger = logging.getLogger(name)
        logger.handlers = list(handlers)


def test_worker_logging_keeps_a_shared_file_handler_open(tmp_path):
    from distr.app import worker_logging

    shared_path = tmp_path / "shared.log"
    shared = logging.FileHandler(shared_path, encoding="utf-8")

    names = ("distr", "", "distr.agent.activity")
    before = _snapshot(*names)
    for name in names:
        logging.getLogger(name).addHandler(shared)
    try:
        worker_logging.setup_worker_logging()

        assert not shared.stream.closed, "the main process handler was closed"
        # Detaching is fine; the handler must remain usable by whoever still holds it.
        shared.emit(
            logging.LogRecord(
                name="test", level=logging.INFO, pathname=__file__, lineno=1,
                msg="still logging", args=None, exc_info=None,
            )
        )
        shared.flush()
        assert "still logging" in shared_path.read_text(encoding="utf-8")
    finally:
        _restore(before)
        try:
            shared.close()
        except Exception:
            pass


def test_no_module_closes_logging_handlers():
    """A static guard: detach handlers, never close a shared one."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    offenders = []
    for path in (root / "distr").rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        if "removeHandler" in text and ".close()" in text:
            for line in text.splitlines():
                stripped = line.strip()
                if "removeHandler" in text and stripped.endswith(".close()") and "handler" in stripped:
                    offenders.append(f"{path.relative_to(root)}: {stripped}")
    assert offenders == [], f"handlers are being closed after detach: {offenders}"
