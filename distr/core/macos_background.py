"""Keep helper and worker Python processes out of the macOS Dock."""

from __future__ import annotations

import logging
import sys

logger = logging.getLogger(__name__)

_DOCK_HIDDEN = False


def hide_process_from_dock() -> None:
    """
    Mark the current process as a UI-element (no Dock icon, no bounce).

    Call before Qt/AppKit initialisation in background workers and sidecar helpers.
    """
    global _DOCK_HIDDEN
    if _DOCK_HIDDEN or sys.platform != "darwin":
        return

    try:
        import ctypes

        class ProcessSerialNumber(ctypes.Structure):
            _fields_ = [
                ("highLongOfPSN", ctypes.c_uint32),
                ("lowLongOfPSN", ctypes.c_uint32),
            ]

        psn = ProcessSerialNumber(0, 2)  # kCurrentProcess
        lib = ctypes.CDLL(
            "/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices"
        )
        lib.TransformProcessType(ctypes.byref(psn), ctypes.c_uint32(4))  # UIElementApplication
        _DOCK_HIDDEN = True
    except Exception as exc:
        logger.debug("hide_process_from_dock failed: %s", exc)


# Snippet embedded in sidecar `-c` helpers (stdlib only).
SIDECAR_DOCK_HIDE_PREAMBLE = """
import ctypes
class _PSN(ctypes.Structure):
    _fields_ = [("highLongOfPSN", ctypes.c_uint32), ("lowLongOfPSN", ctypes.c_uint32)]
try:
    _lib = ctypes.CDLL("/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices")
    _lib.TransformProcessType(ctypes.byref(_PSN(0, 2)), ctypes.c_uint32(4))
except Exception:
    pass
"""
