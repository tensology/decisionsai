"""Memory-cap, batching and checkpoint tests for frustration capture (no Laya, no hub, temp HOME)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest


@pytest.fixture()
def home(tmp_path, monkeypatch):
    h = tmp_path / "home"
    h.mkdir()
    monkeypatch.setenv("HOME", str(h))
    monkeypatch.setenv("DECISIONS_FRUSTRATION_LAYA", "0")  # regex only
    monkeypatch.setenv("DECISIONS_CAPTURE_NOTIFY", "0")
    monkeypatch.delenv("DECISIONS_FRUSTRATION_CAPTURE", raising=False)
    for k in ("DECISIONS_CAPTURE_MAX_RSS_MB", "DECISIONS_CAPTURE_BATCH_SIZE", "DECISIONS_CAPTURE_MAX_TURNS",
              "DECISIONS_CAPTURE_ONLY", "DECISIONS_CAPTURE_WARN_PCT", "DECISIONS_CAPTURE_STOP_PCT"):
        monkeypatch.delenv(k, raising=False)
    from distr.core.mempalace import hub
    from distr.core.mempalace.selfimprove import capture

    monkeypatch.setattr(capture, "apply_rlimits", lambda mb: {})  # don't limit the pytest process
    monkeypatch.setattr(hub, "discover", lambda palace: None)
    monkeypatch.setattr(hub, "journal_append", lambda *a, **k: None)
    return h


def _pi_session(h: Path, name: str, n_users: int, *, huge_line: bool = False) -> Path:
    d = h / ".pi" / "agent" / "sessions" / "proj"
    d.mkdir(parents=True, exist_ok=True)
    f = d / f"{name}.jsonl"
    lines = [{"type": "session", "cwd": "/x/proj", "id": name}]
    for i in range(n_users):
        text = f"No, that's wrong again, I told you to fix item {i}" if i % 2 else f"please do task {i}"
        lines.append({"type": "message", "message": {"role": "user", "content": [{"type": "text", "text": text}]}})
        lines.append({"type": "message", "message": {"role": "assistant", "content": [{"type": "text", "text": "ok"}]}})
        if huge_line and i == 3:
            lines.append({"type": "message", "message": {"role": "toolResult", "content": [
                {"type": "text", "text": "A" * 3_000_000}], "isError": False}})
    f.write_text("\n".join(json.dumps(x) for x in lines) + "\n", encoding="utf-8")
    return f


def test_limits_defaults(monkeypatch):
    from distr.core.mempalace.selfimprove.memguard import Limits

    for k in ("DECISIONS_CAPTURE_MAX_RSS_MB", "DECISIONS_CAPTURE_BATCH_SIZE", "DECISIONS_CAPTURE_MAX_TURNS",
              "DECISIONS_CAPTURE_WARN_PCT", "DECISIONS_CAPTURE_STOP_PCT"):
        monkeypatch.delenv(k, raising=False)
    lim = Limits()
    assert (lim.max_rss_mb, lim.warn_pct, lim.stop_pct, lim.batch_size, lim.max_turns, lim.max_msg_chars) == \
        (3072, 80, 90, 25, 500, 8000)
    assert round(lim.warn_mb) == 2458 and round(lim.stop_mb) == 2765
    monkeypatch.setenv("DECISIONS_CAPTURE_MAX_RSS_MB", "150")
    assert Limits().stop_mb == 135


def test_guard_warns_once_then_stops_once():
    from distr.core.mempalace.selfimprove.memguard import Limits, MemoryGuard

    readings = iter([100, 2500, 2550, 2600, 2800, 2900])  # MB, cap 3072: warn 2458, stop 2765
    sent: list[str] = []
    g = MemoryGuard(Limits(), measure=lambda pid: next(readings), notifier=lambda m: sent.append(m) or True,
                    log=lambda m: None)
    assert [g.check() for _ in range(6)] == ["ok", "warn", "ok", "ok", "stop", "stop"]
    assert len(sent) == 2 and "pausing soon" in sent[0] and "next run resumes" in sent[1]
    assert g.notified == ["warn", "stop"] and g.peak_mb == 2900


def test_notifier_called_once_for_warning():
    from distr.core.mempalace.selfimprove.memguard import Limits, MemoryGuard

    calls: list[str] = []
    g = MemoryGuard(Limits(), measure=lambda pid: 2500, notifier=lambda m: calls.append(m) or True,
                    log=lambda m: None)
    for _ in range(20):
        g.check()
    assert len(calls) == 1 and "2.4 GB of 3.0 GB cap" in calls[0]


def test_notify_respects_off_switch(monkeypatch):
    from distr.core.mempalace.selfimprove import memguard

    monkeypatch.setenv("DECISIONS_CAPTURE_NOTIFY", "0")
    monkeypatch.setattr(memguard.subprocess, "run", lambda *a, **k: pytest.fail("must not call osascript"))
    assert memguard.notify("x") is False


def test_footprint_reads_this_process():
    from distr.core.mempalace.selfimprove.memguard import footprint_mb

    assert 5 < footprint_mb() < 4096


def test_iter_new_streams_and_skips_huge_lines(home):
    from distr.core.mempalace.selfimprove import sources

    f = _pi_session(home, "s1", 6, huge_line=True)
    meta: dict = {}
    out = list(sources.iter_new("pi", str(f), 0, meta, max_line_bytes=1_000_000))
    assert out[-1][1] == f.stat().st_size
    skipped = [x for x in out if not x[0] and x[2] > 1_000_000]
    assert len(skipped) == 1  # the 3 MB tool output was never parsed
    assert sum(1 for ts, _, _ in out for t in ts if t["role"] == "user") == 6


def test_batches_and_checkpoint_resume(home, monkeypatch):
    from distr.core.mempalace.selfimprove import capture, config

    monkeypatch.setenv("DECISIONS_CAPTURE_BATCH_SIZE", "5")
    monkeypatch.setenv("DECISIONS_CAPTURE_MAX_TURNS", "12")
    f = _pi_session(home, "s1", 30)
    r1 = capture.run(dry_run=True)
    assert r1["stopped_early"].startswith("per-run turn cap") and r1["user_turns"] == 12
    assert r1["batches"] >= 2
    st = json.loads(config.state_file().with_name("state.dryrun.json").read_text())
    off1 = st["files"][str(f)]["offset"]
    assert 0 < off1 < f.stat().st_size
    assert not config.state_file().exists()  # dry run never moves the real cursor
    r2 = capture.run(dry_run=True)  # resumes from the checkpoint
    assert r2["user_turns"] == 12
    st2 = json.loads(config.state_file().with_name("state.dryrun.json").read_text())
    assert st2["files"][str(f)]["offset"] > off1


def test_memory_cap_stops_gracefully_with_checkpoint(home, monkeypatch):
    from distr.core.mempalace.selfimprove import capture, config, memguard

    monkeypatch.setenv("DECISIONS_CAPTURE_BATCH_SIZE", "4")
    readings = iter([10.0] * 2 + [5000.0] * 1000)
    monkeypatch.setattr(memguard, "footprint_mb", lambda pid=None: next(readings))
    notes: list[str] = []
    monkeypatch.setattr(memguard, "notify", lambda m: notes.append(m) or True)
    # MemoryGuard binds its defaults at definition time; patch the class defaults used by capture
    orig = memguard.MemoryGuard.__init__

    def init(self, limits, **kw):
        orig(self, limits, measure=memguard.footprint_mb, notifier=memguard.notify, log=lambda m: None)

    monkeypatch.setattr(memguard.MemoryGuard, "__init__", init)
    f = _pi_session(home, "s1", 40)
    r = capture.run(dry_run=True)
    assert r["stopped_early"] == "memory cap"
    assert r["batches"] >= 1 and r["user_turns"] < 40
    assert any("next run resumes" in n for n in notes)
    st = json.loads(config.state_file().with_name("state.dryrun.json").read_text())
    assert 0 < st["files"][str(f)]["offset"] < f.stat().st_size
    assert not st.get("first_run_done")


def test_huge_user_message_is_skipped(home, monkeypatch):
    from distr.core.mempalace.selfimprove import capture

    d = home / ".pi" / "agent" / "sessions" / "proj"
    d.mkdir(parents=True)
    big = "No, wrong again! " + "x" * 9000
    lines = [{"type": "session", "cwd": "/x/proj", "id": "s"},
             {"type": "message", "message": {"role": "user", "content": [{"type": "text", "text": big}]}}]
    (d / "s.jsonl").write_text("\n".join(json.dumps(x) for x in lines) + "\n")
    r = capture.run(dry_run=True)
    assert r["skipped_huge_msgs"] == 1 and r["frustrations"] == 0


def test_laya_only_when_budget_fits(monkeypatch):
    from distr.core.mempalace.selfimprove.memguard import Limits

    monkeypatch.delenv("DECISIONS_CAPTURE_MAX_RSS_MB", raising=False)
    monkeypatch.delenv("DECISIONS_CAPTURE_LAYA_MIN_MB", raising=False)
    lim = Limits()
    assert lim.laya_fits  # 3072 MB default: Laya is NOT auto-skipped
    monkeypatch.setenv("DECISIONS_CAPTURE_MAX_RSS_MB", "2048")
    assert not Limits().laya_fits  # a 2048 MB budget is too small (Laya peaks ~2.3 GB) -> regex-only
    monkeypatch.delenv("DECISIONS_CAPTURE_MAX_RSS_MB")
    assert lim.laya_fits and lim.laya_device == "cpu" and lim.laya_batch == 4 and lim.laya_chars == 600


def test_watchdog_kills_worker_and_notifies_once():
    import time

    from distr.core.mempalace.selfimprove.memguard import Limits, MemoryGuard, WorkerWatchdog

    seq = iter([100, 2500, 2500, 2800] + [2800] * 100)
    notes: list[str] = []
    g = MemoryGuard(Limits(), measure=lambda pid: next(seq), notifier=lambda m: notes.append(m) or True,
                    log=lambda m: None)
    killed: list[bool] = []
    with WorkerWatchdog(g, lambda: killed.append(True), interval=0.01) as wd:
        for _ in range(100):
            if wd.tripped:
                break
            time.sleep(0.01)
    assert wd.tripped and killed == [True]
    assert g.notified == ["warn", "stop"] and len(notes) == 2
