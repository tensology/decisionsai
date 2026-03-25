"""WebMView — transparent QWebEngineView for playing WebM with VP9 alpha.

Uses the Chromium engine inside PyQt6 to play WebM files with native
alpha channel support. The window is fully transparent — only the
non-transparent pixels of the video are visible.
"""

from __future__ import annotations

import logging
import os

from PyQt6.QtCore import Qt, QUrl
from PyQt6.QtGui import QColor
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWebEngineCore import QWebEngineSettings
from PyQt6.QtWidgets import QWidget

logger = logging.getLogger(__name__)

_HTML_TEMPLATE = """<!DOCTYPE html>
<html><head><style>
* { margin:0; padding:0; }
html, body { width:100%; height:100%; overflow:hidden; background:transparent; }
video { width:100%; height:100%; object-fit:contain; background:transparent; }
</style></head><body>
<video id="v" autoplay muted playsinline loop></video>
<script>
var video = document.getElementById('v');
function loadVideo(src, pp) {
    video.src = src;
    video.loop = true;
    video.play().catch(function(){});
}
</script>
</body></html>"""


class WebMView(QWidget):
    """Transparent widget that plays WebM with VP9 alpha via QWebEngineView."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        self._web = QWebEngineView(self)
        self._web.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self._web.page().setBackgroundColor(QColor(0, 0, 0, 0))

        settings = self._web.settings()
        settings.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, True)
        settings.setAttribute(QWebEngineSettings.WebAttribute.PlaybackRequiresUserGesture, False)

        self._ready = False
        self._pending_load = None
        self._web.loadFinished.connect(self._on_load_finished)
        self._web.setHtml(_HTML_TEMPLATE, QUrl("about:blank"))

    def _on_load_finished(self, ok: bool) -> None:
        self._ready = True
        if self._pending_load:
            path, playback = self._pending_load
            self._pending_load = None
            self.load(path, playback)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._web.setGeometry(0, 0, self.width(), self.height())

    def load(self, webm_path: str, playback: str = "pingpong") -> None:
        if not os.path.exists(webm_path):
            return
        if not self._ready:
            self._pending_load = (webm_path, playback)
            return
        file_url = QUrl.fromLocalFile(os.path.abspath(webm_path)).toString()
        pp = "true" if playback == "pingpong" else "false"
        self._web.page().runJavaScript(f'loadVideo("{file_url}", {pp})')

    def stop(self) -> None:
        if self._ready:
            self._web.page().runJavaScript('video.pause(); video.src = "";')

    def set_size(self, width: int, height: int) -> None:
        self.setFixedSize(width, height)
