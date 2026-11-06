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

    @patch("distr.gui.oracle.webm_player.WebMPlayer")
    def test_webm_reuses_decoded_player_for_same_animation(self, MockWebMPlayer):
        """Returning to an avatar state must not decode its WebM again."""
        from distr.gui.oracle.animation_player import AnimationPlayer

        mock_instance = MagicMock()
        mock_instance.frame_ready = MagicMock()
        mock_instance.frame_ready.connect = MagicMock()
        MockWebMPlayer.return_value = mock_instance

        player = AnimationPlayer()
        player.set_size(200, 200)
        player.load("cached.webm", chroma_key=(0, 255, 0))
        player.load("cached.webm", chroma_key=(0, 255, 0))

        MockWebMPlayer.assert_called_once_with(player)
        mock_instance.load_async.assert_called_once()
        mock_instance.set_size.assert_called_once_with(200, 200)
        assert player._webm_player is mock_instance

    @patch("distr.gui.oracle.webm_player.WebMPlayer")
    def test_preloaded_webm_is_used_on_first_visible_load(self, MockWebMPlayer):
        from distr.gui.oracle.animation_player import AnimationPlayer

        mock_instance = MagicMock()
        mock_instance.frame_ready = MagicMock()
        mock_instance.frame_ready.connect = MagicMock()
        MockWebMPlayer.return_value = mock_instance

        player = AnimationPlayer()
        player.set_size(200, 200)
        player.preload("shortcut.webm", chroma_key=(0, 255, 0))
        player.load("shortcut.webm", chroma_key=(0, 255, 0))

        MockWebMPlayer.assert_called_once_with(player)
        mock_instance.load_async.assert_called_once()
        assert player._webm_player is mock_instance

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


class TestWebMPlayerResourceLimits:
    class _Image:
        def scaled(self, *args, **kwargs):
            return self

    class _Pixmap:
        def __init__(self, image):
            self.image = image
            self.dpr = 1.0

        @classmethod
        def fromImage(cls, image):
            return cls(image)

        def setDevicePixelRatio(self, value):
            self.dpr = value

        def toImage(self):
            return self.image

    @patch("distr.gui.oracle.webm_player.QPixmap", _Pixmap)
    @patch("distr.gui.oracle.webm_player._extract_frames_imageio")
    def test_load_caps_frame_rate_and_releases_decoded_images(self, extract):
        from distr.gui.oracle.webm_player import WebMPlayer

        extract.return_value = ([self._Image(), self._Image()], 60.0)
        player = WebMPlayer()
        player.set_size(80, 80)

        player.load("small-oracle.webm")

        assert player._interval_ms == 66
        assert len(player._frames) == 2
        assert player._source_images == []

    @patch("distr.gui.oracle.webm_player.QPixmap", _Pixmap)
    def test_resize_reuses_rendered_frames_without_retaining_sources(self):
        from distr.gui.oracle.webm_player import WebMPlayer

        player = WebMPlayer()
        player._source_images = [self._Image()]
        player._rebuild_frames()

        player.set_size(120, 120)

        assert len(player._frames) == 1
        assert player._source_images == []


