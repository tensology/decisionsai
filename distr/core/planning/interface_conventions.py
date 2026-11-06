"""Interfaces conventions shared by the AI chat, the project scan and ticket building (N6 phase 3).

Two comment conventions live inside Interface items (both are skipped by the wire parser, the renderer,
the server validator and Mermaid):
- ``#|`` page description lines directly under a ``screen`` line (markdown);
- one trailing sidecar line ``# decisions-flow: {json}`` (wire) / ``%% decisions-flow: {json}`` (Mermaid).

Pure functions, no DB access, no LLM. Deterministic output for identical input.
"""
from __future__ import annotations

import json
import re
from typing import Any, Iterable

INTERFACE_TYPES = ("wireframe", "flows")
SIDECAR_PREFIX = {"wire": "# decisions-flow:", "mermaid": "%% decisions-flow:"}
_DESC = re.compile(r"^(\s*)#\|(?: ?)(.*)$")
_SCREEN = re.compile(r"^(\s*)screen\b(.*)$")
_ID = re.compile(r"(?:^|\s)id=([A-Za-z][\w-]{0,63})(?=\s|$)")
_ROUTE = re.compile(r'(?:^|\s)route=(?:"((?:[^"\\]|\\.)*)"|(\S+))')
_LABEL = re.compile(r'^\s*"((?:[^"\\]|\\.)*)"')
BLOCK_HEADER = "Interface context (generated):"
BLOCK_SEPARATOR = "\n\n---\n" + BLOCK_HEADER
SHAPE_WORD = {"diamond": "decision"}
DESC_CHARS, NOTE_CHARS, BLOCK_CHARS, DESCRIPTION_LIMIT = 300, 120, 2000, 20000


def _is_sidecar(line: str, fmt: str) -> bool:
    prefix = SIDECAR_PREFIX.get(fmt)
    return bool(prefix) and line.strip().startswith(prefix)


def screens(content: str) -> list[dict[str, Any]]:
    """Every wire screen line with its identity (id / route / label) and its ``#|`` run."""
    lines = str(content or "").split("\n")
    out = []
    for index, line in enumerate(lines):
        m = _SCREEN.match(line)
        if not m:
            continue
        rest = m.group(2)
        route = _ROUTE.search(rest)
        label = _LABEL.match(rest)
        desc, j = [], index + 1
        while j < len(lines) and _DESC.match(lines[j]):
            desc.append(_DESC.match(lines[j]).group(2))
            j += 1
        sid = _ID.search(rest)
        out.append({"index": index, "indent": m.group(1), "id": sid.group(1) if sid else None,
                    "route": (route.group(1) if route.group(1) is not None else route.group(2)) if route else None,
                    "label": label.group(1) if label else None, "desc": desc})
    return out


def sidecar_line(content: str, fmt: str) -> str | None:
    lines = [line for line in str(content or "").split("\n") if line.strip()]
    return lines[-1].strip() if lines and _is_sidecar(lines[-1], fmt) else None


def sidecar_nodes(content: str, fmt: str) -> dict[str, dict]:
    line = sidecar_line(content, fmt)
    if not line:
        return {}
    try:
        data = json.loads(line[len(SIDECAR_PREFIX[fmt]):].strip())
    except ValueError:
        return {}
    nodes = data.get("nodes") if isinstance(data, dict) else None
    return {str(k): v for k, v in nodes.items() if isinstance(v, dict)} if isinstance(nodes, dict) else {}


def has_annotations(content: str, fmt: str) -> bool:
    """True when an item carries hand-made Interface annotations (a ``#|`` line or a sidecar)."""
    text = str(content or "")
    if fmt == "wire" and any(_DESC.match(line) for line in text.split("\n")):
        return True
    return fmt in SIDECAR_PREFIX and any(_is_sidecar(line, fmt) for line in text.split("\n"))


_MERMAID_TOKEN = re.compile(r"[A-Za-z][\w-]{0,63}")


def node_ids(content: str, fmt: str) -> set[str]:
    if fmt == "wire":
        return {s["id"] for s in screens(content) if s["id"]}
    ids = set()
    for line in str(content or "").split("\n"):
        t = line.strip()
        if not t or t.startswith("%%"):
            continue
        t = re.sub(r"\|[^|]*\||\[[^\]]*\]|\([^)]*\)|\{[^}]*\}|\"[^\"]*\"", " ", t)
        ids.update(_MERMAID_TOKEN.findall(t))
    return ids


def _match(screen: dict, candidates: list[dict], used: set[int]) -> dict | None:
    for key in ("id", "route", "label"):
        value = screen.get(key)
        if not value:
            continue
        for cand in candidates:
            if cand["index"] not in used and cand.get(key) == value:
                return cand
    return None


