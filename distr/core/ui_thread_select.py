"""select_thread / open_thread: point the web UI's left panel at one thread.

Shared by the Oracle tools (agent/tools/chat/select_thread.py) and the
development agent toolset (workflow/dev_agent_tools.py -> Pi/Claude/Codex/Cursor MCP).

* resolve  : 'latest' | numeric id | title query -> one root thread (parent_id NULL),
             from the same /api/chats list the sidebars render.
* select   : POST /api/events/ui/select-thread. The server pushes ui.thread.select over
             the SSE stream; open Chat/Development tabs select + highlight the row and ack.
             The server waits ~1.5s and returns the ack count.
* open     : open /development/threads/<id>/ or /chat/?id=<id> in the DEFAULT browser
             (macOS `open`, never the dev Chrome profile). An already-open tab is
             reused (select + focus) instead of opening a duplicate.
select_thread falls back to open by itself when no tab acks.
Selecting never loads a chat into the Oracle's live agent (no load-in-agent).
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import urllib.parse
import urllib.request
from typing import Any, Callable, Optional

SURFACES = ("development", "chat")
ACK_TIMEOUT_S = 1.5
_LATEST_RE = re.compile(r"^(?:the\s+)?(?:latest|newest|most\s+recent|last|recent|current|top)(?:\s+(?:one|thread|chat))?$", re.I)


def norm_surface(surface: Optional[str]) -> str:
    s = str(surface or "").strip().lower()
    if s in {"chat", "chats", "oracle", "conversation", "conversations"}:
        return "chat"
    return "development"


def base_url() -> Optional[str]:
    try:
        from distr.gui.web.server import get_unified_server
        server = get_unified_server()
        if server and server.is_ready():
            return server.get_url()
    except Exception:
        pass
    for port in range(8765, 8781):
        base = f"http://127.0.0.1:{port}"
        try:
            with urllib.request.urlopen(f"{base}/health", timeout=0.25) as resp:
                if resp.status == 200:
                    return base
        except Exception:
            continue
    return None


def _token_headers() -> dict[str, str]:
    from distr.gui.web.security import INTERNAL_AUTH_HEADER, get_internal_api_token
    return {INTERNAL_AUTH_HEADER: get_internal_api_token(), "Content-Type": "application/json"}


def fetch_threads(surface: str, base: str) -> list[dict[str, Any]]:
    url = f"{base}/api/chats?surface={urllib.parse.quote(surface)}"
    req = urllib.request.Request(url, headers=_token_headers())
    with urllib.request.urlopen(req, timeout=5) as resp:
        data = json.loads(resp.read().decode("utf-8") or "{}")
    rows = data.get("chats", data) if isinstance(data, dict) else data
    return [r for r in rows or [] if isinstance(r, dict) and r.get("parent_id") is None]


def resolve_thread(thread: Any, threads: list[dict[str, Any]]) -> Optional[dict[str, Any]]:
    """Pick one thread. 'latest' = most recently updated (modified_date, then id)."""
    if not threads:
        return None
    q = str(thread if thread is not None else "latest").strip()
    q = re.sub(r"^(?:thread|chat|#)\s*", "", q, flags=re.I).strip() or "latest"
    if q.isdigit():
        want = int(q)
        return next((t for t in threads if int(t.get("id") or 0) == want), None)
    if _LATEST_RE.match(q):
        return max(threads, key=lambda t: (str(t.get("modified_date") or t.get("created_date") or ""), int(t.get("id") or 0)))
    ql = q.lower()
    words = [w for w in re.findall(r"[a-z0-9]+", ql) if w not in {"the", "a", "about", "thread", "chat", "on", "for"}]
    best, best_score = None, 0.0
    for t in threads:
        title = str(t.get("title") or "").lower()
        if ql and ql in title:
            score = 2.0
        elif words:
            score = sum(1 for w in words if w in title) / len(words)
        else:
            score = 0.0
        if score > best_score or (score == best_score and best is not None and score > 0
                                  and str(t.get("modified_date") or "") > str(best.get("modified_date") or "")):
            best, best_score = t, score
    return best if best_score >= 0.5 else None


def thread_path(surface: str, thread_id: int) -> str:
    return f"/development/threads/{int(thread_id)}/" if surface == "development" else f"/chat/?id={int(thread_id)}"


def emit_select(surface: str, thread_id: int, base: str, *, title: str = "", focus: bool = False,
                timeout: float = ACK_TIMEOUT_S) -> dict[str, Any]:
    body = json.dumps({"surface": surface, "thread_id": int(thread_id), "title": title,
                       "focus": bool(focus), "timeout": timeout}).encode()
    req = urllib.request.Request(f"{base}/api/events/ui/select-thread", data=body, headers=_token_headers(), method="POST")
    with urllib.request.urlopen(req, timeout=timeout + 5) as resp:
        return json.loads(resp.read().decode("utf-8") or "{}")


def default_browser_open(url: str) -> bool:
    """Default browser only. On macOS `open <url>` uses the system default handler."""
    if sys.platform == "darwin":
        return subprocess.run(["/usr/bin/open", url], check=False, timeout=10).returncode == 0
    import webbrowser
    return bool(webbrowser.open(url))


def _resolve(thread: Any, surface: str, base: str, fetch: Callable) -> tuple[Optional[dict], str]:
    try:
        threads = fetch(surface, base)
    except Exception as exc:
        return None, f"could not list {surface} threads: {exc}"
    hit = resolve_thread(thread, threads)
    if hit is None:
        return None, f"no {surface} thread matches '{thread}'"
    return hit, ""


def _result(ok: bool, path: str, surface: str, hit: Optional[dict], summary: str, **extra: Any) -> dict[str, Any]:
    out = {"ok": ok, "path": path, "surface": surface, "summary": summary}
    if hit:
        out.update(thread_id=int(hit["id"]), title=hit.get("title") or "")
    out.update(extra)
    return out


def open_thread(thread: Any = "latest", surface: Optional[str] = None, *, base: Optional[str] = None,
                fetch: Callable = fetch_threads, emit: Callable = emit_select,
                opener: Callable[[str], bool] = default_browser_open, _hit: Optional[dict] = None) -> dict[str, Any]:
    surface = norm_surface(surface)
    base = base or base_url()
    if not base:
        return _result(False, "none", surface, None, "The Decisions web server is not running.")
    hit = _hit
    if hit is None:
        hit, err = _resolve(thread, surface, base, fetch)
        if hit is None:
            return _result(False, "none", surface, None, err)
    tid, title = int(hit["id"]), hit.get("title") or ""
    url = f"{base}{thread_path(surface, tid)}"
    if _hit is None:  # reuse an open tab instead of a duplicate
        try:
            acks = int((emit(surface, tid, base, title=title, focus=True) or {}).get("acked") or 0)
        except Exception:
            acks = 0
        if acks:
            return _result(True, "selected", surface, hit, f"Selected {surface} thread {tid} '{title}' in the open tab.", url=url, acked=acks)
    try:
        accepted = bool(opener(url))
    except Exception as exc:
        return _result(False, "none", surface, hit, f"could not open browser: {exc}", url=url)
    return _result(accepted, "opened", surface, hit, f"Opened {surface} thread {tid} '{title}' in the browser.", url=url)


def select_thread(thread: Any = "latest", surface: Optional[str] = None, *, base: Optional[str] = None,
                  fetch: Callable = fetch_threads, emit: Callable = emit_select,
                  opener: Callable[[str], bool] = default_browser_open) -> dict[str, Any]:
    surface = norm_surface(surface)
    base = base or base_url()
    if not base:
        return _result(False, "none", surface, None, "The Decisions web server is not running.")
    hit, err = _resolve(thread, surface, base, fetch)
    if hit is None:
        return _result(False, "none", surface, None, err)
    tid, title = int(hit["id"]), hit.get("title") or ""
    try:
        acks = int((emit(surface, tid, base, title=title) or {}).get("acked") or 0)
    except Exception:
        acks = 0
    if acks:
        return _result(True, "selected", surface, hit,
                       f"Selected {surface} thread {tid} '{title}' in {acks} open tab(s).", acked=acks)
    res = open_thread(thread, surface, base=base, fetch=fetch, emit=emit, opener=opener, _hit=hit)
    res["summary"] = "No open web UI tab acknowledged the selection, so I opened it. " + res["summary"]
    return res


def spoken(result: dict[str, Any]) -> str:
    if not result.get("ok"):
        return f"Couldn't do that: {result.get('summary')}"
    t = result.get("title") or f"thread {result.get('thread_id')}"
    verb = "Selected" if result.get("path") == "selected" else "Opened"
    where = "Chat" if result.get("surface") == "chat" else "Development"
    return f"{verb} '{t}' in {where}. (path: {result.get('path')})"
