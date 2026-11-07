"""Explicit, cancellable post-install speech models; no checkout or silent fetch."""
from __future__ import annotations

import hashlib
import json
import os
import queue
import shutil
import tempfile
import threading
import urllib.request
import zipfile
from pathlib import Path
from urllib.parse import urlparse

MANIFEST = "https://decisions.tensology.com/downloads/models/manifest.json"


def _root():
    from distr.core.paths import user_data_root
    return user_data_root() / "models" / "speech"


def selected_model():
    try:
        data = json.loads((_root() / "selection.json").read_text())
        path = (_root() / data["relative_path"]).resolve()
        if not path.is_relative_to(_root().resolve()) or data["engine"] not in ("whisper", "vosk") or not path.exists():
            return None
        return {"engine": data["engine"], "path": str(path)}
    except (OSError, ValueError, KeyError, TypeError):
        return None


def download_model(engine, progress, cancelled):
    """Download from the site's allowlisted mirror; checksum before activation."""
    root = _root()
    root.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(MANIFEST, timeout=15) as response:
        meta = json.loads(response.read(65536))[engine]
    url = urlparse(meta["url"])
    if url.scheme != "https" or url.netloc != "decisions.tensology.com":
        raise ValueError("Untrusted model mirror URL")
    total = int(meta["bytes"])
    if total <= 0 or total > 2_000_000_000:
        raise ValueError("Invalid model download size")
    with tempfile.TemporaryDirectory(prefix="download-", dir=root) as temporary:
        archive = Path(temporary) / "payload"
        sha = hashlib.sha256()
        received = 0
        with urllib.request.urlopen(meta["url"], timeout=30) as response, archive.open("wb") as output:
            while True:
                if cancelled.is_set():
                    raise InterruptedError("Download cancelled; no model activated")
                block = response.read(256 * 1024)
                if not block:
                    break
                received += len(block)
                if received > total:
                    raise ValueError("Download exceeds declared size")
                output.write(block)
                sha.update(block)
                progress(received, total)
        if received != total or sha.hexdigest() != meta["sha256"]:
            raise ValueError("Model checksum or size mismatch; no model activated")
        if cancelled.is_set():
            raise InterruptedError("Download cancelled; no model activated")
        if engine in ("vosk", "optional_media", "kokoro"):
            extracted = Path(temporary) / "extracted"
            with zipfile.ZipFile(archive) as stream:
                members = stream.infolist()
                if sum(m.file_size for m in members) > 1_000_000_000:
                    raise ValueError("Model archive expands beyond limit")
                for member in members:
                    dest = (extracted / member.filename).resolve()
                    if not dest.is_relative_to(extracted.resolve()):
                        raise ValueError("Unsafe model archive")
                stream.extractall(extracted)
            if engine == "kokoro":
                from distr.core.paths import user_data_root
                if not (extracted / "kokoro-v1.0.onnx").is_file() or not (extracted / "voices-v1.0.bin").is_file():
                    raise ValueError("Incomplete local voice payload")
                shutil.copytree(extracted, user_data_root() / "models", dirs_exist_ok=True)
                return str(user_data_root() / "models")
            if engine == "optional_media":
                from distr.core.paths import user_data_root
                shutil.copytree(extracted, user_data_root() / "assets", dirs_exist_ok=True)
                return str(user_data_root() / "assets")
            source = extracted / "vosk-model-small-en-us-0.15"
            if not (source / "am" / "final.mdl").is_file():
                raise ValueError("Incomplete Vosk model archive")
            target = root / "vosk-model-small-en-us-0.15"
            if not target.exists():
                shutil.move(str(source), str(target))
        else:
            target = root / "ggml-base.en.bin"
            os.replace(archive, target)
        selection = {"engine": engine, "relative_path": target.name}
        pending = root / "selection.pending"
        pending.write_text(json.dumps(selection))
        os.replace(pending, root / "selection.json")
        return str(target)


