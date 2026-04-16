#!/usr/bin/env python3
"""Cross-service log analysis with noise detection.

Portable script for analyzing logs from Docker Compose services,
local log files, or Loki HTTP API. Produces JSON output with
service x level summary, noise detection, and actionable suggestions.

Auto-detects log source (docker -> file -> loki) or accepts explicit --mode.

Usage:
    python analyze_test_logs.py                           # auto-detect
    python analyze_test_logs.py --mode docker --since 5m  # Docker
    python analyze_test_logs.py --mode file --path logs/  # files
    python analyze_test_logs.py --mode loki --loki-url http://localhost:3100
    python analyze_test_logs.py --mode loki --loki-url https://grafana.example.com/proxy/2 --token-env GRAFANA_SA_TOKEN
    python analyze_test_logs.py --threshold 20 --top 10   # noise params
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Constants & patterns
# ---------------------------------------------------------------------------

LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")
LEVELS_SET = frozenset(LEVELS)
DEFAULT_LOG_DIR = "tests/manual/results"
DEFAULT_PERIOD = "5m"
DEFAULT_THRESHOLD = 10
DEFAULT_TOP = 20

# Pipe-delimited: ts | LEVEL | trace_id=xxx | module:line | func() | msg
PIPE_LOG_RE = re.compile(
    r"^.+?\s\|\s(DEBUG|INFO|WARNING|ERROR|CRITICAL)\s+\|\s.+?\|\s.+?\|\s(.+)$"
)
PG_LOG_RE = re.compile(r"\b(ERROR|WARNING|LOG|FATAL|PANIC):\s+(.+)")
REDIS_WARN_RE = re.compile(r"^#\s*(WARNING)\s*(.*)$", re.IGNORECASE)
REDIS_INFO_RE = re.compile(r"^[*\d]")
PLAIN_LEVEL_RE = re.compile(
    r"\b(DEBUG|INFO|WARNING|ERROR|CRITICAL)\b", re.IGNORECASE
)

# Template normalization (order matters: UUIDs before generic numbers)
NORMALIZERS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
                re.IGNORECASE), "<UUID>"),
    (re.compile(r"trace_id=[0-9a-fA-F]+"), "trace_id=<TRACE>"),
    (re.compile(r"\d{2}-\d{2}-\d{4}\s\d{2}:\d{2}:\d{2}"), "<TS>"),
    (re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}[.\d]*Z?"), "<TS>"),
    (re.compile(r"\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}(:\d+)?"), "<IP>"),
    (re.compile(r"/[0-9a-f]{8,}"), "/<ID>"),
    (re.compile(r"\b\d{4,}\b"), "<N>"),
]

PERIOD_RE = re.compile(r"^(\d+)([smhd])$")
PERIOD_MULT = {"s": 1, "m": 60, "h": 3600, "d": 86400}

# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class LogEntry:
    """Single parsed log entry."""
    level: str
    service: str
    message: str
    raw: str = ""

@dataclass(slots=True)
class NoiseGroup:
    """Cluster of repeated log messages sharing the same template."""
    template: str
    count: int
    level: str
    service: str
    samples: list[str] = field(default_factory=list)
    noise_ratio: float = 0.0

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _die(msg: str) -> None:
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(1)

def _empty_result(mode: str, period: str) -> dict[str, Any]:
    return {"status": "OK", "mode": mode, "period": period,
            "total_entries": 0, "summary": {}, "noise": [],
            "errors": [], "warnings": []}

def parse_period(s: str) -> int:
    """Convert '5m', '1h', '24h' to seconds."""
    m = PERIOD_RE.match(s)
    if not m:
        _die(f"Invalid period: {s}. Use e.g. 5m, 1h, 24h.")
    return int(m.group(1)) * PERIOD_MULT[m.group(2)]

def normalize_message(msg: str) -> str:
    """Replace variable parts (UUIDs, IPs, timestamps) with placeholders."""
    result = msg
    for pat, repl in NORMALIZERS:
        result = pat.sub(repl, result)
    return result

def _svc_type(service: str) -> str:
    """Guess infra type from service name for parser dispatch."""
    name = service.lower()
    if "postgres" in name or "pg" in name or "psql" in name:
        return "postgres"
    if "redis" in name:
        return "redis"
    return "app"

# ---------------------------------------------------------------------------
# Parsers (multi-format auto-detect per line)
# ---------------------------------------------------------------------------

def parse_line(line: str, service: str) -> LogEntry | None:
    """Auto-detect format and parse a single log line.

    Format priority: JSON -> pipe-delimited -> PostgreSQL -> Redis -> plain.
    """
    s = line.strip()
    if not s:
        return None
    st = _svc_type(service)

    # JSON structured
    if s.startswith("{"):
        try:
            obj = json.loads(s)
            lvl = obj.get("level", obj.get("levelname", ""))
            msg = obj.get("message", obj.get("msg", ""))
            if lvl and msg:
                return LogEntry(level=lvl.upper(), service=service,
                                message=msg, raw=s)
        except (json.JSONDecodeError, KeyError, TypeError):
            pass
        return None

    # Pipe-delimited
    m = PIPE_LOG_RE.match(s)
    if m:
        return LogEntry(level=m.group(1), service=service,
                        message=m.group(2), raw=s)

    # PostgreSQL native
    if st == "postgres":
        m = PG_LOG_RE.search(s)
        if m:
            lvl = "INFO" if m.group(1) == "LOG" else m.group(1)
            return LogEntry(level=lvl, service=service,
                            message=m.group(2), raw=s)
        return None

    # Redis native
    if st == "redis":
        m = REDIS_WARN_RE.match(s)
        if m:
            return LogEntry(level="WARNING", service=service,
                            message=m.group(2).strip(), raw=s)
        if REDIS_INFO_RE.match(s):
            return LogEntry(level="INFO", service=service, message=s, raw=s)
        return None

    # Plain text with level keyword
    m = PLAIN_LEVEL_RE.search(s)
    if m:
        return LogEntry(level=m.group(1).upper(), service=service,
                        message=s, raw=s)
    return None

# ---------------------------------------------------------------------------
# Collectors
# ---------------------------------------------------------------------------

def _docker_available() -> bool:
    try:
        r = subprocess.run(["docker", "info"], capture_output=True, timeout=10)
        return r.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False

def _docker_services() -> list[str]:
    """Discover running services via docker compose ps."""
    try:
        r = subprocess.run(
            ["docker", "compose", "ps", "--format", "json"],
            capture_output=True, text=True, errors="replace", timeout=15,
        )
        if r.returncode != 0:
            return []
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return []

    output = r.stdout.strip()
    if not output:
        return []

    services: list[str] = []
    # Docker outputs JSON array or one JSON object per line
    chunks = [output] if output.startswith("[") else output.splitlines()
    for chunk in chunks:
        chunk = chunk.strip()
        if not chunk:
            continue
        try:
            parsed = json.loads(chunk)
            items = parsed if isinstance(parsed, list) else [parsed]
            for item in items:
                name = item.get("Name", item.get("name", ""))
                if name:
                    services.append(name)
        except json.JSONDecodeError:
            continue
    return services

def collect_docker(since: str) -> list[LogEntry]:
    """Collect logs from all Docker Compose services."""
    services = _docker_services()
    if not services:
        return []
    entries: list[LogEntry] = []
    for svc in services:
        try:
            r = subprocess.run(
                ["docker", "compose", "logs", svc, "--since", since],
                capture_output=True, text=True, errors="replace", timeout=30,
            )
        except subprocess.TimeoutExpired:
            print(f"Timeout collecting logs from {svc}, skipping",
                  file=sys.stderr)
            continue
        for raw_line in (r.stdout + r.stderr).splitlines():
            # Strip docker compose prefix: "service-1  | actual log line"
            content = raw_line
            idx = raw_line.find(" | ")
            if 0 < idx < 40:
                prefix = raw_line[:idx].strip()
                if prefix and not any(c in prefix for c in "{}[]"):
                    content = raw_line[idx + 3:]
            entry = parse_line(content, svc)
            if entry:
                entries.append(entry)
    return entries

def collect_file(log_dir: str) -> list[LogEntry]:
    """Collect logs from .log files in specified directory."""
    log_path = Path(log_dir)
    if not log_path.is_dir():
        return []
    entries: list[LogEntry] = []
    for log_file in sorted(log_path.glob("*.log")):
        svc_name = log_file.stem
        try:
            text = log_file.read_text(encoding="utf-8", errors="replace")
        except OSError:
            print(f"Cannot read {log_file}, skipping", file=sys.stderr)
            continue
        for raw_line in text.splitlines():
            entry = parse_line(raw_line, svc_name)
            if entry:
                entries.append(entry)
    return entries

def collect_loki(
    period_seconds: int,
    base_url: str,
    token: str | None = None,
    query: str = '{job=~".+"}',
) -> list[LogEntry]:
    """Collect logs from Loki HTTP API via query_range."""
    end_ns = int(time.time()) * 1_000_000_000
    start_ns = end_ns - (period_seconds * 1_000_000_000)
    params = urllib.parse.urlencode({
        "query": query, "start": str(start_ns),
        "end": str(end_ns), "limit": "5000",
    })
    url = f"{base_url}/loki/api/v1/query_range?{params}"
    req = urllib.request.Request(url)
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.URLError as e:
        _die(f"Loki request failed: {e}")
        return []
    if data.get("status") != "success":
        _die(f"Loki returned non-success: {data.get('status')}")

    entries: list[LogEntry] = []
    for stream in data.get("data", {}).get("result", []):
        labels: dict[str, Any] = stream.get("stream", {})
        svc = str(labels.get("service_name") or labels.get("service")
                   or labels.get("container_name") or labels.get("job")
                   or "unknown")
        label_lvl = str(labels.get("level", "")).upper()
        for _ts, log_line in stream.get("values", []):
            msg = str(log_line)
            if label_lvl in LEVELS_SET:
                m = PIPE_LOG_RE.match(msg)
                entries.append(LogEntry(
                    level=label_lvl, service=svc,
                    message=m.group(2) if m else msg, raw=msg))
            else:
                parsed = parse_line(msg, svc)
                if parsed:
                    entries.append(parsed)
    return entries

# ---------------------------------------------------------------------------
# Auto-detection
# ---------------------------------------------------------------------------

def auto_detect_mode(
    file_path: str | None,
) -> tuple[str, str | None]:
    """Detect best available log source. Priority: docker -> file -> loki."""
    if _docker_available() and _docker_services():
        return ("docker", None)
    check_dir = file_path or DEFAULT_LOG_DIR
    p = Path(check_dir)
    if p.is_dir() and list(p.glob("*.log")):
        return ("file", str(p))
    if os.environ.get("LOKI_URL"):
        return ("loki", None)
    return ("none", None)

# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------

def build_summary(entries: list[LogEntry]) -> dict[str, dict[str, int]]:
    """Build service -> {level: count} mapping."""
    summary: dict[str, Counter[str]] = defaultdict(Counter)
    for e in entries:
        summary[e.service][e.level] += 1
    return {svc: dict(counts) for svc, counts in sorted(summary.items())}

def detect_noise(
    entries: list[LogEntry], threshold: int, top_n: int,
) -> list[NoiseGroup]:
    """Find high-volume message templates exceeding threshold."""
    groups: dict[tuple[str, str, str], list[str]] = defaultdict(list)
    for e in entries:
        tpl = normalize_message(e.message)
        groups[(e.service, e.level, tpl)].append(e.raw or e.message)
    svc_totals: Counter[str] = Counter(e.service for e in entries)

    noise: list[NoiseGroup] = []
    for (svc, level, tpl), samples in groups.items():
        count = len(samples)
        if count >= threshold:
            noise.append(NoiseGroup(
                template=tpl, count=count, level=level, service=svc,
                samples=samples[:3],
                noise_ratio=round(count / max(svc_totals[svc], 1), 4),
            ))
    noise.sort(key=lambda x: x.count, reverse=True)
    return noise[:top_n]

def collect_errors_warnings(
    entries: list[LogEntry],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Group ERROR and WARNING entries by (service, normalized_template)."""
    err_grp: dict[tuple[str, str], list[str]] = defaultdict(list)
    warn_grp: dict[tuple[str, str], list[str]] = defaultdict(list)
    for e in entries:
        tpl = normalize_message(e.message)
        if e.level in ("ERROR", "CRITICAL", "FATAL"):
            err_grp[(e.service, tpl)].append(e.raw or e.message)
        elif e.level == "WARNING":
            warn_grp[(e.service, tpl)].append(e.raw or e.message)

    errors = [{"level": "ERROR", "service": s, "message": t,
               "count": len(sm), "samples": sm[:3]}
              for (s, t), sm in sorted(err_grp.items(),
                                       key=lambda x: len(x[1]), reverse=True)]
    warnings = [{"level": "WARNING", "service": s, "message": t,
                 "count": len(sm)}
                for (s, t), sm in sorted(warn_grp.items(),
                                         key=lambda x: len(x[1]),
                                         reverse=True)]
    return errors, warnings

