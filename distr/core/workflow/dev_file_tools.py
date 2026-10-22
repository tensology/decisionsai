"""Files tool for the Development agent: allowlisted roots, denylist, Trash-only deletes.

Roots: ~/Downloads, ~/Desktop, ~/Documents, the project folder, and every
mounted /Volumes/* drive (not the boot volume alias). Relative paths resolve
against the project folder. Symlinks are resolved before checks.
trash / move / overwrite (write, copy onto an existing file) need a one-shot
user approval (dev_approvals.gate). Deletes ONLY go to the macOS Trash via
NSFileManager.trashItemAtURL (pyobjc) or send2trash if installed; never rm,
never Finder/osascript. If neither is available the action fails.
"""

from __future__ import annotations

import mimetypes
import os
import shutil
from pathlib import Path
from typing import Any, Optional

from distr.core.workflow import dev_approvals

ACTIONS = ["list", "stat", "read", "create_file", "write", "mkdir", "move", "copy", "trash"]
MAX_READ = 256 * 1024
MAX_LIST = 200

SCHEMA = {
    "description": (
        "Files and folders on ~/Downloads, ~/Desktop, ~/Documents, the project folder and mounted /Volumes drives. "
        "Actions: list, stat, read, create_file, write, mkdir, move, copy, trash. trash moves to the macOS Trash "
        "(never permanent). trash, move and overwriting an existing file need the user's approval in the thread "
        "(retry the identical call with approval_id after approval_wait)."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ACTIONS},
            "path": {"type": "string", "description": "File or folder; ~ allowed; relative = project folder"},
            "destination": {"type": "string", "description": "move/copy target"},
            "content": {"type": "string", "description": "create_file/write text"},
            "overwrite": {"type": "boolean", "default": False},
            "approval_id": {"type": "string"},
        },
        "required": ["action", "path"],
    },
}

_DENY_HOME = [".ssh", ".gnupg", ".aws", ".config", ".decisions", ".decisionsai", ".netrc", "Library",
              ".docker", ".kube", ".cursor", ".codex", ".claude"]
_DENY_ABS = ["/System", "/private", "/usr", "/bin", "/sbin", "/etc", "/Library", "/Applications"]
_DENY_NAMES = {"settings.db", "internal_api_token", "mcp_server_token", "id_rsa", "id_ed25519"}


def _home() -> Path:
    return Path(os.path.expanduser("~")).resolve()


def allowed_roots(project_folder: Optional[str] = None) -> list[Path]:
    h = _home()
    roots = [h / "Downloads", h / "Desktop", h / "Documents"]
    if project_folder:
        proj = Path(project_folder).expanduser().resolve()
        # Never let a project folder widen access to / , ~ or a top-level system dir.
        if proj != h and len(proj.parts) >= 3:
            roots.append(proj)
    vol = Path("/Volumes")
    try:
        for entry in vol.iterdir():
            real = entry.resolve()
            if real == Path("/"):  # boot volume alias ("Macintosh HD")
                continue
            roots.append(real)
    except OSError:
        pass
    return roots


def _denied(p: Path) -> Optional[str]:
    h = _home()
    if p.name in _DENY_NAMES or ".keychain" in p.name:
        return f"{p.name} is protected"
    try:
        rel = p.relative_to(h)
        if rel.parts and rel.parts[0] in _DENY_HOME:
            return f"~/{rel.parts[0]} is protected"
    except ValueError:
        pass
    under_home = h == p or h in p.parents
    for d in ([] if under_home else _DENY_ABS):
        if str(p) == d or str(p).startswith(d + "/"):
            return f"{d} is protected"
    db_dir = os.environ.get("DECISIONS_DB_DIR")
    for d in [db_dir] if db_dir else []:
        try:
            p.relative_to(Path(d).resolve())
            return "the Decisions database folder is protected"
        except ValueError:
            pass
    return None


def resolve(path: str, project_folder: Optional[str] = None) -> Path:
    """Resolve to an absolute, symlink-free path inside an allowed root, or raise PermissionError."""
    if not path or "\x00" in path:
        raise PermissionError("empty or invalid path")
    raw = Path(os.path.expanduser(path))
    if not raw.is_absolute():
        raw = Path(project_folder or os.getcwd()) / raw
    p = raw.resolve()  # follows symlinks, collapses ..
    proj = Path(project_folder).expanduser().resolve() if project_folder else None
    if proj is not None and (proj == _home() or len(proj.parts) < 3):
        proj = None
    why = _denied(p)
    in_project = bool(proj and (p == proj or proj in p.parents))
    # System-folder denials (messages starting with "/") are waived only inside the
    # project folder; secrets, ~/.dotdirs and the Decisions DB stay denied everywhere.
    if why and not (in_project and why.startswith("/")):
        raise PermissionError(why)
    for root in allowed_roots(project_folder):
        if p == root or root in p.parents:
            return p
    raise PermissionError(f"{p} is outside the allowed folders (Downloads, Desktop, Documents, project, /Volumes)")


def _is_root(p: Path, project_folder: Optional[str]) -> bool:
    return p in allowed_roots(project_folder) or p == _home()


def trash_path(p: Path) -> str:
    """Move to the Trash. Returns the Trash location. Never deletes permanently."""
    try:
        from Foundation import NSFileManager, NSURL  # pyobjc
    except Exception:
        NSFileManager = None
    if NSFileManager is not None:
        ok, res, err = NSFileManager.defaultManager().trashItemAtURL_resultingItemURL_error_(
            NSURL.fileURLWithPath_(str(p)), None, None)
        if not ok:
            raise OSError(f"Trash failed: {err.localizedDescription() if err else 'unknown error'}")
        return str(res.path()) if res else "Trash"
    try:
        from send2trash import send2trash
    except Exception:
        raise OSError("No Trash backend available (pyobjc Foundation or send2trash); refusing to delete")
    send2trash(str(p))
    return "Trash"


