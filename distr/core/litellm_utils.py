"""Shared LiteLLM configuration — keep background LLM calls quiet in the console."""

from __future__ import annotations

import contextlib
import io
import logging
import os
from typing import Any, Callable, TypeVar

logger = logging.getLogger(__name__)

_configured = False

F = TypeVar("F", bound=Callable[..., Any])


def configure_litellm() -> None:
    """Apply process-wide LiteLLM quiet defaults (safe to call repeatedly)."""
    global _configured
    os.environ.setdefault("LITELLM_LOG", "ERROR")

    for name in ("LiteLLM", "litellm"):
        logging.getLogger(name).setLevel(logging.ERROR)

    try:
        import litellm
    except ImportError:
        return

    litellm.set_verbose = False
    if hasattr(litellm, "suppress_debug_info"):
        litellm.suppress_debug_info = True

    _configured = True


def litellm_completion(*args, **kwargs):
    """Call ``litellm.completion`` without spamming stderr on provider failures."""
    configure_litellm()
    import litellm

    with contextlib.redirect_stderr(io.StringIO()):
        return litellm.completion(*args, **kwargs)
