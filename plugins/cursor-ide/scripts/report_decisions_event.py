#!/usr/bin/env python3
"""Report Cursor-side workflow events back to DecisionsAI.

This is intentionally tiny and dependency-free so Cursor can call it from any
project folder while working on a DecisionsAI ticket.
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


def _is_workflow_bridge_url(url: str) -> bool:
    return "/codex-events" in (url or "")


def _discover_packet_meta(cwd: str) -> dict:
    """Read the newest DecisionsAI work packet meta from the project .tickets folder."""
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
        ("project_id", "project_id"),
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


def _ide_event_body(
    args: argparse.Namespace,
    *,
    event_type: str,
    status: str,
    message: str,
    input_text: str,
    output_text: str,
    session_id: int | None = None,
) -> dict:
    payload = _json_arg(args.payload_json)
    if getattr(args, "thread_id", ""):
        payload["thread_id"] = args.thread_id
        payload["external_thread_id"] = args.thread_id
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
        "payload": payload,
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


def _body_for(
    args: argparse.Namespace,
    *,
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
    return _ide_event_body(
        args,
        event_type=event_type,
        status=status,
        message=message,
        input_text=input_text,
        output_text=output_text,
        session_id=session_id,
    )


def _locks_path(surface: str) -> Path:
    return Path.home() / ".decisions" / "ide-threads" / f"{surface}.json"


def _read_locks(surface: str) -> dict:
    try:
        data = json.loads(_locks_path(surface).read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _write_lock(surface: str, folder: str, thread_id: str, seen_at: float) -> None:
    if not thread_id:
        return
    path = _locks_path(surface)
    path.parent.mkdir(parents=True, exist_ok=True)
    locks = _read_locks(surface)
    locks[folder] = {"thread_id": thread_id, "seen_at": seen_at}
    path.write_text(json.dumps(locks), encoding="utf-8")


def _discover_cursor_thread(folder: str) -> tuple[str, float]:
    """Newest Cursor IDE agent transcript for this project folder."""
    slug = folder.lstrip("/").replace("/", "-")
    root = Path.home() / ".cursor" / "projects" / slug / "agent-transcripts"
    if not root.is_dir():
        return "", 0.0
    best_id, best_mtime = "", 0.0
    try:
        children = list(root.iterdir())
    except Exception:
        return "", 0.0
    for child in children:
        if not child.is_dir():
            continue
        path = child / f"{child.name}.jsonl"
        if not path.is_file():
            continue
        try:
            mtime = path.stat().st_mtime
        except Exception:
            continue
        if mtime >= best_mtime:
            best_id, best_mtime = child.name, mtime
    return best_id, best_mtime


def _resolve_plugin_thread_id(args: argparse.Namespace) -> str:
    """Lock this Cursor IDE chat. Development harness sessions are a different surface."""
    folder = str(Path(args.cwd or os.getcwd()).expanduser().resolve())
    args.cwd = folder
    surface = (args.source or "cursor").strip().lower() or "cursor"
    explicit = str(args.thread_id or "").strip()
    current = _read_locks(surface).get(folder)
    current = current if isinstance(current, dict) else {}
    locked = str(current.get("thread_id") or "")
    locked_seen = float(current.get("seen_at") or 0)
    found_id, found_at = _discover_cursor_thread(folder) if surface == "cursor" else ("", 0.0)
    if explicit:
        chosen, seen = explicit, max(found_at, locked_seen, 1.0)
    elif args.new_thread:
        chosen, seen = found_id, found_at
    elif found_id and found_at > locked_seen:
        chosen, seen = found_id, found_at
    elif locked:
        chosen, seen = locked, locked_seen
    else:
        chosen, seen = found_id, found_at
    if chosen:
        _write_lock(surface, folder, chosen, seen or 1.0)
    return chosen


def main() -> int:
    parser = argparse.ArgumentParser(description="Report a Cursor event to DecisionsAI.")
    parser.add_argument("--callback-url", default="")
    parser.add_argument("--api-base", default=os.environ.get("DECISIONS_API_BASE", "http://127.0.0.1:8765"))
    parser.add_argument("--cwd", default=os.getcwd())
    parser.add_argument("--source", default="cursor")
    parser.add_argument("--event-type", default="cursor_event")
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
    parser.add_argument("--thread-id", default="", help="Cursor IDE chat id. The reporter locks this for the project.")
    parser.add_argument("--new-thread", action="store_true", help="Start a new Cursor IDE thread lock for this project.")
    parser.add_argument("--strict", action="store_true", help="Return non-zero and print errors when DecisionsAI is offline.")
    args = parser.parse_args()

    args.thread_id = _resolve_plugin_thread_id(args)
    _apply_packet_meta(args)
    target_url = args.callback_url or f"{args.api_base.rstrip('/')}/api/ide/sessions/event"
    bridge_endpoint = _is_workflow_bridge_url(target_url)

    if args.turn_input or args.turn_output:
        session_id = args.execution_session_id
        outputs: list[str] = []
        if args.turn_input:
            code, text = _post_event(
                target_url,
                _body_for(
                    args,
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
        if outputs:
            sys.stdout.write(outputs[-1])
            sys.stdout.write("\n")
        if args.thread_id:
            sys.stdout.write(f"thread_id={args.thread_id}\n")
        return 0

    code, text = _post_event(
        target_url,
        _body_for(
            args,
            bridge_endpoint=bridge_endpoint,
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
    if args.thread_id:
        sys.stdout.write(f"thread_id={args.thread_id}\n")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
