"""Browser tool for the Development agent (off by default: setting dev_browser_enabled).

One Chrome at a time (dev_browser_daemon, lockfile + idle shutdown), always the
dedicated Decisions profile (~/.decisions/chrome-profile, sign in once with
`python -m distr.core.workflow.dev_browser_tools signin`), headless unless
headed=true. Guards: http(s) only; no private-network hosts except loopback dev
servers, and never the Decisions app itself; no typing into password fields;
clicking a submit / purchase-like control, or type with submit=true, needs a
user approval.
"""

from __future__ import annotations

import ipaddress
import json
import os
import re
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable, Optional
from urllib.parse import urlparse

from distr.core.workflow import dev_approvals
from distr.core.workflow import dev_browser_daemon as daemon

ACTIONS = ["open", "navigate", "click", "type", "extract", "screenshot", "close"]
RISKY_TEXT = re.compile(r"\b(buy|purchase|pay|checkout|check out|place order|order now|subscribe|donate|transfer|"
                        r"send|submit|confirm|delete|remove|sign up|book|reserve|publish|post)\b", re.I)

SCHEMA = {
    "description": (
        "Control one Chrome window (dedicated Decisions profile, already signed in where the user signed in). "
        "Actions: open/navigate(url), click(selector), type(selector, text, submit), extract(selector?, mode=text|links|html), "
        "screenshot, close. Headless unless headed=true. Never types into password fields. Clicking submit/purchase-like "
        "buttons or typing with submit=true needs the user's approval in the thread. Selectors are Playwright selectors "
        "(CSS, text=Sign in, role=button[name=...])."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ACTIONS},
            "url": {"type": "string"},
            "selector": {"type": "string"},
            "text": {"type": "string"},
            "submit": {"type": "boolean", "default": False},
            "mode": {"type": "string", "enum": ["text", "links", "html"], "default": "text"},
            "headed": {"type": "boolean", "default": False, "description": "show the window (only when the user asks)"},
            "approval_id": {"type": "string"},
        },
        "required": ["action"],
    },
}

# transport(request_dict, headed) -> response dict. Tests swap this.
Transport = Callable[[dict[str, Any], bool], dict[str, Any]]


def _blocked_ports() -> set[int]:
    ports = {8765}
    base = os.environ.get("DECISIONS_API_BASE") or ""
    try:
        if base and urlparse(base).port:
            ports.add(int(urlparse(base).port))
    except ValueError:
        pass
    return ports


def check_url(url: str) -> Optional[str]:
    """None if allowed, else the reason."""
    try:
        u = urlparse(url or "")
    except Exception:
        return "invalid URL"
    if u.scheme not in ("http", "https") or not u.hostname:
        return "only http(s) URLs are allowed"
    host = u.hostname
    try:
        infos = socket.getaddrinfo(host, None)
        addrs = {ipaddress.ip_address(i[4][0].split("%")[0]) for i in infos}
    except Exception:
        return f"cannot resolve {host}"
    port = u.port or (443 if u.scheme == "https" else 80)
    for a in addrs:
        if a.is_loopback:
            if port in _blocked_ports():
                return "the Decisions app itself cannot be driven by the agent browser"
            continue
        if a.is_private or a.is_link_local or a.is_reserved or a.is_multicast or a.is_unspecified:
            return f"private-network address {a} is not allowed"
    return None


def _running_mode() -> Optional[str]:
    try:
        txt = daemon.lock_path().read_text().split()
        return txt[1] if len(txt) > 1 and daemon.sock_path().exists() else None
    except OSError:
        return None


def _send(req: dict[str, Any], timeout: float = 60) -> dict[str, Any]:
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        s.connect(str(daemon.sock_path()))
        s.sendall((json.dumps(req) + "\n").encode())
        buf = b""
        while not buf.endswith(b"\n"):
            chunk = s.recv(1 << 20)
            if not chunk:
                break
            buf += chunk
        return json.loads(buf or b"{}")
    finally:
        s.close()


def _socket_transport(req: dict[str, Any], headed: bool) -> dict[str, Any]:
    mode = _running_mode()
    want = "headed" if headed else "headless"
    if mode and mode != want and req.get("cmd") != "close":
        return {"ok": False, "error": f"a {mode} browser is already running; call close first to switch to {want}"}
    if not mode:
        if req.get("cmd") == "close":
            return {"ok": True, "closed": True}
        args = [sys.executable, "-m", "distr.core.workflow.dev_browser_daemon"] + (["--headed"] if headed else [])
        subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=True, cwd=str(Path(__file__).resolve().parents[3]))
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if daemon.sock_path().exists():
                try:
                    if _send({"cmd": "ping"}, timeout=10).get("ok"):
                        break
                except OSError:
                    pass
            time.sleep(0.3)
        else:
            return {"ok": False, "error": "browser did not start (is Google Chrome installed?)"}
    return _send(req)


transport: Transport = _socket_transport


def _res(ok: bool, summary: str, **extra: Any) -> dict[str, Any]:
    return {"ok": ok, "tool": "browser", "summary": summary, "artifacts": [], **extra}


