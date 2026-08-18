"""
Toggle availability of sidecar-backed tools when local HTTP health flips (R23 / TASK 14).

The initiative scheduler calls :func:`tick_sidecar_tool_availability` about once per minute.
Right after :func:`~distr.core.agent.tools.loader.warm_tool_cache` registers tools,
:func:`prime_sidecar_tool_availability` runs once so you do not wait up to a minute for the first sync.

When the sidecar returns HTTP ``GET /health`` successfully, those tools are marked available;
when it fails, they are hidden from :meth:`ToolRegistry.get_all` and semantic retrieval is rebuilt.
"""

from __future__ import annotations

import logging
from typing import Final

logger = logging.getLogger(__name__)

# LangChain tool ``name`` fields — must match accessibility_tree + sidecar_tools classes.
SIDECAR_DEPENDENT_TOOL_NAMES: Final[frozenset[str]] = frozenset(
    {
        "get_window_tree",
        "find_element",
        "move_to_element",
        "click_element_by_id",
        "run_python",
        "drag_to",
        "scroll",
        "wait_for_element",
        "list_windows",
        "focus_window",
        "launch_app",
        "set_window_bounds",
    }
)

_last_sidecar_ok: bool | None = None


def reset_sidecar_tool_watch_for_tests() -> None:
    """Reset probe state (tests only)."""
    global _last_sidecar_ok
    _last_sidecar_ok = None


def prime_sidecar_tool_availability() -> None:
    """
    Forget the last health snapshot and probe immediately.

    Call after native tools are (re)registered — e.g. end of ``warm_tool_cache`` — so sidecar-backed
    tools match HTTP reality without waiting for the next initiative schedule tick (~60s).
    """
    global _last_sidecar_ok
    _last_sidecar_ok = None
    tick_sidecar_tool_availability()


def _apply_sidecar_availability(ok: bool) -> bool:
    """
    Set availability for every registered sidecar tool. Returns True if anything changed.
    """
    from distr.core.agent.tool_retriever import schedule_tool_index_rebuild
    from distr.core.agent.tools.registry import get_tool_registry

    reg = get_tool_registry()
    changed = False
    for name in SIDECAR_DEPENDENT_TOOL_NAMES:
        rec = reg.get_record(name)
        if rec is None:
            continue
        if rec.available == ok:
            continue
        reg.set_available(name, ok)
        changed = True
        logger.info(
            "sidecar_tool_watch: tool %r availability -> %s",
            name,
            ok,
        )
    if changed:
        schedule_tool_index_rebuild(reg.get_all())
    return changed


def tick_sidecar_tool_availability() -> None:
    """
    Probe sidecar HTTP health and sync registry + embedding index.

    Idempotent when health is unchanged. Safe to call from any thread (registry is locked).
    """
    global _last_sidecar_ok

    try:
        from distr.core.agent.tools.input.sidecar_http import is_sidecar_reachable
    except Exception:
        logger.debug("tick_sidecar_tool_availability: sidecar_http import failed", exc_info=True)
        return

    ok = is_sidecar_reachable(timeout=2.0)

    if _last_sidecar_ok is None:
        _apply_sidecar_availability(ok)
        _last_sidecar_ok = ok
        logger.debug("sidecar_tool_watch: initial health=%s", ok)
        return

    if ok == _last_sidecar_ok:
        return

    logger.info(
        "sidecar_tool_watch: health transition %s -> %s (updating tools + index)",
        _last_sidecar_ok,
        ok,
    )
    _apply_sidecar_availability(ok)
    _last_sidecar_ok = ok
