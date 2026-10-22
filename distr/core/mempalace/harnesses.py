"""Connect installed coding harnesses to the local MemPalace hub (and undo it).

Every harness gets the same stdio server, ``<decisions venv python> -m
mempalace.mcp_server --palace <palace>``. MemPalace (3.7+) makes that stdio
process forward every request to the live hub, reading the 0600 token itself,
so no secret is ever written into a harness config. Harnesses that are not
installed are skipped. Each config is backed up once per day as
``<file>.bak-YYYYMMDD-mempalace`` before the first change, edits are
idempotent, unrelated entries are preserved, and ``unwire_all`` removes only
what was added here (marker blocks / the ``mempalace`` key).

No session hooks are installed: frustration capture reads transcripts out of
band (see ``selfimprove``), so nothing can block or slow a harness session.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable

from distr.core.mempalace.paths import default_palace_path

SERVER = "mempalace"
BEGIN = ">>> decisions-mempalace >>>"
END = "<<< decisions-mempalace <<<"
Log = Callable[[str], None]

HERE = Path(__file__).resolve().parent


def home() -> Path:
    return Path(os.path.expanduser("~"))


def python_bin() -> str:
    return os.environ.get("DECISIONS_MEMPALACE_PYTHON") or sys.executable


def server_cmd() -> list[str]:
    return [python_bin(), "-m", "mempalace.mcp_server", "--palace", str(default_palace_path())]


def host_label() -> str:
    return os.environ.get("DECISIONS_MEMPALACE_HOST_LABEL", "mac" if sys.platform == "darwin" else "host")


def identity_block(harness: str, comment: str = "") -> str:
    h = host_label()
    body = f"""## MemPalace shared memory (local, via Decisions)