def _settings() -> dict[str, Any]:
    try:
        from distr.core.settings import load_settings_from_db
        return load_settings_from_db() or {}
    except Exception:
        return {}


def browser(action: str, url: str | None = None, selector: str | None = None, text: str | None = None,
            submit: bool | None = False, mode: str | None = "text", headed: bool | None = False,
            approval_id: str | None = None, *, project_folder: str | None = None,
            settings: dict[str, Any] | None = None, **_: Any) -> dict[str, Any]:
    s = settings if settings is not None else _settings()
    if not s.get("dev_browser_enabled"):
        return _res(False, "The browser tool is off. Turn on 'Browser' in Settings > Third party > Development agent tools.")
    if action not in ACTIONS:
        return _res(False, f"unknown action {action!r}; use one of {ACTIONS}")
    headed = bool(headed)
    if action in ("open", "navigate"):
        why = check_url(url or "")
        if why:
            return _res(False, f"Not allowed: {why}")
        r = transport({"cmd": "open", "url": url}, headed)
        return _res(bool(r.get("ok")), f"opened {r.get('url')} ({r.get('title')})" if r.get("ok") else r.get("error", "failed"),
                    **{k: v for k, v in r.items() if k != "ok"})
    if action == "close":
        r = transport({"cmd": "close"}, headed)
        return _res(True, "browser closed")
    if action in ("click", "type") and not selector:
        return _res(False, f"{action} needs selector")
    if action == "click":
        d = transport({"cmd": "describe", "selector": selector}, headed)
        if not d.get("ok"):
            return _res(False, d.get("error", "element not found"))
        el = d["element"]
        risky = bool(el.get("is_submit")) or bool(RISKY_TEXT.search(el.get("text") or ""))
        if risky:
            page = transport({"cmd": "ping"}, headed).get("url")
            blocked = dev_approvals.gate("browser", "click", {"selector": selector, "page": page}, approval_id,
                                         f"click '{(el.get('text') or selector)[:80]}' on {page}",
                                         {"page": page, "element": el})
            if blocked:
                return blocked
        r = transport({"cmd": "click", "selector": selector}, headed)
        return _res(bool(r.get("ok")), f"clicked; now at {r.get('url')}" if r.get("ok") else r.get("error", "failed"),
                    **{k: v for k, v in r.items() if k != "ok"})
    if action == "type":
        d = transport({"cmd": "describe", "selector": selector}, headed)
        if not d.get("ok"):
            return _res(False, d.get("error", "element not found"))
        if d["element"].get("is_password"):
            return _res(False, "Refusing to type into a password / one-time-code field. Ask the user to sign in "
                               "(headed=true) or use the sign-in helper.")
        if submit:
            page = transport({"cmd": "ping"}, headed).get("url")
            blocked = dev_approvals.gate("browser", "submit", {"selector": selector, "text": text, "page": page},
                                         approval_id, f"type and submit on {page}",
                                         {"page": page, "field": d["element"], "text": (text or "")[:200]})
            if blocked:
                return blocked
        r = transport({"cmd": "type", "selector": selector, "text": text or "", "submit": bool(submit)}, headed)
        return _res(bool(r.get("ok")), "typed" + (" and submitted" if submit else "") if r.get("ok") else r.get("error", "failed"))
    if action == "extract":
        r = transport({"cmd": "extract", "selector": selector, "mode": mode or "text"}, headed)
        return _res(bool(r.get("ok")), f"extracted {mode} from {r.get('url')}" if r.get("ok") else r.get("error", "failed"),
                    **{k: v for k, v in r.items() if k != "ok"})
    # screenshot
    from distr.core.workflow.dev_agent_tools import artifacts_dir
    path = artifacts_dir(project_folder) / f"browser-{time.strftime('%Y%m%d-%H%M%S')}.png"
    r = transport({"cmd": "screenshot", "path": str(path)}, headed)
    if not r.get("ok"):
        return _res(False, r.get("error", "screenshot failed"))
    return _res(True, f"screenshot saved to {path}",
                artifacts=[{"path": str(path), "mime": "image/png", "kind": "image", "bytes": path.stat().st_size}])


def signin(url: str = "https://accounts.google.com/") -> int:
    """Open the dedicated Decisions profile visibly so the user can sign in once. Blocks until closed."""
    if _running_mode():
        try:
            _send({"cmd": "close"}, timeout=10)
        except OSError:
            pass
        time.sleep(1)
    import fcntl
    lock = open(daemon.lock_path(), "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        print("the Decisions browser is busy; try again", file=sys.stderr)
        return 3
    lock.write(f"{os.getpid()} signin\n")
    lock.flush()
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        ctx = pw.chromium.launch_persistent_context(str(daemon.profile_dir()), channel="chrome", headless=False,
                                                    args=["--no-first-run", "--no-default-browser-check"])
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto(url)
        try:
            while ctx.pages:
                ctx.pages[0].wait_for_event("close", timeout=0)
        except Exception:
            pass
        try:
            ctx.close()
        except Exception:
            pass
    lock.close()
    return 0


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "signin":
        raise SystemExit(signin(*(sys.argv[2:3] or [])))
    print(json.dumps({"usage": "python -m distr.core.workflow.dev_browser_tools signin [url]"}))
