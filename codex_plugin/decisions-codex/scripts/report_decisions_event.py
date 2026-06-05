#!/usr/bin/env python3
"""Report Codex-side workflow events back to DecisionsAI.

This is intentionally tiny and dependency-free so Codex can call it from any
project folder while working on a DecisionsAI ticket.
"""

from __future__ import annotations

import argparse
import json
import os
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


def _post_json(url: str, body: dict) -> str:
    data = json.dumps(body).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        return response.read().decode("utf-8", errors="replace")


def _debug_enabled() -> bool:
    return (os.environ.get("DECISIONSAI_HARNESS_DEBUG") or os.environ.get("DEBUG") or "").upper() == "TRUE"


def main() -> int:
    parser = argparse.ArgumentParser(description="Report a Codex event to DecisionsAI.")
    parser.add_argument("--callback-url", default="")
    parser.add_argument("--api-base", default=os.environ.get("DECISIONS_API_BASE", "http://127.0.0.1:8765"))
    parser.add_argument("--harness", default="codex")
    parser.add_argument("--event-type", default="codex_event")
    parser.add_argument("--status", default="")
    parser.add_argument("--message", default="")
    parser.add_argument("--input", default="")
    parser.add_argument("--output", default="")
    parser.add_argument("--source", default="ambient")
    parser.add_argument("--project-folder", default=os.getcwd())
    parser.add_argument("--execution-session-id", type=int, default=None)
    parser.add_argument("--workflow-id", type=int, default=None)
    parser.add_argument("--run-id", type=int, default=None)
    parser.add_argument("--step-id", type=int, default=None)
    parser.add_argument("--ticket-id", type=int, default=None)
    parser.add_argument("--board-id", type=int, default=None)
    parser.add_argument("--project-id", type=int, default=None)
    parser.add_argument("--thread-id", default="")
    parser.add_argument("--session-id", default="")
    parser.add_argument("--payload-json", default="")
    parser.add_argument("--evidence-json", default="")
    args = parser.parse_args()

    body = {
        "harness": args.harness,
        "event_type": args.event_type,
        "status": args.status,
        "message": args.message,
        "input": args.input,
        "output": args.output,
        "source": args.source,
        "project_folder": args.project_folder,
        "execution_session_id": args.execution_session_id,
        "workflow_id": args.workflow_id,
        "run_id": args.run_id,
        "step_id": args.step_id,
        "ticket_id": args.ticket_id,
        "board_id": args.board_id,
        "project_id": args.project_id,
        "thread_id": args.thread_id,
        "session_id": args.session_id,
        "payload": _json_arg(args.payload_json),
        "evidence": _json_arg(args.evidence_json),
    }
    target_url = args.callback_url or f"{args.api_base.rstrip('/')}/api/harness/events"
    try:
        response_text = _post_json(target_url, body)
        if response_text and _debug_enabled():
            sys.stdout.write(response_text)
            sys.stdout.write("\n")
        return 0
    except urllib.error.HTTPError as exc:
        if _debug_enabled():
            sys.stderr.write(exc.read().decode("utf-8", errors="replace") or str(exc))
            sys.stderr.write("\n")
        return 0
    except Exception as exc:
        if _debug_enabled():
            sys.stderr.write(f"Failed to report DecisionsAI event: {exc}\n")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
