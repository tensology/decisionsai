#!/usr/bin/env python3
"""Report Codex-side workflow events back to DecisionsAI.

This is intentionally tiny and dependency-free so Codex can call it from any
project folder while working on a DecisionsAI ticket.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request


def _json_arg(value: str) -> dict:
    if not value:
        return {}
    try:
        parsed = json.loads(value)
        return parsed if isinstance(parsed, dict) else {"value": parsed}
    except Exception:
        return {"raw": value}


def main() -> int:
    parser = argparse.ArgumentParser(description="Report a Codex event to DecisionsAI.")
    parser.add_argument("--callback-url", required=True)
    parser.add_argument("--event-type", default="codex_event")
    parser.add_argument("--status", default="")
    parser.add_argument("--message", default="")
    parser.add_argument("--input", default="")
    parser.add_argument("--output", default="")
    parser.add_argument("--execution-session-id", type=int, default=None)
    parser.add_argument("--step-id", type=int, default=None)
    parser.add_argument("--ticket-id", type=int, default=None)
    parser.add_argument("--project-id", type=int, default=None)
    parser.add_argument("--payload-json", default="")
    parser.add_argument("--evidence-json", default="")
    args = parser.parse_args()

    body = {
        "event_type": args.event_type,
        "status": args.status,
        "message": args.message,
        "input": args.input,
        "output": args.output,
        "execution_session_id": args.execution_session_id,
        "step_id": args.step_id,
        "ticket_id": args.ticket_id,
        "project_id": args.project_id,
        "payload": _json_arg(args.payload_json),
        "evidence": _json_arg(args.evidence_json),
    }
    data = json.dumps(body).encode("utf-8")
    request = urllib.request.Request(
        args.callback_url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            sys.stdout.write(response.read().decode("utf-8", errors="replace"))
            sys.stdout.write("\n")
        return 0
    except urllib.error.HTTPError as exc:
        sys.stderr.write(exc.read().decode("utf-8", errors="replace") or str(exc))
        sys.stderr.write("\n")
        return 1
    except Exception as exc:
        sys.stderr.write(f"Failed to report DecisionsAI event: {exc}\n")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
