"""Page screenshots and pinned map positions for the Interfaces map.

Images and layout JSON live in the linked project's planning/ folder (the same
folder as the plan documents). Older captures in /.planning/ are still read.
When the project's startup terminals are up and they published a local web
address, a missing or stale page is captured from that local URL. If no local
address is available, the project's production host is used when one is known.
A Python or desktop project with no local or production URL is left alone.
This does not scan the project tree and does not crawl any host the project
did not already publish or that is not the project's own production host.
"""
from __future__ import annotations

import json
import logging
import os
import re
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

_SCREEN = re.compile(r"^[A-Za-z][\w-]{0,63}$")
_ROUTE = re.compile(r"^/[A-Za-z0-9._~/-]*$")
_URL = re.compile(r"https?://[^\s\"'<>]+")
_HOST = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)+$")
_IMAGE = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}
_FRONT = ("frontend", "vite", "ng serve", "npm run dev", "yarn dev", "pnpm dev", "bun dev", "next dev")
_BACK = ("backend", "runserver", "uvicorn", "gunicorn", "manage.py", "django", "flask", "fastapi")
_LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}
_LAYOUT_FILE = "iface-layout.json"
# Decisions serves itself here, so its own plan can be captured without a terminal.
_SELF_APP_URL = "http://127.0.0.1:8765"
_SELF_ROOT = Path(__file__).resolve().parents[3]
# A page that could not be captured is not retried on every plan open.
_ATTEMPT_FILE = Path.home() / ".decisions" / "cache" / "plan-shot-attempts.json"
_RETRY_SECONDS = 24 * 60 * 60

TERMINALS_DOWN = (
    "Images cannot be fetched because the terminals are down. "
    "If they were up, this could show the frontend and the backend."
)
NO_WEB = (
    "The terminals are up, but this project did not publish a web address. "
    "A Python or desktop interface has no page to capture."
)


def concrete_route(route: str) -> str:
    """A path this process may open. Empty when the route is not a concrete local page."""
    value = str(route or "").strip()
    if not _ROUTE.fullmatch(value) or ".." in value:
        return ""
    return value


def _host(value: str) -> str:
    return (urlparse(value).hostname or "").lower().rstrip(".")


def _clean_url(value, allowed_hosts: set[str] | None = None) -> str:
    raw = str(value or "").strip().rstrip(".,;)")
    if not raw:
        return ""
    if "0.0.0.0" in raw:
        raw = raw.replace("0.0.0.0", "127.0.0.1", 1)
    parsed = urlparse(raw)
    host = (parsed.hostname or "").lower().rstrip(".")
    if parsed.scheme not in {"http", "https"} or not host:
        return ""
    allowed = _LOCAL_HOSTS | {h.lower().rstrip(".") for h in (allowed_hosts or set()) if h}
    if host not in allowed:
        return ""
    return raw.rstrip("/")


def _kind(command: str) -> str:
    text = str(command or "").lower()
    front = any(token in text for token in _FRONT)
    back = any(token in text for token in _BACK)
    if front and not back:
        return "frontend"
    if back and not front:
        return "backend"
    return ""


def _session_urls(session: dict, allowed_hosts: set[str] | None = None) -> list[str]:
    found = []
    for item in session.get("urls") or []:
        raw = item.get("url") if isinstance(item, dict) else item
        url = _clean_url(raw, allowed_hosts)
        if url and url not in found and _host(url) in _LOCAL_HOSTS:
            found.append(url)
    return found


def _instruction_urls(startup_instructions: str, allowed_hosts: set[str] | None = None) -> list[tuple[str, str]]:
    found = []
    for line in str(startup_instructions or "").splitlines():
        kind = _kind(line)
        for match in _URL.findall(line):
            url = _clean_url(match, allowed_hosts)
            if url and _host(url) in _LOCAL_HOSTS:
                found.append((kind, url))
    return found


def production_url(folder: str, startup_instructions: str = "") -> str:
    """The project's own production origin, or empty.

    Prefers a folder name that is already a hostname (for example
    www.merrypak.co.za). Otherwise uses a non-local http(s) URL written in the
    startup instructions. Never invents a host from a generic folder name.
    """
    name = Path(str(folder or "")).name.strip().lower().rstrip(".")
    if name.startswith("www."):
        name = name  # already a host-looking name
    if _HOST.fullmatch(name):
        return f"https://{name}"
    for match in _URL.findall(str(startup_instructions or "")):
        cleaned = str(match).strip().rstrip(".,;)")
        host = _host(cleaned)
        if urlparse(cleaned).scheme in {"http", "https"} and host and host not in _LOCAL_HOSTS and _HOST.fullmatch(host):
            return cleaned.rstrip("/")
    return ""


