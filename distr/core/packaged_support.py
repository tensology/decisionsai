"""Non-blocking diagnostics and opt-in download-page updates for DMG installs.

Never executes a downloaded payload or modifies the running application.
"""
from __future__ import annotations

import atexit
import json
import logging
import queue
import re
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

UPDATE_MANIFEST = "https://decisions.tensology.com/downloads/latest-macos.json"
DOWNLOAD_PAGE = "https://decisions.tensology.com/#install"
log = logging.getLogger(__name__)


def newer_release(current: str, manifest: dict) -> bool:
    """Only accept stable numeric releases and HTTPS downloads on our own site."""
    def version(value):
        if not isinstance(value, str) or not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", value):
            raise ValueError("Invalid stable version")
        return tuple(map(int, value.split(".")))
    url = urlparse(manifest.get("url", ""))
    if url.scheme != "https" or url.netloc != "decisions.tensology.com" or not url.path.endswith(".dmg"):
        raise ValueError("Untrusted update download URL")
    if manifest.get("architecture") != "arm64":
        raise ValueError("Unsupported update architecture")
    if not re.fullmatch(r"[a-f0-9]{64}", manifest.get("sha256", "")):
        raise ValueError("Missing update checksum")
    return version(manifest.get("version")) > version(current)


def _read_json(url):
    with urllib.request.urlopen(url, timeout=4) as response:
        return json.loads(response.read(65536))


def _diagnose(root: Path):
    messages = []
    # Own only the child started here. Never stop a pre-existing sidecar.
    try:
        _read_json("http://127.0.0.1:11435/health")
    except Exception:
        sidecar = root / "decisionsai-sidecar"
        if not sidecar.is_file():
            messages.append("Desktop sidecar is missing: accessibility-tree tools are unavailable. Download a complete app build.")
        else:
            from distr.core.paths import user_data_root
            logs = user_data_root() / "logs"
            logs.mkdir(parents=True, exist_ok=True)
            try:
                with (logs / "sidecar.log").open("ab") as output:
                    child = subprocess.Popen([str(sidecar), "--local"], stdout=output, stderr=subprocess.STDOUT)
                def stop_owned_child():
                    if child.poll() is None:
                        child.terminate()
                atexit.register(stop_owned_child)
                for _ in range(15):
                    try:
                        _read_json("http://127.0.0.1:11435/health")
                        break
                    except Exception:
                        if child.poll() is not None:
                            break
                        time.sleep(0.2)
                else:
                    messages.append("Desktop sidecar did not become ready. See Application Support/DecisionsAI/logs/sidecar.log.")
                if child.poll() is not None:
                    messages.append("Desktop sidecar exited. Desktop automation is unavailable; see sidecar.log.")
            except OSError:
                messages.append("Desktop sidecar could not start. See Application Support/DecisionsAI/logs/sidecar.log.")
    try:
        import Quartz
        if not Quartz.AXIsProcessTrusted():
            messages.append("Accessibility is disabled. Enable DecisionsAI in System Settings > Privacy & Security > Accessibility to control apps.")
        if not Quartz.CGPreflightScreenCaptureAccess():
            messages.append("Screen Recording is disabled. Enable DecisionsAI in System Settings > Privacy & Security > Screen Recording, then reopen the app when ready.")
    except Exception:
        messages.append("Could not verify macOS permissions. Check Accessibility and Screen Recording in System Settings > Privacy & Security.")
    try:
        from AVFoundation import AVCaptureDevice, AVMediaTypeAudio
        if AVCaptureDevice.authorizationStatusForMediaType_(AVMediaTypeAudio) != 3:
            messages.append("Microphone access is not granted. Enable DecisionsAI under Privacy & Security > Microphone for voice input.")
    except Exception:
        messages.append("Microphone permission could not be checked. Check Privacy & Security > Microphone before using voice.")
    try:
        models = _read_json("http://127.0.0.1:11434/api/tags").get("models", [])
        if not models:
            messages.append("Ollama has no local models. Install a model in Ollama or select a cloud provider and enter your own API key in Settings.")
    except Exception:
        messages.append("Ollama is not running. Local models are optional: install/start Ollama, or choose a cloud provider in Settings.")
    return messages


def schedule_packaged_support(app):
    """Run network probes off the Qt thread, retain non-modal notices on app."""
    from PyQt6.QtCore import QTimer, QUrl
    from PyQt6.QtGui import QDesktopServices
    from PyQt6.QtWidgets import QMessageBox
    from distr.core.packaged_stt import install_speech_menu
    install_speech_menu(app)
    results = queue.Queue()
    root = Path(sys._MEIPASS)
    def worker():
        messages = _diagnose(root)
        update = None
        try:
            current = (root / "VERSION").read_text().strip()
            manifest = _read_json(UPDATE_MANIFEST)
            if newer_release(current, manifest):
                update = manifest["version"]
        except Exception as exc:
            messages.append("Update check unavailable. Check https://decisions.tensology.com/ for new versions; your installed app still works.")
            log.warning("Packaged update check failed: %s", type(exc).__name__)
        results.put((messages, update))
    threading.Thread(target=worker, name="packaged-diagnostics", daemon=True).start()
    app._packaged_notices = []
    timer = QTimer(app)
    app._packaged_support_timer = timer
    def display():
        try:
            messages, update = results.get_nowait()
        except queue.Empty:
            return
        timer.stop()
        if messages:
            box = QMessageBox(QMessageBox.Icon.Warning, "DecisionsAI setup status", "\n\n".join(messages))
            box.setInformativeText("These components are optional. Configure them before using their features; the app can remain open.")
            box.setModal(False)
            app._packaged_notices.append(box)
            box.show()
        if update:
            box = QMessageBox(QMessageBox.Icon.Information, "DecisionsAI update available", f"DecisionsAI {update} is available.")
            box.setInformativeText("Download the new DMG, quit DecisionsAI when ready, drag the new app to Applications and choose Replace. Your data stays in Application Support. No Git or Python setup is needed.")
            box.setStandardButtons(QMessageBox.StandardButton.Open | QMessageBox.StandardButton.Cancel)
            box.button(QMessageBox.StandardButton.Open).setText("Open download page")
            box.finished.connect(lambda result: QDesktopServices.openUrl(QUrl(DOWNLOAD_PAGE)) if result == QMessageBox.StandardButton.Open else None)
            box.setModal(False)
            app._packaged_notices.append(box)
            box.show()
    timer.timeout.connect(display)
    timer.start(1000)
