"""Real Qt event-loop regressions for responsive voice-state transitions."""
import os
import time
from threading import Event

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt6.QtCore import QThread, QTimer
from PyQt6.QtGui import QColor, QGuiApplication, QImage

from distr.gui.oracle import webm_player as webm
from distr.gui.oracle.animation_player import AnimationPlayer


@pytest.fixture(scope="module")
def app():
    return QGuiApplication.instance() or QGuiApplication([])


def wait_for(app, predicate, timeout=3):
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.001)
    assert predicate()


def image(color="red"):
    result = QImage(20, 20, QImage.Format.Format_ARGB32)
    result.fill(QColor(color))
    return result


def test_first_frame_and_event_loop_do_not_wait_for_full_decode(app, monkeypatch):
    release, entered = Event(), Event()
    threads, frames, ticks = [], [], []

    def decode(path, prepare_frame):
        threads.append(QThread.currentThread())
        first = prepare_frame(image())
        entered.set()
        assert release.wait(3)
        return [first, prepare_frame(image("blue"))], 24

    monkeypatch.setattr(webm, "_extract_frames_imageio", decode)
    player = AnimationPlayer()
    player.frame_ready.connect(lambda frame: frames.append(frame))
    timer = QTimer()
    timer.timeout.connect(lambda: ticks.append(True))
    timer.start(1)
    try:
        player.load("dictation.webm")
        player.play()
        wait_for(app, lambda: entered.is_set() and frames and ticks)
        assert threads[0] != app.thread()
        assert len(player._webm_player._frames) == 1
        assert player._webm_player._decode_task is not None
    finally:
        release.set()
        timer.stop()
        webm._decode_pool.waitForDone(3000)
        app.processEvents()
    assert len(player._webm_player._frames) == 2
    player.stop()


def test_switch_cancels_inflight_clip_and_drops_stale_frames(app, monkeypatch):
    release, entered = Event(), Event()
    frames = []

    def decode(path, prepare_frame):
        if path == "old.webm":
            entered.set()
            assert release.wait(3)
        return [prepare_frame(image("red" if path == "old.webm" else "blue"))], 24

    monkeypatch.setattr(webm, "_extract_frames_imageio", decode)
    player = AnimationPlayer()
    player.frame_ready.connect(lambda frame: frames.append(frame.toImage().pixelColor(0, 0).name()))
    try:
        player.load("old.webm")
        player.play()
        wait_for(app, entered.is_set)
        old = player._webm_player
        task = old._decode_task
        player.load("new.webm")
        player.play()
        assert task.cancelled.is_set()
        release.set()
        wait_for(app, lambda: frames and player._webm_player._decode_task is None)
        assert set(frames) == {"#0000ff"}
    finally:
        release.set()
        webm._decode_pool.waitForDone(3000)
        player.stop()
        app.processEvents()


def test_async_completion_enforces_cache_budget(app, monkeypatch):
    monkeypatch.setattr(webm, "_extract_frames_imageio",
                        lambda path, prepare_frame: ([prepare_frame(image())], 24))
    player = AnimationPlayer()
    player._webm_cache_bytes_limit = 1
    player.preload("preload.webm")
    wait_for(app, lambda: not player._webm_cache)
    player.load("active.webm")
    player.play()
    wait_for(app, lambda: player._webm_player._decode_task is None)
    assert len(player._webm_cache) == 1
    assert player._webm_player.memory_bytes == 20 * 20 * 4
    player.stop()


def test_preload_never_emits_visible_frames_and_is_reused(app, monkeypatch):
    calls, frames = [], []

    def decode(path, prepare_frame):
        calls.append(path)
        return [prepare_frame(image())], 24

    monkeypatch.setattr(webm, "_extract_frames_imageio", decode)
    player = AnimationPlayer()
    player.frame_ready.connect(lambda frame: frames.append(frame))
    player.preload("ready.webm")
    cached = next(iter(player._webm_cache.values()))
    wait_for(app, lambda: cached._decode_task is None)
    assert not frames
    player.load("ready.webm")
    player.play()
    assert frames
    assert calls == ["ready.webm"]
    player.stop()


def test_async_falls_back_to_cv2_on_decode_error(app, monkeypatch):
    def fail(*args, **kwargs):
        raise ValueError("unsupported codec")

    monkeypatch.setattr(webm, "_extract_frames_imageio", fail)
    monkeypatch.setattr(webm, "_extract_frames_cv2",
                        lambda path, prepare_frame: ([prepare_frame(image())], 24))
    player = webm.WebMPlayer()
    player.load_async("fallback.webm")
    player.play()
    wait_for(app, lambda: player._decode_task is None)
    assert len(player._frames) == 1
    assert player._timer.isActive()
    player.stop()


def test_motion_starts_before_decode_finishes_and_waits_at_buffer_end(app, monkeypatch):
    release = Event()
    frames = []

    def decode(path, prepare_frame):
        images = [prepare_frame(image()), prepare_frame(image("blue"))]
        assert release.wait(3)
        images.append(prepare_frame(image("green")))
        return images, 24

    monkeypatch.setattr(webm, "_extract_frames_imageio", decode)
    player = webm.WebMPlayer()
    player.frame_ready.connect(lambda frame: frames.append(frame.toImage().pixelColor(0, 0).name()))
    try:
        player.load_async("dictation.webm")
        player.play()
        wait_for(app, lambda: "#0000ff" in frames)
        assert player._decode_task is not None
        assert len(player._frames) == 2
        for _ in range(4):
            player._advance()
            assert player._current_index == 1
            assert player._forward
        release.set()
        wait_for(app, lambda: player._decode_task is None)
        assert len(player._frames) == 3
        player._advance()
        assert player._current_index == 2
        player._advance()
        assert player._current_index == 1
        assert not player._forward
    finally:
        release.set()
        webm._decode_pool.waitForDone(3000)
        player.stop()
        app.processEvents()
