#!/usr/bin/env python3
"""Report Codex-side workflow and ambient harness events to DecisionsAI.

This stays dependency-free so Codex can call it from any project folder. When
DecisionsAI is off, reporting is quiet by default.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
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


def _debug_enabled() -> bool:
    return (os.environ.get("DECISIONSAI_HARNESS_DEBUG") or os.environ.get("DEBUG") or "").upper() == "TRUE"


def _project_folder(args: argparse.Namespace) -> str:
    return args.project_folder or args.cwd or os.getcwd()


def _is_workflow_bridge_url(url: str) -> bool:
    return "/codex-events" in (url or "")


def _discover_packet_meta(cwd: str) -> dict:
    roots: list[Path] = []
    project_root = Path(cwd or os.getcwd()).expanduser().resolve()
    roots.append(project_root)
    if project_root.name != ".tickets":
        roots.append(project_root / ".tickets")

    seen: set[str] = set()
    for root in roots:
        if not root.is_dir():
            continue
        key = str(root)
        if key in seen:
            continue
        seen.add(key)
        try:
            packets: list[Path] = []
            for pattern in ("ticket_*.md", "decisionsai_*.md"):
                packets.extend(root.glob(pattern))
            packets = sorted(
                packets,
                key=lambda item: item.stat().st_mtime,
                reverse=True,
            )
        except Exception:
            continue
        for packet in packets:
            try:
                head = packet.read_text(encoding="utf-8", errors="replace")[:8000]
            except Exception:
                continue
            for marker in ("<!-- decisions-ide-meta:", "<!-- decisions-meta:"):
                if marker not in head:
                    continue
                start = head.index(marker) + len(marker)
                end = head.find("-->", start)
                if end < 0:
                    continue
                try:
                    meta = json.loads(head[start:end].strip())
                except Exception:
                    continue
                if isinstance(meta, dict) and meta:
                    meta["_packet_path"] = str(packet)
                    return meta
    return {}


def _apply_packet_meta(args: argparse.Namespace) -> None:
    meta = _discover_packet_meta(args.cwd)
    if not meta:
        return
    if not args.callback_url:
        bridge = str(meta.get("bridge_url") or "").strip()
        if bridge:
            args.callback_url = bridge
    for attr, key in (
        ("execution_session_id", "execution_session_id"),
        ("step_id", "step_id"),
        ("ticket_id", "ticket_id"),
        ("board_id", "board_id"),
        ("project_id", "project_id"),
        ("workflow_id", "workflow_id"),
        ("run_id", "run_id"),
    ):
        if getattr(args, attr, None) is None:
            raw = meta.get(key)
            if str(raw or "").isdigit():
                setattr(args, attr, int(raw))


def _bridge_event_body(
    args: argparse.Namespace,
    *,
    event_type: str,
    status: str,
    message: str,
    input_text: str,
    output_text: str,
) -> dict:
    return {
        "event_type": event_type,
        "status": status or "",
        "message": message or output_text or input_text,
        "input": input_text,
        "output": output_text,
        "execution_session_id": args.execution_session_id,
        "step_id": args.step_id,
        "ticket_id": args.ticket_id,
        "project_id": args.project_id,
        "payload": _json_arg(args.payload_json),
        "evidence": _json_arg(args.evidence_json),
    }


def _event_body(
    args: argparse.Namespace,
    *,
    event_type: str,
    status: str,
    message: str,
    input_text: str,
    output_text: str,
    session_id: int | None = None,
) -> dict:
    return {
        "source": args.source,
        "cwd": args.cwd,
        "event_type": event_type,
        "status": status,
        "message": message,
        "input": input_text,
        "output": output_text,
        "session_id": session_id if session_id is not None else args.execution_session_id,
        "execution_session_id": session_id if session_id is not None else args.execution_session_id,
        "step_id": args.step_id,
        "ticket_id": args.ticket_id,
        "board_id": args.board_id,
        "project_id": args.project_id,
        "workflow_id": args.workflow_id,
        "run_id": args.run_id,
        "thread_id": args.thread_id,
        "payload": _json_arg(args.payload_json),
        "evidence": _json_arg(args.evidence_json),
        "harness": args.harness or args.source,
        "project_folder": _project_folder(args),
    }


def _harness_body(
    args: argparse.Namespace,
    *,
    event_type: str,
    status: str,
    message: str,
    input_text: str,
    output_text: str,
) -> dict:
    return {
        "harness": args.harness or args.source or "codex",
        "event_type": event_type,
        "status": status,
        "message": message,
        "input": input_text,
        "output": output_text,
        "source": args.source,
        "project_folder": _project_folder(args),
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
        if _debug_enabled():
            sys.stderr.write(message)
            sys.stderr.write("\n")
        return 0, ""
    except Exception as exc:
        message = f"Failed to report DecisionsAI event: {exc}"
        if strict:
            sys.stderr.write(message)
            sys.stderr.write("\n")
            return 1, message
        if _debug_enabled():
            sys.stderr.write(message)
            sys.stderr.write("\n")
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


def _uses_harness_endpoint(args: argparse.Namespace, target_url: str) -> bool:
    if "/api/harness/events" in target_url:
        return True
    if args.callback_url:
        return False
    source = (args.source or "").strip().lower()
    harness = (args.harness or "").strip().lower()
    return source == "ambient" or (harness and harness != source)


def _target_url(args: argparse.Namespace) -> str:
    if args.callback_url:
        return args.callback_url
    if _uses_harness_endpoint(args, ""):
        return f"{args.api_base.rstrip('/')}/api/harness/events"
    return f"{args.api_base.rstrip('/')}/api/ide/sessions/event"


def _body_for(
    args: argparse.Namespace,
    *,
    harness_endpoint: bool,
    bridge_endpoint: bool,
    event_type: str,
    status: str,
    message: str,
    input_text: str,
    output_text: str,
    session_id: int | None = None,
) -> dict:
    if bridge_endpoint:
        return _bridge_event_body(
            args,
            event_type=event_type,
            status=status,
            message=message,
            input_text=input_text,
            output_text=output_text,
        )
    if harness_endpoint:
        return _harness_body(
            args,
            event_type=event_type,
            status=status,
            message=message,
            input_text=input_text,
            output_text=output_text,
        )
    return _event_body(
        args,
        event_type=event_type,
        status=status,
        message=message,
        input_text=input_text,
        output_text=output_text,
        session_id=session_id,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Report a Codex event to DecisionsAI.")
    parser.add_argument("--callback-url", default="")
    parser.add_argument("--api-base", default=os.environ.get("DECISIONS_API_BASE", "http://127.0.0.1:8765"))
    parser.add_argument("--cwd", default=os.getcwd())
    parser.add_argument("--source", default="codex")
    parser.add_argument("--harness", default="")
    parser.add_argument("--event-type", default="codex_event")
    parser.add_argument("--status", default="")
    parser.add_argument("--message", default="")
    parser.add_argument("--input", default="")
    parser.add_argument("--output", default="")
    parser.add_argument("--turn-input", default="", help="Record an IDE prompt event before the completion event.")
    parser.add_argument("--turn-output", default="", help="Record an IDE completion event after the prompt event.")
    parser.add_argument("--turn-status", default="completed")
    parser.add_argument("--project-folder", default="")
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
    parser.add_argument("--strict", action="store_true", help="Return non-zero and print errors when DecisionsAI is offline.")
    args = parser.parse_args()

    _apply_packet_meta(args)
    target_url = _target_url(args)
    bridge_endpoint = _is_workflow_bridge_url(target_url)
    harness_endpoint = _uses_harness_endpoint(args, target_url) and not bridge_endpoint
    if args.turn_input or args.turn_output:
        session_id = args.execution_session_id
        outputs: list[str] = []
        if args.turn_input:
            code, text = _post_event(
                target_url,
                _body_for(
                    args,
                    harness_endpoint=harness_endpoint,
                    bridge_endpoint=bridge_endpoint,
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
            if not bridge_endpoint:
                session_id = _response_session_id(text) or session_id
        if args.turn_output:
            event_status = args.turn_status or "completed"
            code, text = _post_event(
                target_url,
                _body_for(
                    args,
                    harness_endpoint=harness_endpoint,
                    bridge_endpoint=bridge_endpoint,
                    event_type=f"{args.source}_completed" if event_status == "completed" else f"{args.source}_{event_status}",
                    status=event_status,
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
        if outputs and (not harness_endpoint or _debug_enabled()):
            sys.stdout.write(outputs[-1])
            sys.stdout.write("\n")
        return 0

    code, text = _post_event(
        target_url,
        _body_for(
            args,
            harness_endpoint=harness_endpoint,
            bridge_endpoint=bridge_endpoint,
            event_type=args.event_type,
            status=args.status,
            message=args.message,
            input_text=args.input,
            output_text=args.output,
        ),
        strict=args.strict,
    )
    if text and (not harness_endpoint or _debug_enabled()):
        sys.stdout.write(text)
        sys.stdout.write("\n")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