You share one local MemPalace memory with the user's other agents (Decisions, Claude Code, Codex, Cursor, Kilo, Pi).
Your agent identity is `{h}:{harness}:<project>`, where <project> is the current repo/workspace folder name in lowercase.
Use that identity as `added_by` / `from_agent` in every mempalace_* call. Never use another agent's identity.
- Before answering about past work, decisions, people or projects: call `mempalace_search` (wing = project name when known). Quote results; if nothing is found, say so.
- File durable outcomes (decisions, conclusions, learned facts) with `mempalace_add_drawer` (wing = project, room = topic). Say when you do it.
- Never file secrets, tokens, passwords or personal data. Memory stays on this machine."""
    if comment:
        return f"{comment} {BEGIN}\n{body}\n{comment} {END}\n"
    return f"<!-- {BEGIN} -->\n{body}\n<!-- {END} -->\n"


# ── file helpers ──────────────────────────────────────────────────────────────

def backup(path: Path, log: Log, dry_run: bool) -> Path | None:
    if not path.exists():
        return None
    bak = path.with_name(f"{path.name}.bak-{time.strftime('%Y%m%d')}-mempalace")
    if bak.exists():
        return bak
    log(f"backup {path} -> {bak.name}")
    if not dry_run:
        shutil.copy2(path, bak)
    return bak


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp-mempalace")
    tmp.write_text(text, encoding="utf-8")
    try:
        os.chmod(tmp, path.stat().st_mode & 0o777)
    except OSError:
        pass
    os.replace(tmp, path)


def _strip_block(text: str) -> str:
    pattern = re.compile(r"\n?[^\n]*" + re.escape(BEGIN) + r".*?" + re.escape(END) + r"[^\n]*\n?", re.S)
    return pattern.sub("\n", text).rstrip("\n") + ("\n" if text.strip() else "")


def _upsert_block(path: Path, block: str, log: Log, dry_run: bool, *, create: bool) -> str:
    if not path.exists() and not create:
        return "skipped(no file)"
    old = path.read_text(encoding="utf-8") if path.exists() else ""
    new = _strip_block(old) if BEGIN in old else old
    new = (new.rstrip("\n") + "\n\n" if new.strip() else "") + block
    if new == old:
        return "unchanged"
    backup(path, log, dry_run)
    log(f"{'update' if BEGIN in old else 'add'} marker block in {path}")
    if not dry_run:
        _atomic_write(path, new)
    return "created" if not old else "updated"


def _remove_block(path: Path, log: Log, dry_run: bool) -> str:
    if not path.exists():
        return "absent"
    old = path.read_text(encoding="utf-8")
    if BEGIN not in old:
        return "absent"
    backup(path, log, dry_run)
    log(f"remove marker block from {path}")
    if not dry_run:
        _atomic_write(path, _strip_block(old))
    return "removed"


def _json_load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def _json_set_server(path: Path, key: str, entry: dict, log: Log, dry_run: bool, *, create: bool) -> str:
    if not path.exists() and not create:
        return "skipped(no file)"
    data = _json_load(path)
    servers = data.setdefault(key, {})
    if servers.get(SERVER) == entry:
        return "unchanged"
    backup(path, log, dry_run)
    log(f"set {key}.{SERVER} in {path} (keeping {len([k for k in servers if k != SERVER])} other servers)")
    servers[SERVER] = entry
    if not dry_run:
        _atomic_write(path, json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    return "created" if not path.exists() else "set"


def _json_del_server(path: Path, key: str, log: Log, dry_run: bool) -> str:
    if not path.exists():
        return "absent"
    data = _json_load(path)
    if SERVER not in (data.get(key) or {}):
        return "absent"
    backup(path, log, dry_run)
    log(f"remove {key}.{SERVER} from {path}")
    del data[key][SERVER]
    if not dry_run:
        _atomic_write(path, json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    return "removed"


def _which(name: str) -> str | None:
    found = shutil.which(name)
    if found:
        return found
    for cand in (home() / ".local" / "bin" / name, Path("/opt/homebrew/bin") / name, Path("/usr/local/bin") / name):
        if cand.exists():
            return str(cand)
    return None


# ── harness definitions ───────────────────────────────────────────────────────

class Harness:
    name = ""

    def installed(self) -> bool:  # pragma: no cover - overridden
        return False

    def wire(self, log: Log, dry_run: bool) -> dict[str, Any]:  # pragma: no cover
        return {}

    def unwire(self, log: Log, dry_run: bool) -> dict[str, Any]:  # pragma: no cover
        return {}

    def status(self) -> dict[str, Any]:  # pragma: no cover
        return {}


class Claude(Harness):
    name = "claude"

    def cfg(self) -> Path:
        return home() / ".claude.json"

    def md(self) -> Path:
        return home() / ".claude" / "CLAUDE.md"

    def installed(self) -> bool:
        return bool(_which("claude")) or self.cfg().exists()

    def _entry(self) -> dict:
        cmd = server_cmd()
        return {"type": "stdio", "command": cmd[0], "args": cmd[1:], "env": {}}

    def wire(self, log, dry_run):
        res: dict[str, Any] = {}
        cli = _which("claude")
        current = (_json_load(self.cfg()).get("mcpServers") or {}).get(SERVER) if self.cfg().exists() else None
        if current == self._entry():
            res["mcp"] = "unchanged"
        elif cli:
            backup(self.cfg(), log, dry_run)
            cmd = server_cmd()
            log(f"claude mcp add --scope user {SERVER} -- {' '.join(cmd)}")
            if not dry_run:
                subprocess.run([cli, "mcp", "remove", "--scope", "user", SERVER], capture_output=True, timeout=60)
                r = subprocess.run([cli, "mcp", "add", "--scope", "user", SERVER, "--", *cmd],
                                   capture_output=True, text=True, timeout=60)
                res["mcp"] = "added" if r.returncode == 0 else f"error rc={r.returncode}"
            else:
                res["mcp"] = "would-add"
        else:
            res["mcp"] = _json_set_server(self.cfg(), "mcpServers", self._entry(), log, dry_run, create=False)
        res["instructions"] = _upsert_block(self.md(), identity_block("claude"), log, dry_run, create=False)
        return res

    def unwire(self, log, dry_run):
        res: dict[str, Any] = {}
        cli = _which("claude")
        if cli and self.cfg().exists() and SERVER in (_json_load(self.cfg()).get("mcpServers") or {}):
            backup(self.cfg(), log, dry_run)
            log(f"claude mcp remove --scope user {SERVER}")
            if not dry_run:
                subprocess.run([cli, "mcp", "remove", "--scope", "user", SERVER], capture_output=True, timeout=60)
            res["mcp"] = "removed"
        else:
            res["mcp"] = _json_del_server(self.cfg(), "mcpServers", log, dry_run)
        res["instructions"] = _remove_block(self.md(), log, dry_run)
        return res

    def status(self):
        return {"installed": self.installed(),
                "wired": SERVER in ((_json_load(self.cfg()).get("mcpServers") or {}) if self.cfg().exists() else {})}


class Codex(Harness):
    name = "codex"

    def cfg(self) -> Path:
        return home() / ".codex" / "config.toml"

    def md(self) -> Path:
        return home() / ".codex" / "AGENTS.md"

    def installed(self) -> bool:
        return bool(_which("codex")) or self.cfg().exists()

    def _block(self) -> str:
        cmd = server_cmd()
        args = ", ".join(json.dumps(a) for a in cmd[1:])
        return (f"# {BEGIN}\n[mcp_servers.{SERVER}]\ncommand = {json.dumps(cmd[0])}\nargs = [{args}]\n"
                f"startup_timeout_sec = 30\ntool_timeout_sec = 120\n# {END}\n")

    def wire(self, log, dry_run):
        res: dict[str, Any] = {}
        if not self.cfg().exists():
            res["mcp"] = "skipped(no config.toml)"
        else:
            text = self.cfg().read_text(encoding="utf-8")
            if re.search(rf"^\[mcp_servers\.{SERVER}\]", _strip_block(text), re.M):
                res["mcp"] = "skipped(user-defined mempalace entry exists)"
            else:
                res["mcp"] = _upsert_block(self.cfg(), self._block(), log, dry_run, create=False)
        res["instructions"] = _upsert_block(self.md(), identity_block("codex"), log, dry_run, create=False)
        return res

    def unwire(self, log, dry_run):
        return {"mcp": _remove_block(self.cfg(), log, dry_run), "instructions": _remove_block(self.md(), log, dry_run)}

    def status(self):
        return {"installed": self.installed(),
                "wired": self.cfg().exists() and BEGIN in self.cfg().read_text(encoding="utf-8")}


class Cursor(Harness):
    name = "cursor"

    def cfg(self) -> Path:
        return home() / ".cursor" / "mcp.json"

    def installed(self) -> bool:
        return (home() / ".cursor").is_dir() or Path("/Applications/Cursor.app").exists() or bool(_which("cursor-agent"))

    def _entry(self) -> dict:
        cmd = server_cmd()
        return {"command": cmd[0], "args": cmd[1:]}

    def wire(self, log, dry_run):
        # Cursor user rules live in its app database, so identity guidance ships via the MCP
        # server's own Memory Protocol (returned by mempalace_status) instead of a file.
        return {"mcp": _json_set_server(self.cfg(), "mcpServers", self._entry(), log, dry_run, create=True)}

    def unwire(self, log, dry_run):
        return {"mcp": _json_del_server(self.cfg(), "mcpServers", log, dry_run)}

    def status(self):
        return {"installed": self.installed(),
                "wired": self.cfg().exists() and SERVER in (_json_load(self.cfg()).get("mcpServers") or {})}


def _strip_jsonc(text: str) -> str:
    out, i, n, in_str = [], 0, len(text), False
    while i < n:
        c = text[i]
        if in_str:
            out.append(c)
            if c == "\\" and i + 1 < n:
                out.append(text[i + 1])
                i += 2
                continue
            if c == '"':
                in_str = False
        elif c == '"':
            in_str = True
            out.append(c)
        elif text.startswith("//", i):
            while i < n and text[i] != "\n":
                i += 1
            continue
        elif text.startswith("/*", i):
            end = text.find("*/", i + 2)
            i = n if end < 0 else end + 2
            continue
        else:
            out.append(c)
        i += 1
    return re.sub(r",(\s*[}\]])", r"\1", "".join(out))


class Kilo(Harness):
    """Kilo CLI / app (opencode-style ``~/.config/kilo/kilo.jsonc``) and the legacy VS Code extension."""

    name = "kilo"

    def cfg(self) -> Path:
        return home() / ".config" / "kilo" / "kilo.jsonc"

    def vscode_cfg(self) -> Path:
        return (home() / "Library" / "Application Support" / "Code" / "User" / "globalStorage"
                / "kilocode.kilo-code" / "settings" / "mcp_settings.json")

    def installed(self) -> bool:
        return bool(_which("kilo")) or self.cfg().exists() or self.vscode_cfg().parent.parent.exists()

    def _entry(self) -> dict:
        return {"type": "local", "command": server_cmd(), "enabled": True}

    def wire(self, log, dry_run):
        res: dict[str, Any] = {}
        path = self.cfg()
        if path.exists() or _which("kilo"):
            old = path.read_text(encoding="utf-8") if path.exists() else "{\n}\n"
            try:
                parsed = json.loads(_strip_jsonc(old)) if old.strip() else {}
            except ValueError:
                parsed = None
            if parsed is None:
                res["mcp"] = "skipped(unparseable kilo.jsonc)"
            elif BEGIN in old:
                res["mcp"] = "unchanged"
            elif "mcp" in parsed:
                res["mcp"] = "skipped(existing mcp block; add mempalace manually)"
            else:
                entry = json.dumps({SERVER: self._entry()}, indent=2).replace("\n", "\n  ")
                insert = f'\n  // {BEGIN}\n  "mcp": {entry},\n  // {END}'
                new = old.replace("{", "{" + insert, 1)
                backup(path, log, dry_run)
                log(f"add mcp.{SERVER} marker block to {path}")
                if not dry_run:
                    _atomic_write(path, new)
                res["mcp"] = "added"
        if self.vscode_cfg().parent.parent.exists():
            cmd = server_cmd()
            res["vscode_extension"] = _json_set_server(
                self.vscode_cfg(), "mcpServers", {"command": cmd[0], "args": cmd[1:], "disabled": False},
                log, dry_run, create=True)
        return res

    def unwire(self, log, dry_run):
        res: dict[str, Any] = {}
        path = self.cfg()
        if path.exists() and BEGIN in path.read_text(encoding="utf-8"):
            text = path.read_text(encoding="utf-8")
            backup(path, log, dry_run)
            log(f"remove mcp marker block from {path}")
            new = re.sub(r"\n\s*// " + re.escape(BEGIN) + r".*?// " + re.escape(END), "", text, flags=re.S)
            if not dry_run:
                _atomic_write(path, new)
            res["mcp"] = "removed"
        else:
            res["mcp"] = "absent"
        res["vscode_extension"] = _json_del_server(self.vscode_cfg(), "mcpServers", log, dry_run)
        return res

    def status(self):
        return {"installed": self.installed(),
                "wired": self.cfg().exists() and BEGIN in self.cfg().read_text(encoding="utf-8")}


class Pi(Harness):
    """Pi has no MCP: ship a skill whose script talks to the hub over HTTP."""

    name = "pi"

    def skill_dir(self) -> Path:
        return home() / ".agents" / "skills" / "mempalace-memory"

    def md(self) -> Path:
        return home() / ".pi" / "agent" / "AGENTS.md"

    def installed(self) -> bool:
        return bool(_which("pi")) or (home() / ".pi" / "agent").is_dir()

    def wire(self, log, dry_run):
        src = HERE / "resources" / "pi-skill"
        dst = self.skill_dir()
        log(f"install Pi skill {dst}")
        if not dry_run:
            dst.mkdir(parents=True, exist_ok=True)
            for f in src.iterdir():
                text = f.read_text(encoding="utf-8").replace("__PYTHON__", python_bin()).replace(
                    "__REPO__", str(HERE.parents[2]))
                (dst / f.name).write_text(text, encoding="utf-8")
                if f.suffix == ".py":
                    (dst / f.name).chmod(0o755)
            (dst / ".installed-by-decisions").write_text("decisions-mempalace\n", encoding="utf-8")
        return {"skill": str(dst), "instructions": _upsert_block(self.md(), identity_block("pi"), log, dry_run, create=True)}

    def unwire(self, log, dry_run):
        dst = self.skill_dir()
        res: dict[str, Any] = {"skill": "absent"}
        if (dst / ".installed-by-decisions").exists():
            log(f"remove Pi skill {dst}")
            if not dry_run:
                shutil.rmtree(dst)
            res["skill"] = "removed"
        res["instructions"] = _remove_block(self.md(), log, dry_run)
        return res

    def status(self):
        return {"installed": self.installed(), "wired": (self.skill_dir() / "SKILL.md").exists()}


HARNESSES: list[Harness] = [Claude(), Codex(), Cursor(), Kilo(), Pi()]


def _selected() -> list[Harness]:
    only = {s.strip() for s in (os.environ.get("DECISIONS_MEMPALACE_HARNESSES") or "").split(",") if s.strip()}
    return [h for h in HARNESSES if not only or h.name in only]


def wire_all(log: Log, *, dry_run: bool = False, previous: dict | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for h in _selected():
        if not h.installed():
            log(f"{h.name}: not installed, skipped")
            out[h.name] = {"installed": False}
            continue
        try:
            out[h.name] = {"installed": True, **h.wire(log, dry_run)}
        except Exception as exc:  # never let one harness break the rest
            log(f"{h.name}: wiring failed: {exc}")
            out[h.name] = {"installed": True, "error": str(exc)}
    return out


def unwire_all(log: Log, *, dry_run: bool = False, previous: dict | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for h in HARNESSES:
        try:
            out[h.name] = h.unwire(log, dry_run)
        except Exception as exc:
            log(f"{h.name}: unwire failed: {exc}")
            out[h.name] = {"error": str(exc)}
    return out


def status() -> dict[str, Any]:
    return {h.name: h.status() for h in HARNESSES}