def _res(ok: bool, summary: str, **extra: Any) -> dict[str, Any]:
    return {"ok": ok, "tool": "files", "summary": summary, "artifacts": [], **extra}


def _settings() -> dict[str, Any]:
    try:
        from distr.core.settings import load_settings_from_db
        return load_settings_from_db() or {}
    except Exception:
        return {}


def files(action: str, path: str, destination: str | None = None, content: str | None = None,
          overwrite: bool | None = False, approval_id: str | None = None, *, project_folder: str | None = None,
          settings: dict[str, Any] | None = None, **_: Any) -> dict[str, Any]:
    s = settings if settings is not None else _settings()
    if s.get("dev_files_enabled") is False:
        return _res(False, "The files tool is turned off (Settings > Development agent tools).")
    if action not in ACTIONS:
        return _res(False, f"unknown action {action!r}; use one of {ACTIONS}")
    try:
        p = resolve(path, project_folder)
        dest = resolve(destination, project_folder) if destination else None
    except PermissionError as exc:
        return _res(False, f"Not allowed: {exc}")

    try:
        if action == "list":
            if not p.is_dir():
                return _res(False, f"not a folder: {p}")
            entries = []
            for i, e in enumerate(sorted(p.iterdir(), key=lambda x: x.name.lower())):
                if i >= MAX_LIST:
                    break
                try:
                    st = e.lstat()
                    entries.append({"name": e.name, "type": "dir" if e.is_dir() else "file", "size": st.st_size})
                except OSError:
                    continue
            return _res(True, f"{len(entries)} entr(ies) in {p}", path=str(p), entries=entries)
        if action == "stat":
            if not p.exists():
                return _res(True, f"{p} does not exist", path=str(p), exists=False)
            st = p.stat()
            return _res(True, f"{p}", path=str(p), exists=True, type="dir" if p.is_dir() else "file",
                        size=st.st_size, modified=st.st_mtime)
        if action == "read":
            if not p.is_file():
                return _res(False, f"not a file: {p}")
            mime = mimetypes.guess_type(str(p))[0] or ""
            if mime.startswith("image/"):
                return _res(True, f"image {p.name}", path=str(p),
                            artifacts=[{"path": str(p), "mime": mime, "kind": "image", "bytes": p.stat().st_size}])
            data = p.read_bytes()[:MAX_READ]
            if b"\x00" in data[:4096]:
                return _res(True, f"binary file {p.name} ({p.stat().st_size} bytes, {mime or 'unknown type'})", path=str(p))
            text = data.decode("utf-8", errors="replace")
            return _res(True, f"read {p} ({len(data)} bytes{', truncated' if p.stat().st_size > MAX_READ else ''})",
                        path=str(p), content=text)
        if action == "mkdir":
            p.mkdir(parents=True, exist_ok=True)
            return _res(True, f"folder ready: {p}", path=str(p))
        if action == "create_file":
            if p.exists():
                return _res(False, f"{p} already exists; use write with overwrite=true")
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content or "", encoding="utf-8")
            return _res(True, f"created {p}", path=str(p))

        # --- approval-gated ---
        if action == "write":
            if p.is_dir():
                return _res(False, f"{p} is a folder")
            if p.exists():
                if not overwrite:
                    return _res(False, f"{p} exists; pass overwrite=true (needs approval)")
                blocked = dev_approvals.gate("files", "overwrite", {"path": str(p), "content": content},
                                             approval_id, f"overwrite {p}",
                                             {"path": str(p), "before_bytes": p.stat().st_size,
                                              "after_preview": (content or "")[:500]})
                if blocked:
                    return blocked
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content or "", encoding="utf-8")
            return _res(True, f"wrote {p}", path=str(p))
        if action in ("move", "copy"):
            if not dest:
                return _res(False, f"{action} needs destination")
            if not p.exists():
                return _res(False, f"{p} does not exist")
            if action == "move" and _is_root(p, project_folder):
                return _res(False, f"refusing to move a top-level folder: {p}")
            target = dest / p.name if dest.is_dir() else dest
            clobber = target.exists()
            if clobber and not overwrite:
                return _res(False, f"{target} exists; pass overwrite=true (needs approval)")
            if action == "move" or clobber:
                blocked = dev_approvals.gate("files", action, {"path": str(p), "destination": str(target)},
                                             approval_id, f"{action} {p} -> {target}" + (" (overwrites)" if clobber else ""),
                                             {"from": str(p), "to": str(target), "overwrites": clobber})
                if blocked:
                    return blocked
            if clobber:
                trash_path(target)  # the replaced file goes to the Trash too
            if action == "move":
                shutil.move(str(p), str(target))
            elif p.is_dir():
                shutil.copytree(str(p), str(target))
            else:
                shutil.copy2(str(p), str(target))
            return _res(True, f"{action}d {p} -> {target}", path=str(target))
        # trash
        if not p.exists():
            return _res(False, f"{p} does not exist")
        if _is_root(p, project_folder):
            return _res(False, f"refusing to trash a top-level folder: {p}")
        blocked = dev_approvals.gate("files", "trash", {"path": str(p)}, approval_id, f"move {p} to the Trash",
                                     {"path": str(p), "type": "dir" if p.is_dir() else "file"})
        if blocked:
            return blocked
        where = trash_path(p)
        return _res(True, f"moved {p} to the Trash", path=str(p), trash_location=where)
    except OSError as exc:
        return _res(False, f"{action} failed: {exc}")