def describe_targets(running: bool, sessions: list | None, startup_instructions: str = "",
                     production: str = "") -> dict:
    """Frontend and backend addresses from live terminals, then startup instructions.

    Instruction URLs are used only when the terminals are already up. They never
    invent a host the project did not write down. production is the project's
    own production origin and is never used as a local terminal address.
    """
    prod = _clean_url(production, {_host(production)} if production else None)
    if prod and _host(prod) in _LOCAL_HOSTS:
        prod = ""
    if not running:
        return {
            "terminals_up": False, "frontend_url": "", "backend_url": "",
            "production_url": prod, "reason": "" if prod else TERMINALS_DOWN,
        }
    front, back = "", ""
    for session in sessions or []:
        urls = _session_urls(session if isinstance(session, dict) else {})
        if not urls:
            continue
        kind = _kind(str(session.get("command") or ""))
        url = urls[0]
        if kind == "backend" and not back:
            back = url
        elif kind == "frontend" and not front:
            front = url
        elif not kind:
            if not front:
                front = url
            elif not back and url != front:
                back = url
    if not front or not back:
        for kind, url in _instruction_urls(startup_instructions):
            if kind == "backend" and not back:
                back = url
            elif kind == "frontend" and not front:
                front = url
            elif not kind and not front:
                front = url
            elif not kind and not back and url != front:
                back = url
    if not front and not back and not prod:
        return {"terminals_up": True, "frontend_url": "", "backend_url": "",
                "production_url": "", "reason": NO_WEB}
    reason = ""
    if not front and not back and prod:
        reason = ""
    return {"terminals_up": True, "frontend_url": front, "backend_url": back,
            "production_url": prod, "reason": reason}


def capture_plan(targets: dict, *, lane: str, route: str, saved: bool, stale: bool = False, force: bool = False) -> dict:
    """Choose read, grab, or skip. Does not launch a browser.

    Prefers a saved image on disk. A hash mismatch (stale=True) does not
    recapture; only force=True (Rescan images) overwrites an existing file.
    """
    if saved and not force:
        return {"action": "read", "url": "", "reason": ""}
    if lane not in {"frontend", "backend"}:
        return {"action": "skip", "url": "", "reason": "This is not a page capture."}
    path = concrete_route(route)
    if not path:
        return {"action": "skip", "url": "", "reason": "This route has no concrete page to open."}
    base = targets.get("frontend_url") if lane == "frontend" else targets.get("backend_url")
    if base:
        return {"action": "grab", "url": str(base).rstrip("/") + path, "reason": ""}
    prod = str(targets.get("production_url") or "").rstrip("/")
    if prod:
        return {"action": "grab", "url": prod + path, "reason": ""}
    if not targets.get("terminals_up"):
        return {"action": "skip", "url": "", "reason": targets.get("reason") or TERMINALS_DOWN}
    which = "frontend" if lane == "frontend" else "backend"
    return {"action": "skip", "url": "", "reason": targets.get("reason") or f"No {which} address is published in the running terminals."}


def _load(workspace_id: int):
    from distr.core.db import get_session
    from distr.core.db.projects import Project
    from distr.core.planning.service import get_workspace

    workspace = get_workspace(int(workspace_id))
    if workspace is None:
        raise LookupError("Plan workspace not found.")
    project_id = workspace.get("project_id")
    if type(project_id) is not int or project_id <= 0:
        raise ValueError("Select a project before using the planning CLI.")
    with get_session() as session:
        project = session.get(Project, project_id)
    if project is None:
        raise LookupError("Project not found.")
    return workspace, project


def _planning_dir(folder: str, root_path: str = "") -> Path:
    if str(root_path or "").strip():
        planning = Path(root_path).expanduser().resolve()
        root = Path(folder).expanduser().resolve() if str(folder or "").strip() else planning.parent
        if folder and (planning.parent != root and planning != root / "planning"):
            # Allow workspace.root_path when it is folder/planning.
            if planning.parent != root:
                raise ValueError("Invalid project folder.")
        return planning
    root = Path(folder).expanduser().resolve()
    planning = (root / "planning").resolve()
    if planning.parent != root:
        raise ValueError("Invalid project folder.")
    return planning


