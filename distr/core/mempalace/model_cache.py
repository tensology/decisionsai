"""MemPalace's MiniLM embedder: readiness check + background, verified fetch.

Decisions' palace embeds with chromadb's ``ONNXMiniLM_L6_V2``
(``all-MiniLM-L6-v2``, 384-dim; MemPalace ``embedding_model="minilm"``, its
hard default). chromadb keeps the model under
``~/.cache/chroma/onnx_models/all-MiniLM-L6-v2/onnx`` and, when it is missing,
downloads a ~79 MB ``onnx.tar.gz`` *inline* on the first embed call. In the
voice agent that froze startup (12 Nov 2026: pipeline never ready, push-to-talk
captured nothing, Haley's spinner never cleared).

Default here: Decisions never lets an embed call download the model inline
(see ``adapter.embedding_model_ready``). Instead a missing model triggers one
background fetch per machine that:

- downloads into a private temp dir (never chromadb's own ``onnx.tar.gz``, so
  it cannot truncate a download another process is doing),
- checks the SHA-256 chromadb pins,
- and only then moves a complete ``onnx/`` into place (or fills in the missing
  files of an incomplete one). Nothing existing is deleted.

Off switch: ``DECISIONS_MEMPALACE_MODEL_PREFETCH=0``. A cross-process lock
(``.decisions-fetch.lock`` holding the owner PID) keeps it to one download.
"""

from __future__ import annotations

import hashlib
import logging
import os
import shutil
import tarfile
import tempfile
import threading
import time
import urllib.request
from pathlib import Path

logger = logging.getLogger(__name__)

MODEL_NAME = "all-MiniLM-L6-v2"
# Same values as chromadb.utils.embedding_functions.ONNXMiniLM_L6_V2 (chromadb 1.5.x).
DOWNLOAD_URL = "https://chroma-onnx-models.s3.amazonaws.com/all-MiniLM-L6-v2/onnx.tar.gz"
SHA256 = "913d7300ceae3b2dbc2c50d1de4baacab4be7b9380491c27fab7418616a16ec3"
# chromadb re-downloads unless all six exist, so readiness checks all six.
REQUIRED_FILES = (
    "config.json",
    "model.onnx",
    "special_tokens_map.json",
    "tokenizer_config.json",
    "tokenizer.json",
    "vocab.txt",
)
ENV_PREFETCH = "DECISIONS_MEMPALACE_MODEL_PREFETCH"
LOCK_NAME = ".decisions-fetch.lock"
TMP_PREFIX = ".decisions-fetch-"
LOCK_MAX_AGE_S = 2 * 60 * 60
SOCKET_TIMEOUT_S = 60.0

_THREAD: threading.Thread | None = None
_THREAD_LOCK = threading.Lock()
_LAST: dict = {}


def model_dir() -> Path:
    return Path(os.path.expanduser("~")) / ".cache" / "chroma" / "onnx_models" / MODEL_NAME


def onnx_dir() -> Path:
    return model_dir() / "onnx"


def is_cached(directory: str | os.PathLike | None = None) -> bool:
    d = Path(directory) if directory is not None else onnx_dir()
    return all((d / name).is_file() for name in REQUIRED_FILES)


def prefetch_enabled() -> bool:
    raw = (os.environ.get(ENV_PREFETCH) or "1").strip().lower()
    return raw not in {"0", "false", "no", "off", "disabled"}


