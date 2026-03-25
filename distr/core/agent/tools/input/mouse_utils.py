"""
Shared mouse movement utilities.

Provides a natural, human-like mouse movement function used across
all tools that need to move the cursor (vision analyzer, mouse_movement,
navigation, etc.).
"""

import math
import random
import time
import logging

logger = logging.getLogger(__name__)

try:
    import pyautogui
    pyautogui.FAILSAFE = False
except ImportError:
    pyautogui = None


def smooth_move_to(
    x: int,
    y: int,
    duration: float = 0.0,
    min_duration: float = 0.08,
    max_duration: float = 0.35,
):
    """
    Move the mouse to (x, y) with fluid, human-like motion.

    Uses a quintic ease-in-out curve with slight control-point jitter
    so the path isn't a perfectly straight line.  Duration auto-scales
    with distance when *duration* is 0 (the default).

    Args:
        x, y:          Target logical coordinates.
        duration:      Total move time in seconds.  0 = auto-scale.
        min_duration:  Floor for auto-scaled duration.
        max_duration:  Ceiling for auto-scaled duration.
    """
    if not pyautogui:
        return

    sx, sy = pyautogui.position()
    dx, dy = x - sx, y - sy
    dist = math.hypot(dx, dy)

    # Very short distance — just jump
    if dist < 4:
        pyautogui.moveTo(x, y)
        return

    # Auto-scale duration based on distance (feels natural)
    if duration <= 0:
        # ~0.12s for short hops, up to max_duration for cross-screen moves
        duration = min(max_duration, max(min_duration, dist / 3000))

    # Number of intermediate points — enough for smooth 60fps-ish feel
    steps = max(8, int(duration / 0.008))

    # Slight random control-point offset for a natural arc
    # (humans don't move in perfectly straight lines)
    perp_x, perp_y = -dy, dx  # perpendicular vector
    perp_len = max(dist, 1)
    jitter = random.uniform(-0.08, 0.08)  # subtle curve
    cpx = sx + dx * 0.5 + (perp_x / perp_len) * dist * jitter
    cpy = sy + dy * 0.5 + (perp_y / perp_len) * dist * jitter

    start = time.perf_counter()
    for i in range(1, steps + 1):
        t = i / steps
        # Quintic ease-in-out: fast middle, gentle start/end
        if t < 0.5:
            e = 16 * t ** 5
        else:
            e = 1 - (-2 * t + 2) ** 5 / 2

        # Quadratic Bézier: P = (1-e)²·S + 2(1-e)e·CP + e²·T
        inv = 1 - e
        mx = inv * inv * sx + 2 * inv * e * cpx + e * e * x
        my = inv * inv * sy + 2 * inv * e * cpy + e * e * y

        pyautogui.moveTo(int(mx), int(my))

        # Pace the loop to match target duration
        elapsed = time.perf_counter() - start
        expected = duration * (i / steps)
        sleep = expected - elapsed
        if sleep > 0:
            time.sleep(sleep)

    # Snap to exact target
    pyautogui.moveTo(x, y)
