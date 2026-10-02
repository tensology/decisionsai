"""First-run seed corpus pack → MemPalace wing ``seed_corpus_pack``.

Bundled pack lives at ``distr/seed-corpus-pack`` (vendored copy). On first
install / first run when the memory backend is ON, mine into the
Decisions-owned palace (``~/.decisions/mempalace/palace``) with::

    mempalace init <pack> --yes --auto-mine --no-llm --palace <palace>

Offline once chromadb + mempalace are importable. Soft-fails with a clear
setup message when deps are missing. Never blocks the GUI thread — call
``ensure_seed_wing(background=True)`` (default) from app startup.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

from distr.core.mempalace.bootstrap import (
    ensure_mempalace_on_path,
    reference_mempalace_root,
    try_import_mempalace,
)
from distr.core.mempalace.mapping import WING_SEED_CORPUS_PACK
from distr.core.mempalace.paths import (
    bundled_seed_corpus_pack,
    default_palace_path,
    ensure_palace_dir,
    seed_mine_marker_path,
)

logger = logging.getLogger(__name__)

_MINE_LOCK = threading.Lock()
_MINE_THREAD: threading.Thread | None = None
_LAST_STATUS: dict[str, Any] = {"state": "idle"}


def _setup_message(reason: str) -> str:
    return (
        "MemPalace seed corpus not ready: "
        + reason
        + ". Install locally: pip install -e ../reference/mempalace "
        "(pulls free chromadb). Then restart Decisions — first-run will "
        "auto-mine distr/seed-corpus-pack into ~/.decisions/mempalace/palace "
        f"(wing {WING_SEED_CORPUS_PACK})."
    )


def read_seed_manifest(pack: Path | None = None) -> dict[str, Any]:
    root = pack or bundled_seed_corpus_pack()
    if root is None:
        return {}
    mf = root / "manifest.json"
    if not mf.is_file():
        return {}
    try:
        return json.loads(mf.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.debug("seed manifest read failed: %s", exc)
        return {}


def seed_already_mined(palace: Path | None = None) -> bool:
    """True when marker says this pack version was mined into this palace."""
    marker = seed_mine_marker_path()
    if not marker.is_file():
        return False
    try:
        data = json.loads(marker.read_text(encoding="utf-8"))
    except Exception:
        return False
    palace_path = str((palace or default_palace_path()).resolve())
    if data.get("palace_path") != palace_path:
        return False
    if data.get("wing") != WING_SEED_CORPUS_PACK:
        return False
    if data.get("state") != "done":
        return False
    manifest = read_seed_manifest()
    expected_ver = manifest.get("version")
    if expected_ver is not None and data.get("pack_version") != expected_ver:
        return False
    return True


def write_seed_marker(
    *,
    state: str,
    palace: Path,
    detail: dict[str, Any] | None = None,
) -> None:
    marker = seed_mine_marker_path()
    marker.parent.mkdir(parents=True, exist_ok=True)
    manifest = read_seed_manifest()
    payload: dict[str, Any] = {
        "state": state,
        "wing": WING_SEED_CORPUS_PACK,
        "palace_path": str(palace.resolve()),
        "pack_version": manifest.get("version"),
        "pack_id": manifest.get("id", "seed-corpus-pack"),
        "updated_at_local": datetime.now().isoformat(timespec="seconds"),
    }
    if detail:
        payload.update(detail)
    marker.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def last_seed_status() -> dict[str, Any]:
    out = dict(_LAST_STATUS)
    marker = seed_mine_marker_path()
    if marker.is_file():
        try:
            out["marker"] = json.loads(marker.read_text(encoding="utf-8"))
        except Exception:
            out["marker"] = {"error": "unreadable"}
    out["already_mined"] = seed_already_mined()
    out["bundled_pack"] = str(bundled_seed_corpus_pack() or "")
    return out


def _palace_looks_initialized(palace: Path) -> bool:
    """True when chroma sqlite or .mempalace meta already exists under palace."""
    if (palace / "chroma.sqlite3").is_file():
        return True
    if (palace / ".mempalace").is_dir():
        return True
    return False


def _run_mempalace_ingest(pack: Path, palace: Path, log_path: Path) -> int:
    """Invoke mempalace init (first time) or mine (resume) into Decisions palace.

    ``--palace`` is a *global* flag and must precede the subcommand. We also set
    ``MEMPALACE_PALACE_PATH`` so every cfg.palace_path read hits Decisions.
    """
    ensure_mempalace_on_path()
    env = os.environ.copy()
    env["MEMPALACE_PALACE_PATH"] = str(palace.resolve())
    env["PYTHONUNBUFFERED"] = "1"
    palace_s = str(palace.resolve())
    pack_s = str(pack)
    if _palace_looks_initialized(palace) and (pack / "mempalace.yaml").is_file():
        # Resume / re-mine: skips files already filed (mtime-aware).
        cmd = [
            sys.executable,
            "-m",
            "mempalace",
            "--palace",
            palace_s,
            "mine",
            pack_s,
        ]
        mode = "mine-resume"
    else:
        cmd = [
            sys.executable,
            "-m",
            "mempalace",
            "--palace",
            palace_s,
            "init",
            pack_s,
            "--yes",
            "--auto-mine",
            "--no-llm",
        ]
        mode = "init-auto-mine"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    # Chromadb/hnsw can crash mid-mine on large files (macOS ARM). Retry
    # mine resumes (skips already-filed files) until success or no progress.
    max_attempts = 25
    last_rc = 1
    cwd = str(reference_mempalace_root() or pack)
    for attempt in range(1, max_attempts + 1):
        # Prefer mine-resume after first attempt even if we started with init.
        if attempt > 1:
            cmd = [
                sys.executable,
                "-m",
                "mempalace",
                "--palace",
                palace_s,
                "mine",
                pack_s,
            ]
            mode = f"mine-resume-attempt-{attempt}"
        with log_path.open("a", encoding="utf-8") as logf:
            logf.write(f"\n# mode={mode} attempt={attempt}/{max_attempts}\n")
            logf.write(f"# cmd: {' '.join(cmd)}\n")
            logf.write(f"# started: {datetime.now().isoformat(timespec='seconds')}\n")
            logf.flush()
            before = _count_drawers(palace)
            proc = subprocess.Popen(
                cmd,
                stdout=logf,
                stderr=subprocess.STDOUT,
                env=env,
                cwd=cwd,
                start_new_session=True,  # survive child segfaults on macOS ARM/chroma
            )
            last_rc = int(proc.wait())
            after = _count_drawers(palace)
            logf.write(
                f"\n# finished attempt={attempt} rc={last_rc} "
                f"drawers_before={before} drawers_after={after} "
                f"at {datetime.now().isoformat(timespec='seconds')}\n"
            )
        if last_rc == 0:
            return 0
        if after <= before and attempt > 1:
            logger.warning(
                "seed mine no progress on attempt %s (drawers=%s) — stopping",
                attempt,
                after,
            )
            return last_rc or 1
        logger.info(
            "seed mine attempt %s ended rc=%s drawers %s→%s — retrying",
            attempt,
            last_rc,
            before,
            after,
        )
    return last_rc


def _count_drawers(palace: Path) -> int:
    """Best-effort drawer count for progress / no-progress detection."""
    try:
        from mempalace.palace import get_collection

        col = get_collection(str(palace), create=False)
        if col is None:
            return 0
        return int(col.count())
    except Exception:
        return 0


def mine_seed_wing_sync(*, force: bool = False) -> dict[str, Any]:
    """Mine bundled seed pack into Decisions palace (blocking). Soft-fails."""
    global _LAST_STATUS
    with _MINE_LOCK:
        pack = bundled_seed_corpus_pack()
        if pack is None:
            msg = _setup_message("bundled pack missing at distr/seed-corpus-pack")
            _LAST_STATUS = {"state": "error", "error": msg}
            logger.warning(msg)
            return _LAST_STATUS

        ok, err = try_import_mempalace()
        if not ok:
            msg = _setup_message(f"mempalace import failed ({err})")
            _LAST_STATUS = {"state": "error", "error": msg, "import_error": err}
            logger.warning(msg)
            return _LAST_STATUS
        try:
            import chromadb  # noqa: F401
        except Exception as exc:
            msg = _setup_message(f"chromadb missing ({exc})")
            _LAST_STATUS = {"state": "error", "error": msg}
            logger.warning(msg)
            return _LAST_STATUS

        palace = ensure_palace_dir()
        if seed_already_mined(palace) and not force:
            _LAST_STATUS = {
                "state": "done",
                "skipped": True,
                "reason": "already_mined",
                "palace_path": str(palace),
                "wing": WING_SEED_CORPUS_PACK,
            }
            return _LAST_STATUS

        log_path = palace.parent / "seed-corpus-mine.log"
        write_seed_marker(state="mining", palace=palace, detail={"log": str(log_path)})
        _LAST_STATUS = {
            "state": "mining",
            "palace_path": str(palace),
            "pack": str(pack),
            "log": str(log_path),
            "wing": WING_SEED_CORPUS_PACK,
        }
        logger.info(
            "MemPalace seed mine starting: pack=%s palace=%s wing=%s log=%s",
            pack,
            palace,
            WING_SEED_CORPUS_PACK,
            log_path,
        )
        try:
            rc = _run_mempalace_ingest(pack, palace, log_path)
        except Exception as exc:
            msg = f"seed mine subprocess failed: {exc}"
            write_seed_marker(
                state="error",
                palace=palace,
                detail={"error": msg, "log": str(log_path)},
            )
            _LAST_STATUS = {"state": "error", "error": msg, "log": str(log_path)}
            logger.warning(msg)
            return _LAST_STATUS

        if rc != 0:
            msg = f"seed mine exited rc={rc}; see {log_path}"
            write_seed_marker(
                state="error",
                palace=palace,
                detail={"error": msg, "rc": rc, "log": str(log_path)},
            )
            _LAST_STATUS = {
                "state": "error",
                "error": msg,
                "rc": rc,
                "log": str(log_path),
            }
            logger.warning(msg)
            return _LAST_STATUS

        manifest = read_seed_manifest(pack)
        write_seed_marker(
            state="done",
            palace=palace,
            detail={
                "rc": 0,
                "log": str(log_path),
                "expected_drawers": manifest.get("mempalace_drawers"),
            },
        )
        _LAST_STATUS = {
            "state": "done",
            "palace_path": str(palace),
            "pack": str(pack),
            "wing": WING_SEED_CORPUS_PACK,
            "log": str(log_path),
            "expected_drawers": manifest.get("mempalace_drawers"),
        }
        logger.info(
            "MemPalace seed mine done: wing=%s palace=%s",
            WING_SEED_CORPUS_PACK,
            palace,
        )
        return _LAST_STATUS


def ensure_seed_wing(*, background: bool = True, force: bool = False) -> dict[str, Any]:
    """Ensure seed wing is mined. Default: daemon thread (non-blocking)."""
    global _MINE_THREAD, _LAST_STATUS
    if seed_already_mined() and not force:
        _LAST_STATUS = {
            "state": "done",
            "skipped": True,
            "reason": "already_mined",
            "wing": WING_SEED_CORPUS_PACK,
        }
        return last_seed_status()

    if not background:
        return mine_seed_wing_sync(force=force)

    with _MINE_LOCK:
        if _MINE_THREAD is not None and _MINE_THREAD.is_alive():
            _LAST_STATUS = {
                "state": "mining",
                "background": True,
                "wing": WING_SEED_CORPUS_PACK,
            }
            return last_seed_status()

        def _worker() -> None:
            try:
                mine_seed_wing_sync(force=force)
            except Exception:
                logger.exception("background seed mine crashed")

        _MINE_THREAD = threading.Thread(
            target=_worker,
            daemon=True,
            name="mempalace-seed-mine",
        )
        _LAST_STATUS = {
            "state": "mining",
            "background": True,
            "wing": WING_SEED_CORPUS_PACK,
        }
        _MINE_THREAD.start()
        return last_seed_status()