def _legacy_dir(folder: str) -> Path | None:
    if not str(folder or "").strip():
        return None
    root = Path(folder).expanduser().resolve()
    legacy = (root / ".planning").resolve()
    if legacy.parent != root:
        return None
    return legacy


def _find_saved(folder: str, screen_id: str, root_path: str = "") -> Path | None:
    if not _SCREEN.fullmatch(str(screen_id or "")):
        return None
    dirs = []
    if str(folder or "").strip() or str(root_path or "").strip():
        dirs.append(_planning_dir(folder, root_path))
    legacy = _legacy_dir(folder)
    if legacy is not None:
        dirs.append(legacy)
    for directory in dirs:
        if not directory.is_dir():
            continue
        for ext in _IMAGE:
            path = directory / f"{screen_id}{ext}"
            if path.is_file():
                return path
    return None


def _meta_path(image: Path) -> Path:
    return image.with_suffix(".json")


def _read_meta(image: Path) -> dict:
    path = _meta_path(image)
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_meta(image: Path, payload: dict) -> None:
    path = _meta_path(image)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _saved_names(folder: str, root_path: str = "") -> list[str]:
    names: list[str] = []
    seen: set[str] = set()
    dirs = []
    if str(folder or "").strip() or str(root_path or "").strip():
        dirs.append(_planning_dir(folder, root_path))
    legacy = _legacy_dir(folder)
    if legacy is not None:
        dirs.append(legacy)
    for directory in dirs:
        if not directory.is_dir():
            continue
        for path in sorted(directory.iterdir()):
            if path.is_file() and path.suffix.lower() in _IMAGE and path.stem not in seen:
                seen.add(path.stem)
                names.append(path.name)
    return names


def _shot_records(folder: str, root_path: str = "", hashes: dict[str, str] | None = None) -> dict[str, dict]:
    records = {}
    hashes = hashes or {}
    dirs = []
    if str(folder or "").strip() or str(root_path or "").strip():
        dirs.append(_planning_dir(folder, root_path))
    legacy = _legacy_dir(folder)
    if legacy is not None:
        dirs.append(legacy)
    for directory in dirs:
        if not directory.is_dir():
            continue
        for path in sorted(directory.iterdir()):
            if not path.is_file() or path.suffix.lower() not in _IMAGE or path.stem in records:
                continue
            if not _SCREEN.fullmatch(path.stem):
                continue
            meta = _read_meta(path)
            captured = str(meta.get("captured_at") or "")
            wire_hash = str(meta.get("wire_hash") or "")
            expected = hashes.get(path.stem)
            current = bool(captured) and (not expected or not wire_hash or wire_hash == expected)
            url = str(meta.get("url") or "")
            from urllib.parse import urlparse
            route = urlparse(url).path or ""
            records[path.stem] = {
                "captured_at": captured, "current": current, "name": path.name,
                "url": url, "route": route,
            }
    return records


def _image_url(workspace_id: int, screen_id: str) -> str:
    return f"/api/workflows/studio/plan-workspaces/{int(workspace_id)}/interface-shots/{screen_id}"


def _wire_hashes(workspace: dict) -> dict[str, str]:
    from distr.core.planning.interface_rebuild import screen_hashes

    item = next((row for row in workspace.get("items") or []
                 if row.get("item_type") == "flows" and row.get("content_format") == "wire"), None)
    if not item:
        return {}
    return screen_hashes(item.get("content") or "")


def _targets_for(project) -> dict:
    from distr.core.project_startup_terminals import project_startup_terminals_running
    from distr.core.terminal import get_startup_sessions_for_project

    project_id = int(project.id)
    try:
        running = bool(project_startup_terminals_running(project_id))
        sessions = get_startup_sessions_for_project(project_id, purpose="startup") if running else []
    except Exception:
        logger.exception("Interface shot status failed")
        running, sessions = False, []
    prod = production_url(project.folder_location or "", project.startup_instructions or "")
    targets = describe_targets(running, sessions, project.startup_instructions or "", production=prod)
    if not running and _is_self_project(project.folder_location or ""):
        targets.update({"terminals_up": True, "frontend_url": _SELF_APP_URL,
                        "backend_url": _SELF_APP_URL, "reason": ""})
    if not running and not targets["production_url"] and not targets["reason"] and not targets.get("frontend_url"):
        targets["reason"] = TERMINALS_DOWN
    return targets