def choose_stt(*, force=False, media_only=False, voice_only=False):
    """First-run dialog and later menu action. Downloads begin only on a click."""
    if selected_model() and not force and not media_only and not voice_only:
        return
    from PyQt6.QtCore import QTimer
    from PyQt6.QtWidgets import QDialog, QVBoxLayout, QLabel, QComboBox, QPushButton, QProgressBar
    dialog = QDialog()
    dialog.setWindowTitle("DecisionsAI — choose speech recognition")
    dialog.resize(520, 250)
    layout = QVBoxLayout(dialog)
    label = QLabel("No speech models are included. Choose an engine and click Download.\nVosk: lightweight English streaming (~40 MB).\nWhisper base.en: larger English model (~148 MB).\nDownloads stay in your Application Support folder, not the app.")
    label.setWordWrap(True)
    layout.addWidget(label)
    engine = QComboBox()
    engine.addItem("Vosk — smaller, faster CPU streaming", "vosk")
    engine.addItem("Whisper base.en — larger, more robust transcription", "whisper")
    if media_only:
        engine.clear()
        engine.addItem("Optional avatars, intro and sounds (~119 MB)", "optional_media")
        label.setText("Optional visual media and sounds are not bundled. Click Download to install them into Application Support. The basic app works without this pack.")
        dialog.setWindowTitle("DecisionsAI — optional media")
    if voice_only:
        engine.clear()
        engine.addItem("Kokoro local voice (separate optional download)", "kokoro")
        label.setText("Local voice output requires Kokoro weights (~354 MB uncompressed). Click Download to install them in Application Support, or configure a cloud voice in Settings instead.")
        dialog.setWindowTitle("DecisionsAI — optional local voice")
    layout.addWidget(engine)
    bar = QProgressBar()
    layout.addWidget(bar)
    start = QPushButton("Download selected engine")
    later = QPushButton("Not now — continue without voice")
    layout.addWidget(start)
    layout.addWidget(later)
    events = queue.Queue()
    cancel = threading.Event()
    dialog.rejected.connect(cancel.set)
    later.clicked.connect(dialog.reject)
    timer = QTimer(dialog)
    def consume():
        while not events.empty():
            kind, value = events.get_nowait()
            if kind == "progress":
                done, total = value
                bar.setValue(int(done * 100 / total))
                label.setText(f"Downloading: {done / 1e6:.1f} / {total / 1e6:.1f} MB. Cancel leaves voice disabled.")
            elif kind == "done":
                timer.stop()
                dialog.accept()
            elif kind == "error":
                timer.stop()
                label.setText("Download failed: " + value + "\nYou can retry or continue without voice.")
                start.setEnabled(True)
                engine.setEnabled(True)
    timer.timeout.connect(consume)
    def begin():
        cancel.clear()
        choice = engine.currentData()
        start.setEnabled(False)
        engine.setEnabled(False)
        label.setText("Connecting to the model mirror…")
        def worker():
            try:
                download_model(choice, lambda done, total: events.put(("progress", (done, total))), cancel)
                events.put(("done", None))
            except Exception as exc:
                events.put(("error", str(exc)))
        threading.Thread(target=worker, daemon=True, name="speech-model-download").start()
        timer.start(100)
    start.clicked.connect(begin)
    # Installer smoke runs have an isolated HOME/data root and intentionally skip
    # optional downloads. Real first launch never chooses or downloads silently.
    if os.environ.get("DECISIONSAI_SMOKE_TEST") == "1" and os.environ.get("DECISIONS_DATA_DIR"):
        QTimer.singleShot(100, dialog.reject)
    dialog.exec()
    cancel.set()


def install_speech_menu(app):
    from PyQt6.QtGui import QAction
    from PyQt6.QtWidgets import QMenuBar
    menu = QMenuBar(None)
    speech = menu.addMenu("Speech engine")
    action = QAction("Choose / download Whisper or Vosk…", menu)
    def configure():
        choose_stt(force=True)
        if selected_model():
            app.start_agent_session(skip_welcome=True)
    action.triggered.connect(configure)
    speech.addAction(action)
    media = QAction("Download optional avatars and sounds…", menu)
    media.triggered.connect(lambda: choose_stt(force=True, media_only=True))
    speech.addAction(media)
    voice = QAction("Download optional Kokoro local voice…", menu)
    voice.triggered.connect(lambda: choose_stt(force=True, voice_only=True))
    speech.addAction(voice)
    app._speech_menu = menu
