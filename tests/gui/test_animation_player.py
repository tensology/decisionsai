"""Unit tests for AnimationPlayer format detection and ping-pong boundary cases.

Tests:
- AnimationPlayer.load() routes .gif to GifPlayer and .webm to WebMPlayer
- advance_pingpong single-frame stays at 0
- advance_pingpong two-frame alternates 0→1→0

Requirements: 4.4, 4.5
"""

from __future__ import annotations

from unittest.mock import patch, MagicMock

import pytest

from distr.gui.oracle.webm_player import advance_pingpong, elapsed_frame_steps


# ---------------------------------------------------------------------------
# Ping-pong boundary cases (pure function — no Qt needed)
# ---------------------------------------------------------------------------


class TestAdvancePingpongBoundary:
    """Boundary cases for the pure advance_pingpong function."""

    def test_single_frame_stays_at_zero(self):
        """With only 1 frame, index must always be 0."""
        index, forward = advance_pingpong(0, 1, True)
        assert index == 0
        assert forward is True

        # Multiple advances still stay at 0
        for _ in range(10):
            index, forward = advance_pingpong(index, 1, forward)
            assert index == 0

    def test_two_frames_alternates(self):
        """With 2 frames, ping-pong should alternate: 0→1→0→1→0…"""
        index, forward = 0, True
        expected_sequence = [1, 0, 1, 0, 1, 0]
        for expected in expected_sequence:
            index, forward = advance_pingpong(index, 2, forward)
            assert index == expected, (
                f"Expected {expected}, got {index}"
            )

    def test_three_frames_full_cycle(self):
        """With 3 frames: 0→1→2→1→0→1→2→…"""
        index, forward = 0, True
        expected_sequence = [1, 2, 1, 0, 1, 2, 1, 0]
        for expected in expected_sequence:
            index, forward = advance_pingpong(index, 3, forward)
            assert index == expected

    def test_forward_direction_preserved(self):
        """Starting forward, direction stays True until hitting the end."""
        index, forward = 0, True
        index, forward = advance_pingpong(index, 5, forward)
        assert index == 1
        assert forward is True

    def test_direction_reverses_at_end(self):
        """At the last frame going forward, direction reverses."""
        # At index N-2 going forward → next is N-1 boundary
        index, forward = advance_pingpong(3, 5, True)
        assert index == 4
        assert forward is True
        # Now at N-1 going forward → should reverse
        index, forward = advance_pingpong(4, 5, True)
        assert index == 3
        assert forward is False

    def test_direction_reverses_at_start(self):
        """At index 1 going backward → next is 0, then reverses."""
        index, forward = advance_pingpong(1, 5, False)
        assert index == 0
        assert forward is False
        # Now at 0 going backward → should reverse
        index, forward = advance_pingpong(0, 5, False)
        assert index == 1
        assert forward is True


class TestElapsedFrameSteps:
    def test_normal_tick_advances_one_frame(self):
        steps, clock = elapsed_frame_steps(10.0, 10.03, 30)
        assert steps == 1
        assert clock == pytest.approx(10.03)

    def test_late_tick_skips_missed_frames_and_keeps_remainder(self):
        steps, clock = elapsed_frame_steps(10.0, 10.105, 30)
        assert steps == 3
        assert clock == pytest.approx(10.09)

    def test_first_tick_is_safe(self):
        steps, clock = elapsed_frame_steps(None, 42.0, 30)
        assert (steps, clock) == (1, 42.0)


# ---------------------------------------------------------------------------
# AnimationPlayer format detection
# ---------------------------------------------------------------------------


class TestAnimationPlayerFormatDetection:
    """AnimationPlayer.load() should route to GifPlayer for .gif
    and WebMPlayer for .webm."""

    @patch("distr.gui.oracle.animation_player.GifPlayer")
    def test_gif_creates_gif_player(self, MockGifPlayer):
        """Loading a .gif file should instantiate GifPlayer."""
        from distr.gui.oracle.animation_player import AnimationPlayer

        mock_instance = MagicMock()
        mock_instance.frame_ready = MagicMock()
        mock_instance.frame_ready.connect = MagicMock()
        MockGifPlayer.return_value = mock_instance

        player = AnimationPlayer()
        player.load("test_animation.gif")

        MockGifPlayer.assert_called_once_with(player)
        mock_instance.load.assert_called_once_with("test_animation.gif", playback="loop")
        assert player._player is mock_instance
        assert player._webm_player is None

    @patch("distr.gui.oracle.webm_player.WebMPlayer")
    def test_webm_creates_webm_player(self, MockWebMPlayer):
        """Loading a .webm file should instantiate WebMPlayer."""
        from distr.gui.oracle.animation_player import AnimationPlayer

        mock_instance = MagicMock()
        mock_instance.frame_ready = MagicMock()
        mock_instance.frame_ready.connect = MagicMock()
        MockWebMPlayer.return_value = mock_instance

        player = AnimationPlayer()
        player.load("test_animation.webm")

        assert player._player is None

    @patch("distr.gui.oracle.animation_player.GifPlayer")
    def test_gif_case_insensitive(self, MockGifPlayer):
        """Format detection should be case-insensitive."""
        from distr.gui.oracle.animation_player import AnimationPlayer

        mock_instance = MagicMock()
        mock_instance.frame_ready = MagicMock()
        mock_instance.frame_ready.connect = MagicMock()
        MockGifPlayer.return_value = mock_instance

        player = AnimationPlayer()
        player.load("animation.GIF")

        MockGifPlayer.assert_called_once()
