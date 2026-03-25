"""RenderStrategy — polymorphic rendering based on skin type.

Provides an abstract base class and two concrete implementations:
- OracleRenderer: round shape, ellipse mask, border, shadow, glow-on-hold
- AvatarRenderer: square shape, transparent background, no border/shadow

A factory function ``create_renderer`` selects the correct strategy from a
SkinConfig object.

Requirements: 3.1, 3.2, 3.3, 3.4, 3.6, 4.1, 4.2
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from PyQt6.QtCore import Qt, QRect
from PyQt6.QtGui import (
    QBrush,
    QColor,
    QPainter,
    QPen,
    QRegion,
)
from PyQt6.QtWidgets import QMainWindow

from distr.core.skin_config import SkinConfig


class RenderStrategy(ABC):
    """Abstract base for skin-type-specific rendering."""

    @abstractmethod
    def setup_window(self, window: QMainWindow) -> None:
        """Apply window flags, masks, and attributes for this skin type."""

    @abstractmethod
    def paint(self, painter: QPainter, content_rect: QRect) -> None:
        """Paint the background / border / shadow inside *content_rect*."""

    @abstractmethod
    def create_mask(self, width: int, height: int) -> QRegion:
        """Return a QRegion mask for the given dimensions."""


class OracleRenderer(RenderStrategy):
    """Round shape, border, shadow, glow-on-hold (Req 3.1-3.4, 3.6)."""

    def __init__(self, *, border: bool = True, shadow: bool = True, glow_on_hold: bool = True) -> None:
        self.border = border
        self.shadow = shadow
        self.glow_on_hold = glow_on_hold

    def setup_window(self, window: QMainWindow) -> None:
        window.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        mask = self.create_mask(window.width(), window.height())
        window.setMask(mask)

    def paint(self, painter: QPainter, content_rect: QRect) -> None:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        if self.shadow:
            shadow_color = QColor(0, 0, 0, 80)
            painter.setBrush(QBrush(shadow_color))
            painter.setPen(Qt.PenStyle.NoPen)
            shadow_rect = content_rect.adjusted(2, 2, 2, 2)
            painter.drawEllipse(shadow_rect)

        if self.border:
            pen = QPen(QColor(0, 0, 0), 2)
            painter.setPen(pen)
        else:
            painter.setPen(Qt.PenStyle.NoPen)

        painter.setBrush(QBrush(QColor(0, 0, 0)))
        painter.drawEllipse(content_rect)

    def create_mask(self, width: int, height: int) -> QRegion:
        return QRegion(0, 0, width, height, QRegion.RegionType.Ellipse)


class AvatarRenderer(RenderStrategy):
    """Square shape, transparent background, no border/shadow (Req 4.1, 4.2)."""

    def setup_window(self, window: QMainWindow) -> None:
        window.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        # No mask — full rectangular window, transparency handled by WA_TranslucentBackground

    def paint(self, painter: QPainter, content_rect: QRect) -> None:
        # Transparent background — nothing to paint
        pass

    def create_mask(self, width: int, height: int) -> QRegion:
        return QRegion(0, 0, width, height, QRegion.RegionType.Rectangle)


def create_renderer(config: SkinConfig) -> RenderStrategy:
    """Factory: return the correct RenderStrategy for *config*.

    - ``"oracle"`` type → OracleRenderer
    - ``"avatar"`` type → AvatarRenderer
    """
    if config.type == "oracle":
        return OracleRenderer(
            border=config.rendering.border,
            shadow=config.rendering.shadow,
            glow_on_hold=config.rendering.glow_on_hold,
        )
    return AvatarRenderer()
