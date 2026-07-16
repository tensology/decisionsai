#!/usr/bin/env python3
"""Probe a running DecisionsAI web service and emit machine-readable soak evidence."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import statistics
import time
from urllib.request import urlopen


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8765")
    parser.add_argument("--duration", type=float, default=600.0)
    parser.add_argument("--interval", type=float, default=5.0)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    urls = (f"{args.base_url}/health", f"{args.base_url}/workflows/")
    started = time.time()
    latencies: list[float] = []
    failures: list[dict[str, object]] = []
    probe = 0
    while time.time() - started < args.duration:
        probe += 1
        for url in urls:
            request_started = time.perf_counter()
            try:
                with urlopen(url, timeout=min(10.0, args.interval)) as response:
                    response.read(256)
                    if response.status != 200:
                        raise RuntimeError(f"HTTP {response.status}")
                latencies.append((time.perf_counter() - request_started) * 1000)
            except Exception as exc:
                failures.append({"probe": probe, "url": url, "error": str(exc)})
        remaining = args.duration - (time.time() - started)
        if remaining > 0:
            time.sleep(min(args.interval, remaining))

    ordered = sorted(latencies)
    result = {
        "started_at": started,
        "ended_at": time.time(),
        "duration_seconds": round(time.time() - started, 2),
        "requests": len(latencies) + len(failures),
        "successful": len(latencies),
        "failures": failures,
        "median_ms": round(statistics.median(latencies), 2) if latencies else None,
        "average_ms": round(statistics.fmean(latencies), 2) if latencies else None,
        "p95_ms": round(ordered[min(len(ordered) - 1, int(len(ordered) * 0.95))], 2) if ordered else None,
        "max_ms": round(max(latencies), 2) if latencies else None,
    }
    rendered = json.dumps(result, indent=2)
    if args.output:
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