def _is_self_project(folder: str) -> bool:
    try:
        return bool(str(folder or "").strip()) and Path(folder).expanduser().resolve() == _SELF_ROOT
    except OSError:
        return False


def _attempt_key(workspace_id: int, screen_id: str) -> str:
    return f"{int(workspace_id)}:{screen_id}"


def _read_attempts() -> dict:
    try:
        data = json.loads(_ATTEMPT_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _recent_attempt(workspace_id: int, screen_id: str) -> dict | None:
    row = _read_attempts().get(_attempt_key(workspace_id, screen_id))
    if not isinstance(row, dict):
        return None
    try:
        at = float(row.get("at") or 0)
    except (TypeError, ValueError):
        return None
    return row if time.time() - at < _RETRY_SECONDS else None


def _record_attempt(workspace_id: int, screen_id: str, reason: str | None) -> None:
    """Remember a failed capture, or forget it after a success. Best effort."""
    try:
        attempts = _read_attempts()
        key = _attempt_key(workspace_id, screen_id)
        if reason is None:
            if key not in attempts:
                return
            attempts.pop(key, None)
        else:
            attempts[key] = {"at": time.time(), "reason": reason}
        _ATTEMPT_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = _ATTEMPT_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(attempts, indent=1, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(tmp, _ATTEMPT_FILE)
    except OSError:
        logger.debug("Interface shot attempt log not written", exc_info=True)


def interface_shot_status(workspace_id: int) -> dict:
    """Whether page images can be fetched, plus filenames already in planning/."""
    workspace, project = _load(workspace_id)
    targets = _targets_for(project)
    root_path = str(workspace.get("root_path") or "")
    hashes = _wire_hashes(workspace)
    shots = _shot_records(project.folder_location or "", root_path, hashes)
    for name, row in shots.items():
        row["image"] = _image_url(int(workspace_id), name)
        row["thumb"] = row["image"] + "?thumb=1"
    return {
        **targets,
        "saved": _saved_names(project.folder_location or "", root_path),
        "shots": shots,
    }


def _thumbnail_for(path: Path) -> Path:
    """A small JPEG beside the original capture, rebuilt when the capture is newer."""
    thumb = path.with_name(f"{path.stem}.thumb.jpg")
    try:
        if thumb.is_file() and thumb.stat().st_mtime >= path.stat().st_mtime and thumb.stat().st_size > 0:
            return thumb
    except OSError:
        pass
    from PIL import Image
    with Image.open(path) as image:
        rgb = image.convert("RGB")
        rgb.thumbnail((480, 300), Image.Resampling.LANCZOS)
        tmp = thumb.with_suffix(".tmp.jpg")
        rgb.save(tmp, "JPEG", quality=72, optimize=True)
        tmp.replace(thumb)
    return thumb


_DIR_CACHE: dict[int, tuple[str, str, float]] = {}
_DIR_CACHE_TTL = 120.0
_dir_lock = threading.Lock()


def _workspace_dirs(workspace_id: int) -> tuple[str, str]:
    """Project folder + planning dir, without loading the full workspace (thumbs must stay cheap)."""
    wid = int(workspace_id)
    now = time.time()
    with _dir_lock:
        hit = _DIR_CACHE.get(wid)
        if hit and now - hit[2] < _DIR_CACHE_TTL:
            return hit[0], hit[1]
    from distr.core.db import get_session
    from distr.core.db.projects import Project
    from distr.core.db.workflow import PlanWorkspace

    with get_session() as session:
        row = session.get(PlanWorkspace, wid)
        if row is None:
            raise LookupError("Plan workspace not found.")
        folder = ""
        if row.project_id:
            project = session.get(Project, int(row.project_id))
            folder = str(project.folder_location or "") if project is not None else ""
        root = str(row.root_path or "")
    with _dir_lock:
        _DIR_CACHE[wid] = (folder, root, now)
    return folder, root


def interface_shot_file(workspace_id: int, screen_id: str, *, thumb: bool = False) -> tuple[Path, str]:
    """One image already saved for this screen id. No directory walk past planning/."""
    if not _SCREEN.fullmatch(str(screen_id or "")):
        raise ValueError("Invalid page id.")
    folder, root_path = _workspace_dirs(workspace_id)
    path = _find_saved(folder, screen_id, root_path)
    if path is None:
        raise LookupError("No screenshot for that page.")
    if thumb:
        return _thumbnail_for(path), "image/jpeg"
    return path, _IMAGE[path.suffix.lower()]


# Viewport capture: 1440x900 at retina scale, settled page, no interactive dismissals.
_SHOT_VIEWPORT = {"width": 1440, "height": 900}
_SHOT_SCALE = 2
_SHOT_GOTO_MS = 15000
_SHOT_STEP_MS = 8000
_SHOT_SETTLE_MS = 800
_SHOT_PAGE_MS = 25000
_SHOT_LOADERS = ".spinner, .loading, [aria-busy=true], .skeleton, .spinner-border, .loader"
_SHOT_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
_SHOT_STEALTH = (
    "Object.defineProperty(navigator, 'webdriver', { get: () => undefined });"
)
_PAYMENT_HOST_MARKERS = ("payfast", "yoco", "paypal.com", "payu.co", "peachpayments")



def _is_payment_url(url: str) -> bool:
    host = (urlparse(str(url or "")).hostname or "").lower()
    return any(marker in host for marker in _PAYMENT_HOST_MARKERS)


def _origin(url: str) -> str:
    parsed = urlparse(url)
    return f"{parsed.scheme}://{parsed.netloc}"


def _shot_remaining(deadline: float) -> int:
    return max(300, int((deadline - time.monotonic()) * 1000))


def _wait_for_shot(page, deadline: float) -> None:
    """Settle a loaded page before capture. Never dismisses interactive UI."""
    try:
        page.wait_for_load_state("networkidle", timeout=min(_SHOT_STEP_MS, _shot_remaining(deadline)))
    except Exception:
        pass
    try:
        page.evaluate("() => document.fonts.ready", timeout=min(_SHOT_STEP_MS, _shot_remaining(deadline)))
    except Exception:
        pass
    try:
        page.wait_for_function(
            "() => [...document.images].every((img) => img.complete)",
            timeout=min(_SHOT_STEP_MS, _shot_remaining(deadline)),
        )
    except Exception:
        pass
    try:
        page.wait_for_function(
            """(sel) => [...document.querySelectorAll(sel)].every((el) => {
                const st = getComputedStyle(el);
                return st.display === 'none' || st.visibility === 'hidden'
                    || st.opacity === '0' || el.getClientRects().length === 0;
            })""",
            _SHOT_LOADERS,
            timeout=min(_SHOT_STEP_MS, _shot_remaining(deadline)),
        )
    except Exception:
        pass
    page.wait_for_timeout(min(_SHOT_SETTLE_MS, _shot_remaining(deadline)))


def _prepare_shot_page(context, allowed_hosts):
    page = context.new_page()

    def _on_route(route):
        if _is_payment_url(route.request.url):
            route.abort()
            return
        # A screenshot only reads: nothing the page tries to POST, PUT or DELETE is sent.
        if route.request.method not in ("GET", "HEAD", "OPTIONS"):
            route.abort()
            return
        route.continue_()

    page.route("**/*", _on_route)

    def _on_frame(frame):
        try:
            frame_url = frame.url
        except Exception:
            return
        if _is_payment_url(frame_url):
            try:
                page.go_back(wait_until="domcontentloaded", timeout=3000)
            except Exception:
                pass

    page.on("framenavigated", _on_frame)
    return page


def _open_shot_url(page, url: str, allowed_hosts: set[str], deadline: float):
    """Warm up via the site home, then open the target. Never follows payment providers."""
    origin = _origin(url)
    target_path = urlparse(url).path or "/"
    if target_path not in {"/", ""}:
        try:
            page.goto(origin + "/", wait_until="domcontentloaded",
                      timeout=min(_SHOT_GOTO_MS, _shot_remaining(deadline)))
        except Exception:
            pass
        if page.url and not _clean_url(page.url, allowed_hosts) and not _is_payment_url(page.url):
            raise ValueError("The page left the project.")
        if _is_payment_url(page.url):
            raise ValueError("Aborted navigation to a payment provider.")
    response = page.goto(url, wait_until="load", timeout=min(_SHOT_GOTO_MS, _shot_remaining(deadline)))
    if _is_payment_url(page.url):
        raise ValueError("Aborted navigation to a payment provider.")
    if not _clean_url(page.url, allowed_hosts):
        raise ValueError("The page left the project.")
    if response is not None and response.status >= 400:
        raise ValueError(f"The page returned {response.status}.")
    try:
        page.wait_for_function(
            r"""() => performance.getEntriesByType('resource').some((e) =>
                /\/api\//.test(e.name) && (!e.responseStatus || e.responseStatus < 500)
            )""",
            timeout=min(6000, _shot_remaining(deadline)),
        )
    except Exception:
        pass
    return response


def _browser_env() -> dict[str, str]:
    """The app's environment without DYLD_* overrides, which stop Firefox from loading its own NSS."""
    return {key: value for key, value in os.environ.items() if not key.startswith("DYLD_")}


def _grab(url: str, dest: Path, allowed_hosts: set[str]) -> dict:
    """Capture one page with headless Firefox. Returns the page's rendered-DOM cue ({} if unreadable)."""
    from playwright.sync_api import sync_playwright

    if not _clean_url(url, allowed_hosts):
        raise ValueError("Refusing a page outside the project.")
    dest.parent.mkdir(parents=True, exist_ok=True)
    last_error = None
    cue: dict = {}
    for attempt in range(2):
        with sync_playwright() as playwright:
            browser = playwright.firefox.launch(headless=True, env=_browser_env())
            try:
                context = browser.new_context(
                    viewport=dict(_SHOT_VIEWPORT),
                    device_scale_factor=_SHOT_SCALE,
                    user_agent=_SHOT_UA,
                    locale="en-ZA",
                )
                context.add_init_script(_SHOT_STEALTH)
                page = _prepare_shot_page(context, allowed_hosts)
                deadline = time.monotonic() + (_SHOT_PAGE_MS / 1000)
                _open_shot_url(page, url, allowed_hosts, deadline)
                _wait_for_shot(page, deadline)
                # Viewport PNG at device_scale_factor 2 (2880x1800). Not a full-page shot, to keep size reasonable.
                page.screenshot(path=str(dest), type="png")
                try:
                    from distr.core.planning.dom_cues import _JS_READ, _normalise

                    cue = _normalise(page.evaluate(_JS_READ))
                except Exception:
                    logger.debug("Interface shot: rendered cues not read", exc_info=True)
                    cue = {}
                last_error = None
                break
            except Exception as exc:
                last_error = exc
                logger.info("Interface shot attempt %s failed: %s", attempt + 1, exc)
            finally:
                browser.close()
        if last_error is None:
            break
    if last_error is not None:
        raise last_error
    return cue


def _clear_shot_files(directory: Path) -> int:
    """Delete screenshot PNG/JPEG/WebP files and their sidecar JSON in planning/.

    Never touches iface-layout.json, wire documents, or other plan files.
    """
    if not directory.is_dir():
        return 0
    cleared = 0
    skip = {"iface-layout"}
    for path in list(directory.iterdir()):
        if not path.is_file():
            continue
        stem = path.stem
        if stem in skip or not _SCREEN.fullmatch(stem):
            continue
        if path.suffix.lower() in _IMAGE:
            meta = _meta_path(path)
            path.unlink(missing_ok=True)
            if meta.is_file():
                meta.unlink(missing_ok=True)
            cleared += 1
        elif path.suffix.lower() == ".json":
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError, UnicodeError):
                continue
            if isinstance(data, dict) and (data.get("captured_at") or data.get("screen_id") or data.get("url")):
                if not any((directory / f"{stem}{ext}").is_file() for ext in _IMAGE):
                    path.unlink(missing_ok=True)
                    cleared += 1
    return cleared


def clear_interface_shots(workspace_id: int) -> dict:
    """Remove cached page screenshots from the project's planning/ folder."""
    workspace, project = _load(workspace_id)
    root_path = str(workspace.get("root_path") or "")
    folder = _planning_dir(project.folder_location or "", root_path)
    cleared = _clear_shot_files(folder)
    return {"cleared": cleared, "saved": []}


def capture_interface_shot(workspace_id: int, *, screen_id: str, route: str, lane: str, force: bool = False) -> dict:
    """Return a saved image, capture one page, or explain why it was skipped."""
    if not _SCREEN.fullmatch(str(screen_id or "")):
        raise ValueError("Invalid page id.")
    workspace, project = _load(workspace_id)
    root_path = str(workspace.get("root_path") or "")
    targets = _targets_for(project)
    saved = _find_saved(project.folder_location or "", screen_id, root_path)
    hashes = _wire_hashes(workspace)
    meta = _read_meta(saved) if saved is not None else {}
    expected = hashes.get(screen_id)
    plan = capture_plan(
        targets, lane=str(lane or ""), route=str(route or ""),
        saved=saved is not None, force=bool(force),
    )
    image = _image_url(workspace_id, screen_id)
    captured_at = str(meta.get("captured_at") or "")
    stale = bool(expected and meta.get("wire_hash") and meta.get("wire_hash") != expected)
    if plan["action"] == "grab" and saved is None and not force:
        recent = _recent_attempt(workspace_id, screen_id)
        if recent is not None:
            return {"image": "", "reason": str(recent.get("reason") or "The page did not load."),
                    "captured_at": "", "current": False}
    if plan["action"] == "read":
        return {"image": image, "reason": "", "captured_at": captured_at, "current": True}
    if plan["action"] != "grab":
        return {"image": image if saved is not None else "", "reason": plan["reason"],
                "captured_at": captured_at, "current": False}
    dest = _planning_dir(project.folder_location or "", root_path) / f"{screen_id}.png"
    allowed = set(_LOCAL_HOSTS)
    if targets.get("production_url"):
        allowed.add(_host(targets["production_url"]))
    try:
        cue = _grab(plan["url"], dest, allowed)
    except Exception as exc:
        logger.info("Interface shot skipped: %s", exc)
        if saved is not None:
            return {"image": image, "reason": "", "captured_at": captured_at, "current": not stale}
        reason = "The page did not load from the running project or production site."
        _record_attempt(workspace_id, screen_id, reason)
        return {"image": "", "reason": reason, "captured_at": "", "current": False}
    _record_attempt(workspace_id, screen_id, None)
    stamp = datetime.now(timezone.utc).isoformat()
    payload = {
        "captured_at": stamp,
        "url": plan["url"],
        "screen_id": screen_id,
        "wire_hash": expected or "",
    }
    if cue:
        payload["cues"] = cue
    _write_meta(dest, payload)
    return {"image": image, "reason": "", "captured_at": stamp, "current": True}


def _layout_path(workspace: dict, project) -> Path:
    return _planning_dir(project.folder_location or "", str(workspace.get("root_path") or "")) / _LAYOUT_FILE


def _clean_layout(nodes) -> dict[str, dict]:
    out = {}
    if not isinstance(nodes, dict) or len(nodes) > 400:
        return out
    for key, value in nodes.items():
        if not _SCREEN.fullmatch(str(key or "")) or not isinstance(value, dict):
            continue
        try:
            x = float(value.get("x"))
            y = float(value.get("y"))
        except (TypeError, ValueError):
            continue
        if not (abs(x) < 100_000 and abs(y) < 100_000):
            continue
        out[str(key)] = {"x": round(x, 2), "y": round(y, 2), "pinned": True}
        if len(out) >= 200:
            break
    return out


def interface_layout(workspace_id: int) -> dict:
    """Pinned card positions stored in planning/iface-layout.json."""
    folder, root_path = _workspace_dirs(workspace_id)
    path = _planning_dir(folder, root_path) / _LAYOUT_FILE
    if not path.is_file():
        return {"nodes": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeError):
        return {"nodes": {}}
    nodes = data.get("nodes") if isinstance(data, dict) else {}
    return {"nodes": _clean_layout(nodes)}


def save_interface_layout(workspace_id: int, nodes: dict) -> dict:
    """Replace the pinned-position file. Does not rewrite plan documents."""
    folder, root_path = _workspace_dirs(workspace_id)
    clean = _clean_layout(nodes)
    path = _planning_dir(folder, root_path) / _LAYOUT_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps({"v": 1, "nodes": clean}, indent=2, sort_keys=True) + "\n"
    staged = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=".iface-layout-", delete=False) as stream:
            staged = Path(stream.name)
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(staged, path)
        staged = None
    finally:
        if staged is not None and staged.exists():
            staged.unlink()
    return {"nodes": clean}
