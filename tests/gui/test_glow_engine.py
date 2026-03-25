"""Unit tests for GlowEngine.

Tests the pure computation functions directly (no Qt event loop needed)
and the GlowEngine class integration via QTimer mocking.

Requirements: 5.7, 5.8
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Tuple
from unittest.mock import MagicMock

import pytest

from distr.gui.oracle.glow_engine import (
    GlowEngine,
    _TICK_MS,
    _in_out_cubic,
    breathing_alpha,
    fade_alpha,
    flash_alpha,
    pulse_alpha,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

@dataclass
class FakeEventResponse:
    """Minimal stand-in for EventResponse used by GlowEngine.apply()."""
    glow: bool = True
    glow_color: Tuple[int, int, int] = (0, 170, 255)
    glow_speed: int = 1000
    glow_style: str = "breathing"


# ---------------------------------------------------------------------------
# breathing_alpha tests
# ---------------------------------------------------------------------------

class TestBreathingAlpha:
    """Breathing style: sinusoidal alpha oscillation 0.3 → 1.0."""

    def test_range_always_within_bounds(self):
        """Alpha must stay in [0.3, 1.0] for any elapsed time."""
        for elapsed in range(0, 5000, 7):
            a = breathing_alpha(elapsed, 1000)
            assert 0.3 - 1e-9 <= a <= 1.0 + 1e-9, f"Out of range at {elapsed}ms: {a}"

    def test_starts_at_minimum(self):
        """At elapsed=0 the sine starts at its trough → alpha ≈ 0.3."""
        a = breathing_alpha(0, 1000)
        assert abs(a - 0.3) < 0.01

    def test_reaches_peak_at_half_cycle(self):
        """At half the cycle period the sine peaks → alpha ≈ 1.0."""
        a = breathing_alpha(500, 1000)
        assert abs(a - 1.0) < 0.01

    def test_returns_to_minimum_at_full_cycle(self):
        """After one full cycle alpha returns to ≈ 0.3."""
        a = breathing_alpha(1000, 1000)
        assert abs(a - 0.3) < 0.01

    def test_zero_speed_returns_minimum(self):
        assert breathing_alpha(500, 0) == 0.3


# ---------------------------------------------------------------------------
# pulse_alpha tests
# ---------------------------------------------------------------------------

class TestPulseAlpha:
    """Pulse style: same sinusoidal curve as breathing."""

    def test_matches_breathing(self):
        for elapsed in range(0, 3000, 13):
            assert pulse_alpha(elapsed, 1000) == breathing_alpha(elapsed, 1000)


# ---------------------------------------------------------------------------
# fade_alpha tests
# ---------------------------------------------------------------------------

class TestFadeAlpha:
    """Fade style: InOutCubic easing, infinite loop."""

    def test_range_always_within_bounds(self):
        for elapsed in range(0, 5000, 7):
            a = fade_alpha(elapsed, 1000)
            assert 0.3 - 1e-9 <= a <= 1.0 + 1e-9, f"Out of range at {elapsed}ms: {a}"

    def test_starts_at_minimum(self):
        a = fade_alpha(0, 1000)
        assert abs(a - 0.3) < 0.01

    def test_reaches_peak_at_half_cycle(self):
        """At the midpoint of the forward half (speed_ms) alpha should peak."""
        # Full cycle = 2000ms. Peak at 1000ms (end of forward half).
        a = fade_alpha(1000, 1000)
        assert abs(a - 1.0) < 0.01

    def test_returns_to_minimum_after_full_cycle(self):
        a = fade_alpha(2000, 1000)
        assert abs(a - 0.3) < 0.01

    def test_uses_in_out_cubic_easing(self):
        """Verify the easing curve shape: at 25% of forward half, value should
        follow InOutCubic (steep middle, gentle ends)."""
        # Forward half goes from 0 to speed_ms (1000). At 250ms → t=0.25
        a = fade_alpha(250, 1000)
        expected_raw = _in_out_cubic(0.25)
        expected = 0.3 + 0.7 * expected_raw
        assert abs(a - expected) < 0.01

    def test_zero_speed_returns_minimum(self):
        assert fade_alpha(500, 0) == 0.3


# ---------------------------------------------------------------------------
# flash_alpha tests
# ---------------------------------------------------------------------------

class TestFlashAlpha:
    """Flash style: N cycles then stop."""

    def test_returns_none_after_all_cycles(self):
        """After 2 cycles at 500ms speed (total 2000ms), flash should stop."""
        result = flash_alpha(2000, 500, num_cycles=2)
        assert result is None

    def test_active_during_cycles(self):
        """During the cycles, alpha should be a float in [0.3, 1.0]."""
        for elapsed in range(0, 1999, 16):
            a = flash_alpha(elapsed, 500, num_cycles=2)
            assert a is not None
            assert 0.3 - 1e-9 <= a <= 1.0 + 1e-9

    def test_single_cycle(self):
        """With 1 cycle at 500ms, total duration is 1000ms."""
        assert flash_alpha(999, 500, num_cycles=1) is not None
        assert flash_alpha(1000, 500, num_cycles=1) is None

    def test_zero_speed_returns_none(self):
        assert flash_alpha(0, 0, num_cycles=2) is None

    def test_zero_cycles_returns_none(self):
        assert flash_alpha(0, 500, num_cycles=0) is None


# ---------------------------------------------------------------------------
# InOutCubic helper
# ---------------------------------------------------------------------------

class TestInOutCubic:
    def test_boundaries(self):
        assert abs(_in_out_cubic(0.0)) < 1e-9
        assert abs(_in_out_cubic(1.0) - 1.0) < 1e-9

    def test_midpoint(self):
        assert abs(_in_out_cubic(0.5) - 0.5) < 1e-9


# ---------------------------------------------------------------------------
# GlowEngine integration tests (mocked QTimer)
# ---------------------------------------------------------------------------

class TestGlowEngineApply:
    """Test GlowEngine.apply() dispatching and stop() behaviour."""

    @pytest.fixture()
    def engine(self):
        """Create a GlowEngine and replace its QTimer with a mock."""
        eng = GlowEngine()
        eng._timer = MagicMock()
        eng._timer.timeout = MagicMock()
        yield eng

    def test_apply_breathing_starts_timer(self, engine):
        resp = FakeEventResponse(glow=True, glow_style="breathing")
        engine.apply(resp)
        engine._timer.start.assert_called_with(_TICK_MS)
        assert engine._active is True
        assert engine._style == "breathing"

    def test_apply_pulse_starts_timer(self, engine):
        resp = FakeEventResponse(glow=True, glow_style="pulse")
        engine.apply(resp)
        engine._timer.start.assert_called_with(_TICK_MS)
        assert engine._style == "pulse"

    def test_apply_fade_starts_timer(self, engine):
        resp = FakeEventResponse(glow=True, glow_style="fade")
        engine.apply(resp)
        engine._timer.start.assert_called_with(_TICK_MS)
        assert engine._style == "fade"

    def test_apply_flash_starts_timer(self, engine):
        resp = FakeEventResponse(glow=True, glow_style="flash")
        engine.apply(resp)
        engine._timer.start.assert_called_with(_TICK_MS)
        assert engine._style == "flash"

    def test_apply_glow_false_calls_stop(self, engine):
        # First start something
        engine._active = True
        resp = FakeEventResponse(glow=False)
        engine.apply(resp)
        engine._timer.stop.assert_called()
        assert engine._active is False

    def test_stop_resets_state(self, engine):
        engine._active = True
        engine._elapsed = 500
        engine.stop()
        engine._timer.stop.assert_called()
        assert engine._active is False
        assert engine._elapsed == 0

    def test_apply_stores_color_and_speed(self, engine):
        resp = FakeEventResponse(
            glow=True,
            glow_color=(255, 193, 7),
            glow_speed=1000,
            glow_style="fade",
        )
        engine.apply(resp)
        assert engine._color == (255, 193, 7)
        assert engine._speed == 1000
