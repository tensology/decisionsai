"""N4: Rebuild Interfaces as an overlay, never a blind overwrite.

Every generated screen carries a marker comment on the line BEFORE its `screen` line:
    # @generated scan:<key> hash:<sha8>
(`#` lines are skipped by the wire parser/validator; the marker sits above the screen so a `#|` description
run directly under the screen is untouched.) key = "overview" for the journey map, else the route URL.

On rebuild (defaults chosen, see report):
* marker present and the screen body (minus `#|` lines) still hashes to the marker -> regenerated in place
  (its `#|` description lines are kept);
* no marker, or the body was edited -> hand-made, kept verbatim (Q1: unmarked = hand-made, EXCEPT an item
  that is still at revision <= 1, i.e. untouched scan output, which is regenerated whole);
* route gone from the scan -> kept, label suffixed " (stale)", marker removed (Q2: flag, never delete);
* new routes -> appended; the trailing decisions-flow sidecar stays the last line.
Applied to soft scans AND "Scan project" (force) (Q3).
"""
from __future__ import annotations

import hashlib
import re
from typing import Any

MARKER_RE = re.compile(r"^\s*#\s*@generated\s+scan:(\S+)\s+hash:([0-9a-f]{8})\s*$")
SCREEN_RE = re.compile(r"^\s*screen\b")
ROUTE_RE = re.compile(r'\broute="([^"]*)"|\broute=(\S+)')
SIDECAR_RE = re.compile(r"^\s*#\s*decisions-flow:")
STALE = " (stale)"


ID_RE = re.compile(r"\s+id=[A-Za-z][\w-]{0,63}(?=\s|$)")


def _screen_id(line: str) -> str | None:
    m = ID_RE.search(line)
    return m.group(0) if m else None


def body_hash(lines: list[str]) -> str:
    # The screen's id= (added by the node popover / carry) does not make a screen hand-made.
    lines = [ID_RE.sub("", lines[0]), *lines[1:]] if lines else lines
    body = "\n".join(l.rstrip() for l in lines if not l.lstrip().startswith("#|") and not MARKER_RE.match(l)).strip()
    return hashlib.sha256(body.encode("utf-8")).hexdigest()[:8]


def marker(key: str, lines: list[str]) -> str:
    return f"# @generated scan:{key} hash:{body_hash(lines)}"


def mark_flows(text: str) -> str:
    """Add markers to freshly built wire output (one per screen)."""
    parsed = _blocks(text)
    # Same layout as overlay() (head, blank line between screens, sidecar last) so rebuilds are byte-idempotent.
    chunks = ["\n".join(([marker(b["key"], b["lines"])] if b["key"] else []) + b["lines"]) for b in parsed["blocks"]]
    head = "\n".join(parsed["head"]).strip("\n")
    content = ((head + "\n\n") if head else "") + "\n\n".join(chunks)
    if parsed["sidecar"]:
        content += "\n" + parsed["sidecar"]
    return content.rstrip("\n") + "\n"


def _route(screen_line: str) -> str | None:
    m = ROUTE_RE.search(screen_line)
    return (m.group(1) if m and m.group(1) is not None else (m.group(2) if m else None))


def _blocks(text: str) -> dict[str, Any]:
    lines = str(text or "").split("\n")
    sidecar = None
    while lines and not lines[-1].strip():
        lines.pop()
    if lines and SIDECAR_RE.match(lines[-1]):
        sidecar = lines.pop()
    head: list[str] = []
    blocks: list[dict[str, Any]] = []
    pending_marker = None
    for line in lines:
        m = MARKER_RE.match(line)
        if m:
            pending_marker = m
            continue
        if SCREEN_RE.match(line):
            route = _route(line)
            key = "overview" if line.lstrip().startswith('screen "Overview ·') else (route or None)
            blocks.append({"lines": [line], "marker": pending_marker, "key": pending_marker.group(1) if pending_marker else key})
            pending_marker = None
            continue
        (blocks[-1]["lines"] if blocks else head).append(line)
    for b in blocks:
        while len(b["lines"]) > 1 and not b["lines"][-1].strip():
            b["lines"].pop()
    return {"head": head, "blocks": blocks, "sidecar": sidecar}


def _descriptions(lines: list[str]) -> list[str]:
    out = []
    for line in lines[1:]:
        if line.lstrip().startswith("#|"):
            out.append(line)
        else:
            break
    return out


def _with_descriptions(lines: list[str], desc: list[str]) -> list[str]:
    if not desc:
        return lines
    rest = lines[1:]
    while rest and rest[0].lstrip().startswith("#|"):
        rest = rest[1:]
    return [lines[0], *desc, *rest]


def _stale(line: str) -> str:
    if STALE in line:
        return line
    return re.sub(r'^(\s*screen\s+"[^"]*)"', lambda m: f'{m.group(1)}{STALE}"', line, count=1) if '"' in line else line


def overlay(previous: str, generated: str, *, revision: int) -> tuple[str, dict[str, Any]]:
    """Merge freshly generated (unmarked) flows into the previous content. Returns (content, summary)."""
    fresh = _blocks(generated)
    gen = {b["key"]: b for b in fresh["blocks"] if b["key"]}
    prev = _blocks(previous)
    has_markers = any(b["marker"] for b in prev["blocks"])
    if not prev["blocks"] or (revision <= 1 and not has_markers):
        content = mark_flows(generated)
        if prev["sidecar"]:
            content = content.rstrip("\n") + "\n" + prev["sidecar"] + "\n"
        return content, {"regenerated": len(gen), "kept": 0, "stale": 0, "added": 0, "first_rebuild": True}
    out: list[list[str]] = []
    seen: set[str] = set()
    counts = {"regenerated": 0, "kept": 0, "stale": 0, "added": 0}
    kept_names: list[str] = []
    for b in prev["blocks"]:
        key = b["key"]
        untouched = bool(b["marker"]) and body_hash(b["lines"]) == b["marker"].group(2)
        if key and key in gen:
            seen.add(key)
            if untouched:
                new_lines = _with_descriptions(gen[key]["lines"], _descriptions(b["lines"]))
                sid = _screen_id(b["lines"][0])
                if sid and not _screen_id(new_lines[0]):
                    new_lines = [new_lines[0].rstrip() + sid, *new_lines[1:]]
                out.append([marker(key, gen[key]["lines"]), *new_lines])
                counts["regenerated"] += 1
                continue
            out.append(b["lines"])          # hand-made / edited: verbatim, no marker
            counts["kept"] += 1
            kept_names.append(b["lines"][0].strip()[:80])
            continue
        if untouched:                        # generated earlier, route gone now
            out.append([_stale(b["lines"][0]), *b["lines"][1:]])
            counts["stale"] += 1
            continue
        out.append(b["lines"])
        counts["kept"] += 1
        kept_names.append(b["lines"][0].strip()[:80])
    for key, b in gen.items():
        if key not in seen:
            out.append([marker(key, b["lines"]), *b["lines"]])
            counts["added"] += 1
    text = "\n".join(prev["head"]).strip("\n")
    chunks = ["\n".join(lines) for lines in out]
    content = ((text + "\n\n") if text else "") + "\n\n".join(chunks) + "\n"
    if prev["sidecar"]:
        content += prev["sidecar"] + "\n"
    return content, {**counts, "first_rebuild": False, "kept_screens": kept_names[:20]}


def summary_text(s: dict[str, Any]) -> str:
    return f"Interfaces: {s['regenerated']} regenerated, {s['added']} added, {s['kept']} kept (hand-made), {s['stale']} stale."