def last_status() -> dict:
    return dict(_LAST)


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _acquire_lock(parent: Path) -> Path | None:
    lock = parent / LOCK_NAME
    for _ in range(2):
        try:
            fd = os.open(str(lock), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            try:
                pid = int((lock.read_text(encoding="utf-8").strip() or "0"))
                age = time.time() - lock.stat().st_mtime
            except (OSError, ValueError):
                pid, age = 0, LOCK_MAX_AGE_S + 1
            if _pid_alive(pid) and age < LOCK_MAX_AGE_S:
                return None
            try:  # stale: our own lock file from a dead process
                lock.unlink()
            except OSError:
                return None
            continue
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(str(os.getpid()))
        return lock
    return None


def _sweep_stale_tmp(parent: Path) -> None:
    """Remove our own temp dirs left by a killed process (never chromadb's files)."""
    try:
        for entry in parent.iterdir():
            if entry.is_dir() and entry.name.startswith(TMP_PREFIX):
                if time.time() - entry.stat().st_mtime > LOCK_MAX_AGE_S:
                    shutil.rmtree(entry, ignore_errors=True)
    except OSError:
        pass


def _safe_extract(archive: Path, dest: Path) -> None:
    with tarfile.open(archive, "r:gz") as tar:
        try:
            tar.extractall(dest, filter="data")
        except TypeError:  # Python without tarfile filters
            root = dest.resolve()
            for member in tar.getmembers():
                target = (dest / member.name).resolve()
                if root not in target.parents and target != root:
                    raise ValueError(f"unsafe path in model archive: {member.name}")
                if not (member.isfile() or member.isdir()):
                    raise ValueError(f"unexpected member in model archive: {member.name}")
            tar.extractall(dest)


def _download(url: str, dest: Path) -> str:
    h = hashlib.sha256()
    req = urllib.request.Request(url, headers={"User-Agent": "DecisionsAI-mempalace-prefetch"})
    with urllib.request.urlopen(req, timeout=SOCKET_TIMEOUT_S) as resp, open(dest, "wb") as out:
        while True:
            chunk = resp.read(1 << 16)
            if not chunk:
                break
            h.update(chunk)
            out.write(chunk)
    return h.hexdigest()


def fetch_sync(*, url: str = DOWNLOAD_URL, sha256: str = SHA256) -> str:
    """Fetch, verify and install the model. Returns a short status string."""
    if is_cached():
        return "cached"
    parent = model_dir()
    parent.mkdir(parents=True, exist_ok=True)
    lock = _acquire_lock(parent)
    if lock is None:
        return "locked"
    tmp: Path | None = None
    try:
        _sweep_stale_tmp(parent)
        tmp = Path(tempfile.mkdtemp(prefix=TMP_PREFIX, dir=str(parent)))
        archive = tmp / "onnx.tar.gz"
        got = _download(url, archive)
        if got != sha256:
            logger.warning("MemPalace model fetch: sha256 mismatch (%s); not installed", got[:12])
            return "sha_mismatch"
        stage = tmp / "x"
        stage.mkdir()
        _safe_extract(archive, stage)
        staged = stage / "onnx"
        if not is_cached(staged):
            logger.warning("MemPalace model fetch: archive is missing model files; not installed")
            return "incomplete_archive"
        if is_cached():
            return "cached"
        final = onnx_dir()
        if not final.exists():
            os.rename(staged, final)
        else:
            # Partial dir from another writer: add only what is missing, delete nothing.
            for name in REQUIRED_FILES:
                if not (final / name).is_file():
                    os.replace(staged / name, final / name)
        logger.info("MemPalace embedding model installed at %s", final)
        return "installed" if is_cached() else "incomplete_install"
    except Exception as exc:  # network, disk, archive: never raise into callers
        logger.warning("MemPalace model fetch failed: %s", exc)
        return f"error: {type(exc).__name__}"
    finally:
        if tmp is not None:
            shutil.rmtree(tmp, ignore_errors=True)
        try:
            lock.unlink()
        except OSError:
            pass


def request_background_fetch(reason: str = "") -> str:
    """Start the fetch on a daemon thread (at most one per process). Never blocks."""
    global _THREAD
    if is_cached():
        return "cached"
    if not prefetch_enabled():
        return "disabled"
    with _THREAD_LOCK:
        if _THREAD is not None and _THREAD.is_alive():
            return "running"

        def _worker() -> None:
            _LAST.update({"state": "running", "reason": reason, "started": time.time()})
            result = fetch_sync()
            _LAST.update({"state": result, "finished": time.time()})
            logger.info("MemPalace background model fetch: %s", result)

        _THREAD = threading.Thread(target=_worker, daemon=True, name="mempalace-model-fetch")
        _THREAD.start()
    logger.info("MemPalace embedding model missing; background fetch started (%s)", reason or "-")
    return "started"
