"""Lightweight logging bootstrap for the spawned voice-agent process.

The worker must not import :mod:`distr.app.main` just to configure logging:
that module imports the complete Qt GUI and audio application graph, delaying
STT construction by several seconds on macOS spawn workers.
"""

from __future__ import annotations

import logging
import os
import sys
import time

from distr.core.paths import DB_DIR


_crash_log_file = None


def _console_level() -> int:
    explicit = (os.environ.get("DECISIONSAI_CONSOLE_LOG_LEVEL") or "").strip().upper()
    levels = {
        "DEBUG": logging.DEBUG,
        "INFO": logging.INFO,
        "WARNING": logging.WARNING,
        "ERROR": logging.ERROR,
        "CRITICAL": logging.CRITICAL,
    }
    if explicit in levels:
        return levels[explicit]
    enabled = (os.environ.get("DECISIONSAI_LOG_CONSOLE_INFO") or "").strip().lower()
    return logging.INFO if enabled in {"1", "true", "yes", "on"} else logging.WARNING


def _setup_crash_logging() -> None:
    global _crash_log_file
    try:
        import faulthandler

        crash_dir = os.path.expanduser("~/.decisions/logs")
        os.makedirs(crash_dir, exist_ok=True)
        crash_path = os.path.join(crash_dir, f"crash_{os.getpid()}.log")
        _crash_log_file = open(crash_path, "a")
        _crash_log_file.write(
            f"\n{'=' * 60}\nProcess {os.getpid()} started at {time.strftime('%Y-%m-%d %H:%M:%S')}\n"
        )
        _crash_log_file.flush()
        faulthandler.enable(_crash_log_file, all_threads=True)
    except Exception:
        pass


def setup_worker_logging() -> None:
    """Configure append-only worker logging without importing the GUI app."""
    _setup_crash_logging()
    os.environ.setdefault("LITELLM_LOG", "ERROR")

    log_dir = os.path.join(DB_DIR, "logs")
    os.makedirs(log_dir, exist_ok=True)
    log_path = os.path.join(log_dir, "decisions.log")

    for logger_name in ("distr", ""):
        target = logging.getLogger(logger_name)
        while target.handlers:
            handler = target.handlers[0]
            target.removeHandler(handler)
            handler.close()

    activity_logger = logging.getLogger("distr.agent.activity")
    while activity_logger.handlers:
        handler = activity_logger.handlers[0]
        activity_logger.removeHandler(handler)
        handler.close()

    console_stream = sys.stderr
    if hasattr(console_stream, "reconfigure"):
        try:
            console_stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    formatter = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setFormatter(formatter)
    file_handler.setLevel(logging.INFO)
    console_handler = logging.StreamHandler(console_stream)
    console_handler.setFormatter(formatter)
    console_handler.setLevel(_console_level())

    app_logger = logging.getLogger("distr")
    app_logger.setLevel(logging.INFO)
    app_logger.addHandler(file_handler)
    app_logger.addHandler(console_handler)
    app_logger.propagate = False

    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    root_logger.addHandler(file_handler)

    activity_logger.setLevel(logging.INFO)
    activity_logger.propagate = False
    activity_logger.addHandler(file_handler)
    show_activity = (os.environ.get("DECISIONSAI_AGENT_ACTIVITY_CONSOLE") or "1").strip().lower()
    if show_activity not in {"0", "false", "no", "off"}:
        activity_console = logging.StreamHandler(console_stream)
        activity_console.setFormatter(formatter)
        activity_console.setLevel(logging.INFO)
        activity_logger.addHandler(activity_console)

    for logger_name in (
        "httpcore",
        "httpx",
        "urllib3",
        "matplotlib",
        "PIL",
        "LiteLLM",
        "litellm",
        "sentence_transformers",
    ):
        logging.getLogger(logger_name).setLevel(logging.CRITICAL)

    for name in logging.root.manager.loggerDict:
        if not name.startswith("distr"):
            logging.getLogger(name).propagate = False
