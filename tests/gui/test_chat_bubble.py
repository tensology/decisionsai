"""Unit tests for ChatBubbleWidget.

Tests positioning, clamping to screen bounds, show/hide behaviour,
and size recalculation.

Requirements: 5.5, 5.6, 6.2, 6.5
"""

from __future__ import annotations

import sys
from unittest.mock import patch

import pytest
from PyQt6.QtCore import QRect, Qt
from PyQt6.QtWidgets import QApplication

from distr.gui.oracle.chat_bubble import (
    ChatBubbleWidget,
    _BUBBLE_PADDING,
    _MARGIN,
    _TAIL_HEIGHT,
)


# ---------------------------------------------------------------------------
# QApplication singleton — required for any QWidget instantiation
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session", autouse=True)
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    yield app


_SCREEN = QRect(0, 0, 1920, 1080)


@pytest.fixture()
def bubble():
    """Create a ChatBubbleWidget with a mocked screen geometry."""
    with patch.object(
        ChatBubbleWidget,
        "_available_screen_rect",
        return_value=_SCREEN,
    ):
        w = ChatBubbleWidget()
        yield w
        w.close()


# ---------------------------------------------------------------------------
# Window flags / attributes
# ---------------------------------------------------------------------------

class TestWindowSetup:
    """Verify frameless, transparent, stays-on-top flags."""

    def test_frameless_flag(self, bubble):
        flags = bubble.windowFlags()
        assert flags & Qt.WindowType.FramelessWindowHint

    def test_stays_on_top_flag(self, bubble):
        flags = bubble.windowFlags()
        assert flags & Qt.WindowType.WindowStaysOnTopHint

    def test_translucent_background(self, bubble):
        assert bubble.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)


# ---------------------------------------------------------------------------
# show_text / hide_bubble
# ---------------------------------------------------------------------------

class TestShowHide:
    def test_show_text_makes_visible(self, bubble):
        bubble.show_text("Hello")
        assert bubble.isVisible()
        assert bubble._text == "Hello"

    def test_hide_bubble_hides(self, bubble):
        bubble.show_text("Hello")
        bubble.hide_bubble()
        assert not bubble.isVisible()

    def test_show_text_updates_text(self, bubble):
        bubble.show_text("First")
        bubble.show_text("Second")
        assert bubble._text == "Second"


# ---------------------------------------------------------------------------
# reposition — clamping to screen bounds
# ---------------------------------------------------------------------------

class TestReposition:
    """Verify that reposition() clamps the bubble within screen bounds."""

    def test_positions_above_oracle_by_default(self, bubble):
        bubble.show_text("Test")
        oracle_rect = QRect(900, 500, 120, 120)
        bubble.reposition(oracle_rect)

        # Bubble bottom + margin should be at or above oracle top
        assert bubble.y() + bubble.height() + _MARGIN <= oracle_rect.top() + 1

    def test_clamps_left_edge(self, bubble):
        bubble.show_text("Test")
        oracle_rect = QRect(-50, 500, 120, 120)
        bubble.reposition(oracle_rect)

        assert bubble.x() >= 0

    def test_clamps_right_edge(self, bubble):
        bubble.show_text("Test")
        oracle_rect = QRect(1850, 500, 120, 120)
        bubble.reposition(oracle_rect)

        assert bubble.x() + bubble.width() <= _SCREEN.right()

    def test_falls_below_when_no_room_above(self, bubble):
        bubble.show_text("Test")
        # Oracle at very top of screen — no room above
        oracle_rect = QRect(900, 0, 120, 120)
        bubble.reposition(oracle_rect)

        # Bubble should be below the oracle
        assert bubble.y() >= oracle_rect.bottom()

    def test_clamps_bottom_edge(self, bubble):
        bubble.show_text("Test")
        oracle_rect = QRect(900, 1060, 120, 120)
        bubble.reposition(oracle_rect)

        assert bubble.y() + bubble.height() <= _SCREEN.bottom()


# ---------------------------------------------------------------------------
# Size recalculation
# ---------------------------------------------------------------------------

class TestSizeRecalc:
    def test_size_grows_with_longer_text(self, bubble):
        bubble.show_text("Hi")
        small_h = bubble.height()

        bubble.show_text("This is a much longer piece of text that should wrap across lines")
        large_h = bubble.height()

        assert large_h >= small_h

    def test_includes_tail_height(self, bubble):
        bubble.show_text("X")
        assert bubble.height() >= 2 * _BUBBLE_PADDING + _TAIL_HEIGHT

    def test_minimum_width(self, bubble):
        bubble.show_text("")
        assert bubble.width() >= 40