def suggest_action(ng: NoiseGroup) -> str:
    """Suggest remediation for a noisy log template."""
    tpl = ng.template.lower()
    if ng.level in ("ERROR", "CRITICAL", "FATAL"):
        return "INVESTIGATE: repeated errors indicate a real bug"
    if any(kw in tpl for kw in ("health", "healthcheck", "/live", "/ready")):
        return "Demote to DEBUG (health check noise)"
    if any(kw in tpl for kw in ("booting worker", "starting", "shutdown",
                                "initialized", "ready")):
        return "Acceptable startup noise (one-time per deploy)"
    if ng.level == "INFO" and ng.count > 100 and ng.noise_ratio > 0.30:
        return "Demote to DEBUG (high-volume, >30% of service logs)"
    if ng.level == "INFO" and ng.count > 100:
        return "Consider DEBUG (high-volume INFO)"
    if ng.level == "WARNING" and any(
        kw in tpl for kw in ("rate_limit", "429", "validation", "deprecated")
    ):
        return "Consider DEBUG if expected traffic"
    if ng.level == "WARNING" and ng.noise_ratio > 0.30:
        return "Review: WARNING dominates service logs (>30%)"
    return "Review manually"

# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

def build_output(
    entries: list[LogEntry], mode: str, period: str,
    threshold: int, top_n: int,
) -> dict[str, Any]:
    """Build the complete JSON output structure."""
    summary = build_summary(entries)
    noise_groups = detect_noise(entries, threshold, top_n)
    errors, warnings = collect_errors_warnings(entries)
    return {
        "status": "OK",
        "mode": mode,
        "period": period,
        "total_entries": len(entries),
        "summary": summary,
        "noise": [
            {"template": ng.template, "count": ng.count, "level": ng.level,
             "service": ng.service, "noise_ratio": ng.noise_ratio,
             "samples": ng.samples, "suggestion": suggest_action(ng)}
            for ng in noise_groups
        ],
        "errors": errors,
        "warnings": warnings,
    }

# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    ap = argparse.ArgumentParser(
        description="Cross-service log analysis with noise detection.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  %(prog)s                                    # auto-detect\n"
            "  %(prog)s --mode docker --since 5m           # Docker\n"
            "  %(prog)s --mode file --path logs/           # files\n"
            "  %(prog)s --mode loki --loki-url http://localhost:3100\n"
            "  %(prog)s --threshold 20 --top 10            # noise params\n"
        ),
    )
    ap.add_argument("--mode", choices=["docker", "file", "loki"], default=None,
                    help="Log source. Default: auto-detect (docker->file->loki)")
    ap.add_argument("--since", default=DEFAULT_PERIOD,
                    help="Time window (docker/loki). Default: %(default)s")
    ap.add_argument("--path", default=None,
                    help=f"Log file directory (file mode). Default: {DEFAULT_LOG_DIR}")
    ap.add_argument("--threshold", type=int, default=DEFAULT_THRESHOLD,
                    help="Noise threshold (min occurrences). Default: %(default)s")
    ap.add_argument("--top", type=int, default=DEFAULT_TOP,
                    help="Top N noisiest templates. Default: %(default)s")
    ap.add_argument("--loki-url", default=None,
                    help="Loki base URL. Default: LOKI_URL env var")
    ap.add_argument("--loki-query", default='{job=~".+"}',
                    help='LogQL selector. Default: \'{job=~".+"}\'')
    ap.add_argument("--token-env", default=None,
                    help="Env var name with auth token (e.g. GRAFANA_SA_TOKEN)")
    args = ap.parse_args()

    # Resolve mode
    mode = args.mode
    file_dir = args.path
    if mode is None:
        detected, extra = auto_detect_mode(args.path)
        if detected == "none":
            print(json.dumps({"status": "NO_LOG_SOURCES"}, indent=2))
            return
        mode = detected
        if detected == "file" and extra:
            file_dir = extra

    # Collect
    period_display = args.since if mode != "file" else "N/A"
    entries: list[LogEntry] = []

    if mode == "docker":
        entries = collect_docker(args.since)
    elif mode == "file":
        entries = collect_file(file_dir or args.path or DEFAULT_LOG_DIR)
    elif mode == "loki":
        loki_url = args.loki_url or os.environ.get("LOKI_URL")
        if not loki_url:
            _die("Loki URL required: use --loki-url or set LOKI_URL env var")
        token: str | None = None
        if args.token_env:
            token = os.environ.get(args.token_env)
            if not token:
                _die(f"Token env var {args.token_env} is not set")
        entries = collect_loki(
            parse_period(args.since), loki_url,
            token=token, query=args.loki_query,
        )

    if not entries:
        print(json.dumps(_empty_result(mode, period_display), indent=2))
        return

    # Analyze and output
    print(json.dumps(
        build_output(entries, mode, period_display, args.threshold, args.top),
        indent=2,
    ))


if __name__ == "__main__":
    main()
