#!/usr/bin/env python3
"""Measure Mission Control responsiveness over an idle/long-running window."""

from __future__ import annotations

import argparse
import json
import statistics
import time
import urllib.error
import urllib.request


DEFAULT_PATHS = ("/workflows/", "/api/workflows/active-runs?limit=1")


def summarize(samples: list[float], failures: list[dict], duration_seconds: float) -> dict:
    ordered = sorted(samples)
    p95_index = max(0, min(len(ordered) - 1, round(len(ordered) * 0.95) - 1)) if ordered else 0
    return {
        "duration_seconds": round(duration_seconds, 2),
        "requests": len(samples) + len(failures),
        "successful_requests": len(samples),
        "failures": len(failures),
        "average_ms": round(statistics.fmean(samples), 2) if samples else None,
        "median_ms": round(statistics.median(samples), 2) if samples else None,
        "p95_ms": round(ordered[p95_index], 2) if ordered else None,
        "max_ms": round(max(samples), 2) if samples else None,
        "failure_details": failures[:10],
    }


def run_soak(base_url: str, duration: float, interval: float, timeout: float) -> dict:
    base_url = base_url.rstrip("/")
    started = time.monotonic()
    deadline = started + duration
    samples: list[float] = []
    failures: list[dict] = []
    cycles = 0
    while True:
        cycle_started = time.monotonic()
        for path in DEFAULT_PATHS:
            request_started = time.monotonic()
            try:
                with urllib.request.urlopen(f"{base_url}{path}", timeout=timeout) as response:
                    response.read(1)
                    if response.status >= 500:
                        raise RuntimeError(f"HTTP {response.status}")
                samples.append((time.monotonic() - request_started) * 1000)
            except (OSError, RuntimeError, urllib.error.URLError) as exc:
                failures.append({"at_seconds": round(time.monotonic() - started, 2), "path": path, "error": str(exc)})
        cycles += 1
        now = time.monotonic()
        if now >= deadline:
            break
        time.sleep(min(max(0.0, interval - (now - cycle_started)), max(0.0, deadline - now)))
    result = summarize(samples, failures, time.monotonic() - started)
    result.update({"base_url": base_url, "cycles": cycles, "paths": list(DEFAULT_PATHS), "interval_seconds": interval})
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8765")
    parser.add_argument("--duration", type=float, default=600.0)
    parser.add_argument("--interval", type=float, default=5.0)
    parser.add_argument("--timeout", type=float, default=5.0)
    args = parser.parse_args()
    result = run_soak(args.base_url, args.duration, args.interval, args.timeout)
    print(json.dumps(result, indent=2))
    return 1 if result["failures"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
