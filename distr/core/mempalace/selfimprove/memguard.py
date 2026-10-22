"""Memory limits for the frustration-capture scan.

The capture job must never threaten the machine. On 2026-10-22 an earlier capture
version sent 300 transcript turns to Laya in one MPS forward pass. Its worker reached
a 20 GB footprint, mostly wired GPU memory, and the Mac was jetsammed and then panicked.
This module enforces a per-run memory budget:

- ``DECISIONS_CAPTURE_MAX_RSS_MB`` (default 3072; warn ~2458 MB, stop ~2765 MB): budget for the capture
  process plus its Laya worker together.
- ``DECISIONS_CAPTURE_WARN_PCT`` (default 80): one macOS notification when crossed.
- ``DECISIONS_CAPTURE_STOP_PCT`` (default 90): stop gracefully, keep the checkpoint,
  exit 0 so the next scheduled run resumes. A second notification says so.
- ``DECISIONS_CAPTURE_NOTIFY=0``: no notifications at all.

Memory is read as the macOS *physical footprint* (``proc_pid_rusage``), the number jetsam uses.
It includes Metal/MPS allocations and leaves out shared dyld-cache pages. ``ps`` RSS gets both wrong:
it misses Metal memory, and on macOS 26 it reports ~2 GB for an idle Python process. Other platforms use RSS from
``ps``. ``setrlimit`` is applied best-effort only, because macOS does not enforce
RLIMIT_AS/RLIMIT_DATA for mmap-backed allocations. The watchdog is the real guard.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import os
import resource
import subprocess
import sys
import threading
from typing import Callable

TITLE = "Decisions frustration capture"


def _env_num(name: str, default: float, lo: float, hi: float) -> float:
    try:
        v = float(os.environ.get(name, "") or default)
    except ValueError:
        v = default
    return max(lo, min(hi, v))


class Limits:
    def __init__(self) -> None:
        self.max_rss_mb = _env_num("DECISIONS_CAPTURE_MAX_RSS_MB", 3072, 64, 65536)
        self.warn_pct = _env_num("DECISIONS_CAPTURE_WARN_PCT", 80, 10, 99)
        self.stop_pct = max(self.warn_pct, _env_num("DECISIONS_CAPTURE_STOP_PCT", 90, 10, 99))
        self.batch_size = int(_env_num("DECISIONS_CAPTURE_BATCH_SIZE", 25, 1, 200))
        self.max_turns = int(_env_num("DECISIONS_CAPTURE_MAX_TURNS", 500, 1, 100000))
        self.max_msg_chars = int(_env_num("DECISIONS_CAPTURE_MAX_MSG_CHARS", 8000, 200, 1_000_000))
        self.max_line_bytes = int(_env_num("DECISIONS_CAPTURE_MAX_LINE_BYTES", 2_000_000, 10_000, 100_000_000))
        self.max_bytes_per_run = int(_env_num("DECISIONS_CAPTURE_MAX_BYTES_PER_RUN", 256 * 2**20, 2**20, 2**40))
        self.check_every_lines = int(_env_num("DECISIONS_CAPTURE_CHECK_EVERY_LINES", 500, 1, 10**6))
        # Laya (measured 2026-10-22, M3 Max, CPU): ~1.9 GB once loaded. One forward pass over 25 states of
        # 1500 chars peaks at ~7 GB; inner batch 4 at 600 chars peaks at ~2.26 GB. So Laya gets small
        # inner batches on CPU, and it only runs when the budget leaves room for it. Otherwise regex-only.
        self.laya_chars = int(_env_num("DECISIONS_CAPTURE_LAYA_CHARS", 600, 100, 8000))
        self.laya_batch = int(_env_num("DECISIONS_CAPTURE_LAYA_BATCH", 4, 1, 64))
        self.laya_min_mb = _env_num("DECISIONS_CAPTURE_LAYA_MIN_MB", 2500, 0, 65536)
        self.laya_device = (os.environ.get("DECISIONS_CAPTURE_LAYA_DEVICE") or "cpu").strip()

    @property
    def warn_mb(self) -> float:
        return self.max_rss_mb * self.warn_pct / 100.0

    @property
    def stop_mb(self) -> float:
        return self.max_rss_mb * self.stop_pct / 100.0

    def as_dict(self) -> dict:
        return {k: getattr(self, k) for k in ("max_rss_mb", "warn_pct", "stop_pct", "batch_size", "max_turns",
                                               "max_msg_chars", "max_line_bytes", "max_bytes_per_run",
                                               "laya_chars", "laya_batch", "laya_min_mb", "laya_device")}

    @property
    def laya_fits(self) -> bool:
        """Laya is only started when the stop threshold leaves room for its measured peak."""
        return self.stop_mb >= self.laya_min_mb


# ── measuring ─────────────────────────────────────────────────────────────────

class _RusageV2(ctypes.Structure):
    _fields_ = [("uuid", ctypes.c_uint8 * 16)] + [(n, ctypes.c_uint64) for n in (
        "user_time", "system_time", "pkg_idle_wkups", "interrupt_wkups", "pageins", "wired_size",
        "resident_size", "phys_footprint", "proc_start_abstime", "proc_exit_abstime", "child_user_time",
        "child_system_time", "child_pkg_idle_wkups", "child_interrupt_wkups", "child_pageins",
        "child_elapsed_abstime", "diskio_bytesread", "diskio_byteswritten")]


_libproc = None
if sys.platform == "darwin":
    try:
        _libproc = ctypes.CDLL(ctypes.util.find_library("proc") or "/usr/lib/libproc.dylib")
    except OSError:
        _libproc = None


def footprint_mb(pid: int | None = None) -> float:
    """Current memory of ``pid`` in MB (physical footprint on macOS, else RSS). 0 if unknown/gone."""
    pid = pid or os.getpid()
    if _libproc is not None:
        buf = _RusageV2()
        if _libproc.proc_pid_rusage(ctypes.c_int(pid), ctypes.c_int(2), ctypes.byref(buf)) == 0:
            # phys_footprint is what jetsam acts on and includes Metal/MPS memory. resident_size does
            # not include that, and it also counts shared dyld-cache pages (~2 GB on macOS 26).
            return (buf.phys_footprint or buf.resident_size) / 2**20
    try:
        out = subprocess.run(["ps", "-o", "rss=", "-p", str(pid)], capture_output=True, text=True, timeout=5).stdout
        return float(out.strip() or 0) / 1024.0
    except Exception:
        return 0.0


def apply_rlimits(max_mb: float) -> dict[str, str]:
    """Best-effort soft limits for this process (not the Metal worker). Returns what happened."""
    out: dict[str, str] = {}
    limit = int(max_mb * 2**20)
    names = ("RLIMIT_DATA", "RLIMIT_AS") if sys.platform == "darwin" else ("RLIMIT_DATA",)
    for name in names:
        res = getattr(resource, name, None)
        if res is None:
            continue
        try:
            soft, hard = resource.getrlimit(res)
            if hard != resource.RLIM_INFINITY and hard < limit:
                limit = hard
            resource.setrlimit(res, (limit, hard))
            out[name] = "set (advisory on macOS)" if sys.platform == "darwin" else "set"
        except (ValueError, OSError) as exc:
            out[name] = f"not set: {type(exc).__name__}"
    return out


# ── notifying ─────────────────────────────────────────────────────────────────

def _gui_session() -> bool:
    try:
        r = subprocess.run(["launchctl", "managername"], capture_output=True, text=True, timeout=3)
        return r.stdout.strip() == "Aqua"
    except Exception:
        return False


def notify(message: str) -> bool:
    """One macOS notification; never raises. False when disabled/unsupported/failed."""
    if (os.environ.get("DECISIONS_CAPTURE_NOTIFY") or "1").strip().lower() in {"0", "false", "no", "off"}:
        return False
    if sys.platform != "darwin" or not _gui_session():
        return False
    safe = message.replace("\\", "\\\\").replace('"', '\\"')
    try:
        r = subprocess.run(["osascript", "-e", f'display notification "{safe}" with title "{TITLE}"'],
                           capture_output=True, text=True, timeout=5)
        return r.returncode == 0
    except Exception:
        return False


# ── guard ─────────────────────────────────────────────────────────────────────

class MemoryGuard:
    """Tracks capture + worker memory; warns once at warn_pct, stops at stop_pct."""

    def __init__(self, limits: Limits, *, measure: Callable[[int | None], float] = footprint_mb,
                 notifier: Callable[[str], bool] = notify, log: Callable[[str], None] | None = None) -> None:
        self.limits = limits
        self.measure = measure
        self.notifier = notifier
        self.log = log or (lambda m: print(m, file=sys.stderr, flush=True))
        self.worker_pid: int | None = None
        self.peak_mb = 0.0
        self.warned = False
        self.stopped = False
        self.notified: list[str] = []
        self.checks = 0

    def current_mb(self) -> float:
        total = self.measure(None)
        if self.worker_pid:
            total += self.measure(self.worker_pid)
        self.peak_mb = max(self.peak_mb, total)
        return total

    def check(self, where: str = "") -> str:
        """Return 'ok', 'warn' (first crossing only) or 'stop'."""
        self.checks += 1
        mb = self.current_mb()
        if self.stopped:
            return "stop"  # sticky: once over the line, the run winds down
        if mb >= self.limits.stop_mb:
            if not self.stopped:
                self.stopped = True
                msg = (f"Frustration-capture scan stopped at ~{mb / 1024:.1f} GB of the "
                       f"{self.limits.max_rss_mb / 1024:.1f} GB cap; progress saved, next run resumes")
                self.log(f"[capture] memory cap reached ({mb:.0f} MB >= {self.limits.stop_mb:.0f} MB) at {where}: "
                         "stopping gracefully, checkpoint saved")
                if self.notifier(msg):
                    self.notified.append("stop")
            return "stop"
        if mb >= self.limits.warn_mb and not self.warned:
            self.warned = True
            msg = (f"Frustration-capture scan using ~{mb / 1024:.1f} GB of "
                   f"{self.limits.max_rss_mb / 1024:.1f} GB cap; pausing soon")
            self.log(f"[capture] memory warning: {mb:.0f} MB >= {self.limits.warn_mb:.0f} MB at {where}")
            if self.notifier(msg):
                self.notified.append("warn")
            return "warn"
        return "ok"


class WorkerWatchdog:
    """While Laya loads or classifies, poll the guard. Warn/stop notifications fire from here too;
    at the stop threshold (or the hard cap) the worker is killed and the run winds down."""

    def __init__(self, guard: MemoryGuard, kill: Callable[[], None], interval: float = 0.25) -> None:
        self.guard, self.kill, self.interval = guard, kill, interval
        self.tripped = False
        self._stop = threading.Event()
        self._t = threading.Thread(target=self._run, daemon=True)

    def _run(self) -> None:
        while not self._stop.wait(self.interval):
            if self.guard.check("laya worker") == "stop":
                self.tripped = True
                self.kill()
                return

    def __enter__(self) -> "WorkerWatchdog":
        self._t.start()
        return self

    def __exit__(self, *exc) -> None:
        self._stop.set()
        self._t.join(timeout=2)
