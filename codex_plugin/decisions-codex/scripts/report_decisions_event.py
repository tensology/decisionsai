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
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


def _json_arg(value: str) -> dict:
    if not value:
        return {}
    try:
        parsed = json.loads(value)
        return parsed if isinstance(parsed, dict) else {"value": parsed}
    except Exception:
        return {"raw": value}


def _with_internal_token(url: str) -> str:
    token = (os.environ.get("DECISIONSAI_INTERNAL_API_TOKEN") or "").strip()
    if not token:
        return url
    parts = urlsplit(url)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query.setdefault("internal_token", token)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


def _event_body(args: argparse.Namespace, *, event_type: str, status: str, message: str, input_text: str, output_text: str, session_id: int | None = None) -> dict:
    return {
        "source": args.source,
        "cwd": args.cwd,
        "event_type": event_type,
        "status": status,
        "message": message,
        "input": input_text,
        "output": output_text,
        "session_id": session_id if session_id is not None else args.execution_session_id,
        "step_id": args.step_id,
        "ticket_id": args.ticket_id,
        "project_id": args.project_id,
        "payload": _json_arg(args.payload_json),
        "evidence": _json_arg(args.evidence_json),
    }


def _post_event(target_url: str, body: dict, *, strict: bool) -> tuple[int, str]:
    data = json.dumps(body).encode("utf-8")
    request = urllib.request.Request(
        _with_internal_token(target_url),
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return 0, response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        message = exc.read().decode("utf-8", errors="replace") or str(exc)
        if strict:
            sys.stderr.write(message)
            sys.stderr.write("\n")
            return 1, message
        return 0, ""
    except Exception as exc:
        message = f"Failed to report DecisionsAI event: {exc}"
        if strict:
            sys.stderr.write(message)
            sys.stderr.write("\n")
            return 1, message
        return 0, ""


def _response_session_id(text: str) -> int | None:
    try:
        payload = json.loads(text or "{}")
    except Exception:
        return None
    session = payload.get("session") if isinstance(payload, dict) else None
    if not isinstance(session, dict):
        return None
    value = session.get("id")
    return int(value) if str(value or "").isdigit() else None


def main() -> int:
    parser = argparse.ArgumentParser(description="Report a Codex event to DecisionsAI.")
    parser.add_argument("--callback-url", default="")
    parser.add_argument("--api-base", default=os.environ.get("DECISIONS_API_BASE", "http://127.0.0.1:8765"))
    parser.add_argument("--cwd", default=os.getcwd())
    parser.add_argument("--source", default="codex")
    parser.add_argument("--event-type", default="codex_event")
    parser.add_argument("--status", default="")
    parser.add_argument("--message", default="")
    parser.add_argument("--input", default="")
    parser.add_argument("--output", default="")
    parser.add_argument("--turn-input", default="", help="Record an IDE prompt event before the completion event.")
    parser.add_argument("--turn-output", default="", help="Record an IDE completion event after the prompt event.")
    parser.add_argument("--turn-status", default="completed")
    parser.add_argument("--execution-session-id", type=int, default=None)
    parser.add_argument("--step-id", type=int, default=None)
    parser.add_argument("--ticket-id", type=int, default=None)
    parser.add_argument("--project-id", type=int, default=None)
    parser.add_argument("--payload-json", default="")
    parser.add_argument("--evidence-json", default="")
    parser.add_argument("--strict", action="store_true", help="Return non-zero and print errors when DecisionsAI is offline.")
    args = parser.parse_args()

    target_url = args.callback_url or f"{args.api_base.rstrip('/')}/api/ide/sessions/event"
    if args.turn_input or args.turn_output:
        session_id = args.execution_session_id
        outputs: list[str] = []
        if args.turn_input:
            code, text = _post_event(
                target_url,
                _event_body(
                    args,
                    event_type=f"{args.source}_prompt_submitted",
                    status="observed",
                    message=args.message or "IDE prompt submitted.",
                    input_text=args.turn_input,
                    output_text="",
                    session_id=session_id,
                ),
                strict=args.strict,
            )
            if code:
                return code
            outputs.append(text)
            session_id = _response_session_id(text) or session_id
        if args.turn_output:
            code, text = _post_event(
                target_url,
                _event_body(
                    args,
                    event_type=f"{args.source}_completed" if args.turn_status == "completed" else f"{args.source}_{args.turn_status}",
                    status=args.turn_status,
                    message=args.message or "IDE response completed.",
                    input_text="",
                    output_text=args.turn_output,
                    session_id=session_id,
                ),
                strict=args.strict,
            )
            if code:
                return code
            outputs.append(text)
        if outputs:
            sys.stdout.write(outputs[-1])
            sys.stdout.write("\n")
        return 0

    code, text = _post_event(
        target_url,
        _event_body(
            args,
            event_type=args.event_type,
            status=args.status,
            message=args.message,
            input_text=args.input,
            output_text=args.output,
        ),
        strict=args.strict,
    )
    if text:
        sys.stdout.write(text)
        sys.stdout.write("\n")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