class TestAnimationCacheBudget:
    @staticmethod
    def _fake_player(size):
        player = MagicMock()
        player.memory_bytes = size
        return player

    def test_byte_budget_evicts_old_frames_immediately(self):
        from distr.gui.oracle.animation_player import AnimationPlayer

        player = AnimationPlayer()
        player._webm_cache_bytes_limit = 64
        old, recent = self._fake_player(40), self._fake_player(40)
        player._webm_cache.update(old=old, recent=recent)
        player._trim_webm_cache()
        assert list(player._webm_cache) == ["recent"]
        old.release_frames.assert_called_once()
        old.deleteLater.assert_called_once()

    def test_oversize_active_animation_is_kept_and_preloads_are_evicted(self):
        from distr.gui.oracle.animation_player import AnimationPlayer

        player = AnimationPlayer()
        player._webm_cache_bytes_limit = 64
        active, preload = self._fake_player(80), self._fake_player(20)
        player._webm_player = active
        player._webm_cache.update(active=active, preload=preload)
        player._trim_webm_cache()
        assert list(player._webm_cache) == ["active"]
        active.release_frames.assert_not_called()
        preload.release_frames.assert_called_once()

    def test_count_limit_keeps_active_entry_and_evicts_oldest_inactive(self):
        from distr.gui.oracle.animation_player import AnimationPlayer

        player = AnimationPlayer()
        player._webm_cache_limit = 2
        active, old, recent = [self._fake_player(1) for _ in range(3)]
        player._webm_player = active
        player._webm_cache.update(active=active, old=old, recent=recent)
        player._trim_webm_cache()
        assert list(player._webm_cache) == ["active", "recent"]
        old.release_frames.assert_called_once()
        active.release_frames.assert_not_called()

    @patch("distr.gui.oracle.webm_player.WebMPlayer")
    def test_visible_oversize_load_is_not_deleted_by_trim(self, factory):
        from distr.gui.oracle.animation_player import AnimationPlayer

        active = self._fake_player(100)
        factory.return_value = active
        player = AnimationPlayer()
        player._webm_cache_bytes_limit = 64
        player.load("large.webm")
        assert player._webm_player is active
        assert list(player._webm_cache.values()) == [active]
        active.release_frames.assert_not_called()

    def test_frame_accounting_uses_physical_dimensions_and_depth(self):
        from distr.gui.oracle.webm_player import WebMPlayer

        player = WebMPlayer()
        frame = MagicMock()
        frame.width.return_value = 160
        frame.height.return_value = 160
        frame.depth.return_value = 32
        player._frames = [frame, frame]
        assert player.memory_bytes == 160 * 160 * 4 * 2
        player.release_frames()
        assert player._frames == []
        assert player._source_images == []
        assert player.memory_bytes == 0


def test_chroma_key_despills_only_subject_edges():
    import numpy as np
    from PyQt6.QtGui import QImage

    from distr.gui.oracle.webm_player import _apply_chroma_key_to_image

    pixels = np.zeros((40, 40, 4), dtype=np.uint8)
    pixels[:, :, 1] = 255  # BGRA storage: solid green background.
    pixels[:, :, 3] = 255
    pixels[8:32, 8:32, :3] = (90, 90, 90)
    pixels[8:32, 8:32, 3] = 255
    pixels[8:32, 8, :3] = (80, 220, 80)  # Green spill on subject edge.
    pixels[19:22, 19:22, :3] = (40, 180, 40)  # Legitimate interior green.
    image = QImage(
        pixels.data,
        40,
        40,
        40 * 4,
        QImage.Format.Format_ARGB32,
    ).copy()

    cleaned = _apply_chroma_key_to_image(image, (0, 255, 0), threshold=35)
    ptr = cleaned.bits()
    ptr.setsize(40 * 40 * 4)
    result = np.frombuffer(memoryview(ptr), dtype=np.uint8).reshape((40, 40, 4))

    assert result[0, 0, 3] == 0
    assert result[12, 8, 1] == max(result[12, 8, 0], result[12, 8, 2])
    assert tuple(result[20, 20, :3]) == (40, 180, 40)


def test_decoder_prepares_each_frame_before_reading_the_next(monkeypatch):
    import numpy as np
    import sys
    from types import SimpleNamespace
    from distr.gui.oracle.webm_player import _extract_frames_imageio

    prepared = []

    def frames(*args, **kwargs):
        yield np.zeros((20, 20, 3), dtype=np.uint8)
        assert len(prepared) == 1
        yield np.zeros((20, 20, 3), dtype=np.uint8)

    def prepare(image):
        prepared.append(image.size())
        return image.scaled(4, 4)

    # imageio is optional; production can use the cv2 fallback instead.
    iio = SimpleNamespace(
        improps=lambda *a, **k: SimpleNamespace(fps=24),
        imiter=frames,
        imread=lambda *a, **k: pytest.fail("whole-video allocation"),
    )
    monkeypatch.setitem(sys.modules, "imageio", SimpleNamespace(v3=iio))
    monkeypatch.setitem(sys.modules, "imageio.v3", iio)
    images, fps = _extract_frames_imageio("test.webm", prepare_frame=prepare)
    assert fps == 24
    assert len(images) == 2
    assert [(img.width(), img.height()) for img in images] == [(4, 4), (4, 4)]
