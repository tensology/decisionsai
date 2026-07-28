"""GlowEngine — drives glow effects based on Event_Response glow parameters.

Replaces the inline glow animation code in OracleWindow with a configurable
engine driven by Event_Response fields (glow, glow_color, glow_speed, glow_style).

Requirements: 5.7, 5.8, 1.8
"""

from __future__ import annotations

import math
from typing import Tuple

from PyQt6.QtCore import QObject, QTimer, pyqtSignal


# ---------------------------------------------------------------------------
# Pure computation helpers (easily testable without Qt event loop)
# ---------------------------------------------------------------------------

def breathing_alpha(elapsed_ms: int, speed_ms: int) -> float:
    """Sinusoidal alpha oscillation between 0.3 and 1.0.

    *speed_ms* is the period of one full cycle (trough → peak → trough).
    Returns a value in [0.3, 1.0].
    """
    if speed_ms <= 0:
        return 0.3
    phase = (elapsed_ms % speed_ms) / speed_ms  # 0.0 → 1.0
    # sin goes -1→+1; map to 0→1 then scale to 0.3→1.0
    raw = (math.sin(2 * math.pi * phase - math.pi / 2) + 1.0) / 2.0
    return 0.3 + 0.7 * raw


def pulse_alpha(elapsed_ms: int, speed_ms: int) -> float:
    """PTT-style pulsing — same sinusoidal curve as breathing.

    Kept as a separate function so the caller can distinguish the trigger
    semantics (mouse-hold vs continuous) while sharing the math.
    """
    return breathing_alpha(elapsed_ms, speed_ms)


def _in_out_cubic(t: float) -> float:
    """Attempt to replicate QEasingCurve.Type.InOutCubic."""
    if t < 0.5:
        return 4.0 * t * t * t
    p = 2.0 * t - 2.0
    return 0.5 * p * p * p + 1.0


def fade_alpha(elapsed_ms: int, speed_ms: int) -> float:
    """InOutCubic easing, infinite loop (forward then backward per cycle).

    One full cycle = *speed_ms* × 2 (forward half + backward half).
    Returns a value in [0.3, 1.0].
    """
    if speed_ms <= 0:
        return 0.3
    full_cycle = speed_ms * 2
    pos = (elapsed_ms % full_cycle) / full_cycle  # 0.0 → 1.0
    # First half: ease in-out 0→1, second half: ease in-out 1→0
    if pos < 0.5:
        t = pos * 2.0  # 0→1
        raw = _in_out_cubic(t)
    else:
        t = (pos - 0.5) * 2.0  # 0→1
        raw = 1.0 - _in_out_cubic(t)
    return 0.3 + 0.7 * raw


def flash_alpha(elapsed_ms: int, speed_ms: int, num_cycles: int = 2) -> float | None:
    """N-cycle flash then stop.

    One cycle = *speed_ms* × 2 (on-ramp + off-ramp).
    Returns alpha in [0.3, 1.0] while active, or ``None`` when all cycles
    have completed (caller should stop the animation).
    """
    if speed_ms <= 0 or num_cycles <= 0:
        return None
    full_cycle = speed_ms * 2
    total_duration = full_cycle * num_cycles
    if elapsed_ms >= total_duration:
        return None  # done
    pos = (elapsed_ms % full_cycle) / full_cycle
    if pos < 0.5:
        raw = pos * 2.0
    else:
        raw = 1.0 - (pos - 0.5) * 2.0
    return 0.3 + 0.7 * raw


# ---------------------------------------------------------------------------
# GlowEngine
# ---------------------------------------------------------------------------

# Glow periods are measured in hundreds of milliseconds, so 30 fps remains
# visually smooth while halving translucent-window repaints during continuous
# hands-free and dictation modes.
_TICK_MS = 33  # ~30 fps


class GlowEngine(QObject):
    """Drives glow effects based on Event_Response glow parameters."""

    # Emitted every tick with (color_rgb_tuple, alpha_0_to_1).
    glow_updated = pyqtSignal(tuple, float)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)

        self._color: Tuple[int, int, int] = (0, 0, 0)
        self._speed: int = 1000
        self._style: str = "breathing"
        self._elapsed: int = 0
        self._flash_cycles: int = 2
        self._active: bool = False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def apply(self, response) -> None:
        """Apply glow from an *EventResponse*.

        Reads ``glow``, ``glow_color``, ``glow_speed``, ``glow_style``.
        If ``response.glow`` is False the current glow is stopped.
        """
        if not response.glow:
            self.stop()
            return

        style = response.glow_style
        color = response.glow_color
        speed = response.glow_speed

        match style:
            case "breathing":
                self._start_breathing(color, speed)
            case "pulse":
                self._start_pulse(color, speed)
            case "fade":
                self._start_fade(color, speed)
            case "flash":
                self._start_flash(color, speed)
            case _:
                self.stop()

    def stop(self) -> None:
        """Stop the current glow animation and emit zero alpha."""
        self._timer.stop()
        if self._active:
            self.glow_updated.emit(self._color, 0.0)
        self._active = False
        self._elapsed = 0

    @property
    def active(self) -> bool:
        return self._active

    # ------------------------------------------------------------------
    # Style starters
    # ------------------------------------------------------------------

    def _start_breathing(self, glow_color: Tuple[int, int, int], glow_speed: int) -> None:
        self._begin(glow_color, glow_speed, "breathing")

    def _start_pulse(self, glow_color: Tuple[int, int, int], glow_speed: int) -> None:
        self._begin(glow_color, glow_speed, "pulse")

    def _start_fade(self, glow_color: Tuple[int, int, int], glow_speed: int) -> None:
        self._begin(glow_color, glow_speed, "fade")

    def _start_flash(self, glow_color: Tuple[int, int, int], glow_speed: int, num_cycles: int = 2) -> None:
        self._flash_cycles = num_cycles
        self._begin(glow_color, glow_speed, "flash")

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _begin(self, color: Tuple[int, int, int], speed: int, style: str) -> None:
        self._timer.stop()
        self._color = tuple(color)
        self._speed = speed
        self._style = style
        self._elapsed = 0
        self._active = True
        self._timer.start(_TICK_MS)

    def _tick(self) -> None:
        self._elapsed += _TICK_MS

        match self._style:
            case "breathing":
                alpha = breathing_alpha(self._elapsed, self._speed)
            case "pulse":
                alpha = pulse_alpha(self._elapsed, self._speed)
            case "fade":
                alpha = fade_alpha(self._elapsed, self._speed)
            case "flash":
                result = flash_alpha(self._elapsed, self._speed, self._flash_cycles)
                if result is None:
                    self.stop()
                    return
                alpha = result
            case _:
                self.stop()
                return

        self.glow_updated.emit(self._color, alpha)
