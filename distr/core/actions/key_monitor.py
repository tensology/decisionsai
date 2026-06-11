"""Keyboard monitors for action recording and playback."""

from __future__ import annotations

import logging
import sys

logger = logging.getLogger(__name__)


class MacEscapeMonitor:
    """Global + local NSEvent monitors so Escape works when DecisionsAI is focused."""

    def __init__(self, on_escape, on_ctrl_space=None):
        self._on_escape = on_escape
        self._on_ctrl_space = on_ctrl_space
        self._monitors: list = []

    def start(self) -> bool:
        if sys.platform != "darwin":
            return False
        try:
            from AppKit import NSEvent, NSKeyDownMask

            try:
                from Quartz.CoreGraphics import kVK_Escape, kVK_Space
            except ImportError:
                kVK_Escape = 53
                kVK_Space = 49

            ns_control_key_mask = 1 << 18

            def _handle_key(event):
                key_code = event.keyCode()
                flags = event.modifierFlags()
                if key_code in (kVK_Escape, 53):
                    if self._on_escape:
                        self._on_escape()
                elif key_code in (kVK_Space, 49) and (flags & ns_control_key_mask):
                    if self._on_ctrl_space:
                        self._on_ctrl_space()

            def global_handler(event):
                try:
                    _handle_key(event)
                except Exception as exc:
                    logger.error("MacEscapeMonitor global handler error: %s", exc)
                return event

            def local_handler(event):
                try:
                    key_code = event.keyCode()
                    flags = event.modifierFlags()
                    if key_code in (kVK_Escape, 53):
                        if self._on_escape:
                            self._on_escape()
                        return None
                    if key_code in (kVK_Space, 49) and (flags & ns_control_key_mask):
                        if self._on_ctrl_space:
                            self._on_ctrl_space()
                        return None
                except Exception as exc:
                    logger.error("MacEscapeMonitor local handler error: %s", exc)
                return event

            global_monitor = NSEvent.addGlobalMonitorForEventsMatchingMask_handler_(
                NSKeyDownMask, global_handler
            )
            local_monitor = NSEvent.addLocalMonitorForEventsMatchingMask_handler_(
                NSKeyDownMask, local_handler
            )
            if global_monitor:
                self._monitors.append(global_monitor)
            if local_monitor:
                self._monitors.append(local_monitor)
            return bool(self._monitors)
        except Exception as exc:
            logger.error("MacEscapeMonitor start failed: %s", exc, exc_info=True)
            return False

    def stop(self) -> None:
        if sys.platform != "darwin":
            return
        try:
            from AppKit import NSEvent

            for monitor in self._monitors:
                try:
                    NSEvent.removeMonitor_(monitor)
                except Exception:
                    pass
        except Exception as exc:
            logger.warning("MacEscapeMonitor stop error: %s", exc)
        self._monitors = []