def carry_annotations(previous: str, incoming: str, fmt: str) -> tuple[str, list[str]]:
    """Re-attach the previous version's annotations that ``incoming`` dropped.

    - wire: for every incoming screen that matches a previous screen (id, then route, then label), the
      previous ``id=`` is restored when missing (sidecar keys depend on it) and the previous ``#|`` run is
      re-inserted when the incoming screen has none. An incoming ``#|`` run is kept as written.
    - sidecar: re-attached as the last line when ``incoming`` has none; an incoming sidecar is kept.
      Keys for screens that no longer exist are KEPT and reported as warnings, never deleted.
    Returns (content, warnings). Unchanged input gives unchanged output.
    """
    previous, incoming = str(previous or ""), str(incoming or "")
    warnings: list[str] = []
    out = incoming
    if fmt == "wire":
        old = screens(previous)
        lines = incoming.split("\n")
        new = screens(incoming)
        used: set[int] = set()
        new_ids = {s["id"] for s in new if s["id"]}
        inserts: dict[int, tuple[str, list[str]]] = {}
        for screen in new:
            match = _match(screen, old, used)
            if match is None:
                continue
            used.add(match["index"])
            line = lines[screen["index"]]
            if match["id"] and not screen["id"] and match["id"] not in new_ids:
                m = _SCREEN.match(line)
                rest = m.group(2)
                label = _LABEL.match(rest)
                if label:
                    rest = rest[:label.end()] + f" id={match['id']}" + rest[label.end():]
                else:
                    rest = f"{rest.rstrip()} id={match['id']}"
                line = f"{m.group(1)}screen{rest}"
                new_ids.add(match["id"])
            desc = match["desc"] if match["desc"] and not screen["desc"] else []
            inserts[screen["index"]] = (line, desc)
        result = []
        for index, line in enumerate(lines):
            if index in inserts:
                line, desc = inserts[index]
                result.append(line)
                indent = _SCREEN.match(line).group(1) + "  "
                result.extend(f"{indent}#| {d}" if d else f"{indent}#|" for d in desc)
            else:
                result.append(line)
        out = "\n".join(result)
        for screen in old:
            if screen["desc"] and screen["index"] not in used:
                name = screen["id"] or screen["route"] or screen["label"] or "page"
                warnings.append(f'The description of page "{name}" was not carried over: the page no longer exists.')
    prefix = SIDECAR_PREFIX.get(fmt)
    if prefix:
        old_side, new_side = sidecar_line(previous, fmt), sidecar_line(out, fmt)
        if old_side and not new_side:
            body = [line for line in out.split("\n") if not _is_sidecar(line, fmt)]
            while body and not body[-1].strip():
                body.pop()
            out = "\n".join(body) + f"\n{old_side}\n"
        present = node_ids(out, fmt)
        for key in sorted(sidecar_nodes(out, fmt)):
            if key not in present:
                warnings.append(f'Flow metadata for "{key}" has no matching node; it was kept.')
    return out, warnings


# ---- Ticket context block (deterministic, no LLM) ------------------------------------------------------
def _plain(text: str) -> str:
    text = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"(\*\*|__|\*|_|`|~~)", "", text)
    text = re.sub(r"^\s{0,3}(#{1,6}\s+|[-*+]\s+|\d+[.)]\s+|>\s?)", "", text, flags=re.M)
    text = re.sub(r"<[^>]{0,200}>", "", text)
    return re.sub(r"\s+", " ", text).strip()


def _cut(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    cut = text[:limit]
    space = cut.rfind(" ")
    if space >= limit // 2:
        cut = cut[:space]
    return cut.rstrip(" ,;:.-") + "…"


def interface_context(items: Iterable[dict]) -> str:
    """The fixed block appended to build-task descriptions. ``items`` are PlanItem-like dicts in source order."""
    lines: list[str] = []
    for item in items:
        if item.get("item_type") not in INTERFACE_TYPES:
            continue
        fmt, content = item.get("content_format"), str(item.get("content") or "")
        if fmt not in SIDECAR_PREFIX:
            continue
        nodes = sidecar_nodes(content, fmt)
        present = node_ids(content, fmt)
        node_parts = []
        if fmt == "wire":
            for screen in screens(content):
                if screen["desc"]:
                    name = _plain(screen["label"] or screen["id"] or "Page")[:80]
                    route = f" ({screen['route']})" if screen["route"] else ""
                    lines.append(f'Page "{name}"{route}: {_cut(_plain(" ".join(screen["desc"])), DESC_CHARS)}')
            order = [s["id"] for s in screens(content) if s["id"]]
        else:
            order = sorted(present)
        for key in order:
            meta = nodes.get(key)
            if not meta or key not in present:
                continue                                                     # orphan keys are skipped
            shape = meta.get("shape") if fmt == "wire" else None
            note = _cut(_plain(str(meta.get("note") or "")), NOTE_CHARS)
            if not note and not shape:
                continue
            kind = SHAPE_WORD.get(shape, shape or "page") if fmt == "wire" else "node"
            node_parts.append(f"{key} ({kind})" + (f" – {note}" if note else ""))
        if node_parts:
            lines.append("Nodes: " + "; ".join(node_parts))
    if not lines:
        return ""
    block = BLOCK_HEADER + "\n" + "\n".join(lines)
    if len(block) > BLOCK_CHARS:
        kept = block[:BLOCK_CHARS - 1]
        kept = kept[:kept.rfind("\n")] if "\n" in kept[len(BLOCK_HEADER):] else kept
        block = kept + "\n…"
    return block


def with_interface_context(description: str, block: str) -> str:
    """Strip any earlier generated block, then append ``block``; clamp to the 20,000-char task limit."""
    base = str(description or "").split(BLOCK_SEPARATOR, 1)[0].rstrip()
    if not block:
        return base if BLOCK_SEPARATOR in str(description or "") else str(description or "")
    room = DESCRIPTION_LIMIT - len(BLOCK_SEPARATOR) - len(block) + len(BLOCK_HEADER) - 1
    return f"{base[:max(0, room)]}\n\n---\n{block}"
