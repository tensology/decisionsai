"""Single headless/headed Chrome for the Development agent (one at a time).

Started on demand by dev_browser_tools; holds an exclusive lockfile, serves
newline-delimited JSON over a private Unix socket, and exits after IDLE_S
seconds without requests. Uses ONLY the dedicated Decisions profile folder
(~/.decisions/chrome-profile), never the user's main Chrome profile.
"""

from __future__ import annotations

import fcntl
import json
import os
import socket
import sys
from pathlib import Path
from typing import Any

IDLE_S = int(os.environ.get("DECISIONS_BROWSER_IDLE_S", "120"))
MAX_TEXT = 20000


def state_dir() -> Path:
    d = Path(os.path.expanduser("~")) / ".decisions"
    d.mkdir(parents=True, exist_ok=True, mode=0o700)
    return d


def profile_dir() -> Path:
    p = (state_dir() / "chrome-profile").resolve()
    main = (Path(os.path.expanduser("~")) / "Library" / "Application Support" / "Google" / "Chrome").resolve()
    if p == main or main in p.parents:
        raise RuntimeError("refusing to use the main Chrome profile")
    p.mkdir(parents=True, exist_ok=True, mode=0o700)
    return p


def sock_path() -> Path:
    return state_dir() / "browser.sock"


def lock_path() -> Path:
    return state_dir() / "browser.lock"


def _describe(page, selector: str) -> dict[str, Any]:
    loc = page.locator(selector).first
    return loc.evaluate("""(el) => {
        const form = el.closest('form');
        const type = (el.getAttribute('type') || '').toLowerCase();
        const tag = el.tagName.toLowerCase();
        const ac = (el.getAttribute('autocomplete') || '').toLowerCase();
        return {
          tag, type, text: (el.innerText || el.value || el.getAttribute('aria-label') || '').slice(0, 200),
          in_form: !!form,
          is_submit: type === 'submit' || (tag === 'button' && !!form && (type === '' || type === 'submit')),
          is_password: type === 'password' || ac.includes('password') || ac.includes('one-time-code'),
        };
    }""")


def _handle(page, req: dict[str, Any]) -> dict[str, Any]:
    cmd = req.get("cmd")
    if cmd in ("open", "navigate"):
        resp = page.goto(req["url"], wait_until="domcontentloaded", timeout=30000)
        return {"ok": True, "url": page.url, "title": page.title(), "status": resp.status if resp else None}
    if cmd == "describe":
        return {"ok": True, "element": _describe(page, req["selector"])}
    if cmd == "click":
        page.locator(req["selector"]).first.click(timeout=15000)
        page.wait_for_load_state("domcontentloaded", timeout=15000)
        return {"ok": True, "url": page.url, "title": page.title()}
    if cmd == "type":
        info = _describe(page, req["selector"])
        if info.get("is_password"):
            return {"ok": False, "error": "refusing to type into a password / one-time-code field"}
        loc = page.locator(req["selector"]).first
        loc.fill(req.get("text") or "", timeout=15000)
        if req.get("submit"):
            loc.press("Enter")
            page.wait_for_load_state("domcontentloaded", timeout=15000)
        return {"ok": True, "url": page.url}
    if cmd == "extract":
        mode = req.get("mode") or "text"
        sel = req.get("selector") or "body"
        loc = page.locator(sel).first
        if mode == "links":
            links = page.eval_on_selector_all(f"{sel} a[href]", "els => els.slice(0, 200).map(a => ({text: (a.innerText||'').trim().slice(0,120), href: a.href}))")
            return {"ok": True, "url": page.url, "links": links}
        text = loc.inner_html() if mode == "html" else loc.inner_text()
        return {"ok": True, "url": page.url, "title": page.title(), "content": text[:MAX_TEXT], "truncated": len(text) > MAX_TEXT}
    if cmd == "screenshot":
        page.screenshot(path=req["path"], full_page=False)
        return {"ok": True, "path": req["path"]}
    if cmd == "ping":
        return {"ok": True, "url": page.url}
    return {"ok": False, "error": f"unknown cmd {cmd}"}


def serve(headless: bool) -> int:
    lock = open(lock_path(), "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        print("another Decisions browser is already running", file=sys.stderr)
        return 3
    lock.write(f"{os.getpid()} {'headless' if headless else 'headed'}\n")
    lock.flush()
    sp = sock_path()
    sp.unlink(missing_ok=True)
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(str(sp))
    os.chmod(sp, 0o600)
    srv.listen(4)
    srv.settimeout(IDLE_S)
    from playwright.sync_api import sync_playwright
    try:
        with sync_playwright() as pw:
            ctx = pw.chromium.launch_persistent_context(
                str(profile_dir()), channel="chrome", headless=headless,
                args=["--disable-background-networking", "--no-first-run", "--no-default-browser-check"],
                viewport={"width": 1280, "height": 860})
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            while True:
                try:
                    conn, _ = srv.accept()
                except socket.timeout:
                    break  # idle shutdown
                with conn:
                    conn.settimeout(60)
                    buf = b""
                    while not buf.endswith(b"\n"):
                        chunk = conn.recv(65536)
                        if not chunk:
                            break
                        buf += chunk
                    try:
                        req = json.loads(buf or b"{}")
                    except Exception:
                        req = {}
                    if req.get("cmd") == "close":
                        conn.sendall(b'{"ok": true, "closed": true}\n')
                        break
                    try:
                        if page.is_closed():
                            page = ctx.new_page()
                        out = _handle(page, req)
                    except Exception as exc:
                        out = {"ok": False, "error": f"{type(exc).__name__}: {str(exc)[:300]}"}
                    conn.sendall((json.dumps(out, default=str) + "\n").encode())
            ctx.close()
    finally:
        srv.close()
        sp.unlink(missing_ok=True)
        lock.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(serve(headless="--headed" not in sys.argv))
