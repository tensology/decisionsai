"""Install / uninstall the Decisions shared-memory stack (local only).

    python -m distr.core.mempalace.installer install   [--dry-run] [--no-launchctl]
                                                       [--skip-package] [--skip-laya]
                                                       [--only hub,capture,harnesses]
    python -m distr.core.mempalace.installer uninstall [--dry-run] [--no-launchctl]
                                                       [--keep-harnesses]
    python -m distr.core.mempalace.installer status

What ``install`` does (each step idempotent, user-agnostic paths from $HOME):
1. pins ``mempalace==PINNED_VERSION`` in the running (Decisions) venv;
2. records the palace's embedder and installs a loopback-only hub LaunchAgent
   (``mempalace serve`` on 127.0.0.1, bearer token 0600 under ~/.mempalace/server);
3. installs the frustration-capture (every 10 min) and nightly review
   (02:00, every second night) LaunchAgents and, when possible, Laya;
4. connects installed harnesses (Claude Code, Codex, Cursor, Kilo, Pi) after
   backing up each config. See ``harnesses.py``.

Off switches (env wins; also honoured at runtime by every component):
- ``DECISIONS_MEMPALACE_HUB=0``             no hub LaunchAgent (adapter stays in-process)
- ``DECISIONS_MEMPALACE_HARNESS_WIRING=0``  do not touch any harness config
- ``DECISIONS_FRUSTRATION_CAPTURE=0``       no capture / nightly agents (settings key
                                             ``frustration_capture`` does the same)
Nothing leaves the machine: hub is loopback-only, data stays under ~/.decisions.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any

from distr.core.mempalace.paths import default_palace_path

PINNED_VERSION = "3.11.0"
HUB_LABEL = "com.tensology.mempalace-hub"
CAPTURE_LABEL = "com.tensology.decisions-selfimprove-capture"
NIGHTLY_LABEL = "com.tensology.decisions-selfimprove-nightly"
DEFAULT_HUB_PORT = 8766  # 8765 is Decisions' own local server

HERE = Path(__file__).resolve().parent
RESOURCES = HERE / "resources"
REPO_ROOT = HERE.parents[2]


def _off(name: str) -> bool:
    return (os.environ.get(name) or "").strip().lower() in {"0", "false", "no", "off", "disabled"}


def hub_enabled() -> bool:
    return not _off("DECISIONS_MEMPALACE_HUB")


def harness_wiring_enabled() -> bool:
    return not _off("DECISIONS_MEMPALACE_HARNESS_WIRING")


def capture_enabled() -> bool:
    from distr.core.mempalace.selfimprove.config import capture_enabled as _ce

    return _ce()


def home() -> Path:
    return Path(os.path.expanduser("~"))


def launch_agents_dir() -> Path:
    return home() / "Library" / "LaunchAgents"


def log_dir() -> Path:
    return home() / "Library" / "Logs" / "mempalace"


def bin_dir() -> Path:
    return home() / ".decisions" / "mempalace" / "bin"


def state_path() -> Path:
    return home() / ".decisions" / "mempalace" / "install-state.json"


def hub_port() -> int:
    try:
        return int(os.environ.get("DECISIONS_MEMPALACE_HUB_PORT") or DEFAULT_HUB_PORT)
    except ValueError:
        return DEFAULT_HUB_PORT


def _load_state() -> dict[str, Any]:
    try:
        return json.loads(state_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save_state(state: dict[str, Any]) -> None:
    state_path().parent.mkdir(parents=True, exist_ok=True)
    state_path().write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")


class Plan:
    def __init__(self, dry_run: bool) -> None:
        self.dry_run = dry_run
        self.steps: list[str] = []

    def log(self, msg: str) -> None:
        line = ("[dry-run] " if self.dry_run else "") + msg
        self.steps.append(line)
        print(line, flush=True)


# ── 1. package ────────────────────────────────────────────────────────────────

def installed_version() -> str:
    try:
        from importlib.metadata import version

        return version("mempalace")
    except Exception:
        return ""


def ensure_package(plan: Plan) -> None:
    cur = installed_version()
    if cur == PINNED_VERSION:
        plan.log(f"mempalace {cur} already installed in {sys.prefix}")
        return
    plan.log(f"pip install mempalace=={PINNED_VERSION} into {sys.prefix} (was {cur or 'absent'})")
    if plan.dry_run:
        return
    subprocess.run(
        [sys.executable, "-m", "pip", "install", "--quiet", f"mempalace=={PINNED_VERSION}"],
        check=True,
        timeout=1800,
    )


# ── launchd helpers ───────────────────────────────────────────────────────────

def _uid() -> int:
    return os.getuid()


def _launchctl(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["launchctl", *args], capture_output=True, text=True, timeout=60)


def agent_loaded(label: str) -> bool:
    if platform.system() != "Darwin":
        return False
    return _launchctl("print", f"gui/{_uid()}/{label}").returncode == 0


def _render(template: str, values: dict[str, str]) -> str:
    text = (RESOURCES / "launchd" / template).read_text(encoding="utf-8")
    for key, val in values.items():
        text = text.replace(f"__{key}__", val)
    return text


def _install_agent(plan: Plan, label: str, content: str, *, launchctl: bool) -> Path:
    path = launch_agents_dir() / f"{label}.plist"
    changed = not path.exists() or path.read_text(encoding="utf-8") != content
    plan.log(f"{'write' if changed else 'keep'} {path}")
    if not plan.dry_run:
        path.parent.mkdir(parents=True, exist_ok=True)
        log_dir().mkdir(parents=True, exist_ok=True)
        if changed:
            path.write_text(content, encoding="utf-8")
    if not launchctl or platform.system() != "Darwin":
        plan.log(f"launchctl skipped for {label}")
        return path
    if plan.dry_run:
        plan.log(f"launchctl bootstrap gui/{_uid()} {path}")
        return path
    if agent_loaded(label):
        if not changed:
            plan.log(f"{label} already loaded")
            return path
        _launchctl("bootout", f"gui/{_uid()}/{label}")
        time.sleep(1)
    res = _launchctl("bootstrap", f"gui/{_uid()}", str(path))
    plan.log(f"launchctl bootstrap {label}: rc={res.returncode} {res.stderr.strip()[:200]}")
    return path


def _remove_agent(plan: Plan, label: str, *, launchctl: bool) -> None:
    path = launch_agents_dir() / f"{label}.plist"
    if launchctl and platform.system() == "Darwin" and agent_loaded(label):
        plan.log(f"launchctl bootout {label}")
        if not plan.dry_run:
            _launchctl("bootout", f"gui/{_uid()}/{label}")
    if path.exists():
        plan.log(f"remove {path}")
        if not plan.dry_run:
            path.unlink()


def _path_env() -> str:
    parts = [str(Path(sys.executable).parent), "/opt/homebrew/bin", "/usr/local/bin",
             str(home() / ".local" / "bin"), "/usr/bin", "/bin", "/usr/sbin", "/sbin"]
    seen: list[str] = []
    for p in parts:
        if p not in seen:
            seen.append(p)
    return ":".join(seen)


# ── 2. hub ────────────────────────────────────────────────────────────────────

def hub_healthy(port: int | None = None, timeout: float = 2.0) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port or hub_port()}/healthz", timeout=timeout) as r:
            return r.status == 200
    except Exception:
        return False


def record_embedder(plan: Plan, palace: Path) -> None:
    from distr.core.mempalace import hub as _hub

    if not (palace / "chroma.sqlite3").exists():
        plan.log(f"palace {palace} empty; embedder is recorded on first write")
        return
    if _hub.hub_is_up(str(palace)):
        plan.log("hub already owns the palace; embedder step skipped")
        return
    plan.log(f"record embedder identity (minilm) for {palace}")
    if plan.dry_run:
        return
    subprocess.run(
        [sys.executable, "-m", "mempalace", "--palace", str(palace), "palace", "set-embedder", "--model", "minilm"],
        capture_output=True, text=True, timeout=300, check=False,
    )


def install_hub(plan: Plan, *, launchctl: bool) -> None:
    palace = default_palace_path()
    plan.log(f"palace: {palace}")
    if not plan.dry_run:
        palace.mkdir(parents=True, exist_ok=True)
        bin_dir().mkdir(parents=True, exist_ok=True)
    record_embedder(plan, palace)
    script = bin_dir() / "mempalace-hub.sh"
    plan.log(f"install {script}")
    if not plan.dry_run:
        shutil.copyfile(RESOURCES / "mempalace-hub.sh", script)
        script.chmod(0o755)
    content = _render(
        f"{HUB_LABEL}.plist.template",
        {"HUB_SCRIPT": str(script), "PALACE": str(palace), "PYTHON": sys.executable,
         "PORT": str(hub_port()), "LOGDIR": str(log_dir())},
    )
    _install_agent(plan, HUB_LABEL, content, launchctl=launchctl)
    if launchctl and not plan.dry_run and platform.system() == "Darwin":
        for _ in range(60):
            if hub_healthy():
                plan.log(f"hub healthy on 127.0.0.1:{hub_port()}")
                break
            time.sleep(1)
        else:
            plan.log("WARNING: hub did not become healthy within 60s; see ~/Library/Logs/mempalace/hub.err.log")


# ── 3. self-improvement agents ────────────────────────────────────────────────

def install_selfimprove(plan: Plan, *, launchctl: bool, skip_laya: bool) -> None:
    from distr.core.mempalace.selfimprove import config as sic

    if not skip_laya:
        sic.ensure_classifier(plan.log, dry_run=plan.dry_run)
    else:
        plan.log("Laya install skipped (--skip-laya); capture uses regex-only unless Laya is already present")
    if not plan.dry_run:
        sic.ensure_dirs()
    values = {"PYTHON": sys.executable, "REPO": str(REPO_ROOT), "PATH": _path_env(), "LOGDIR": str(log_dir())}
    _install_agent(plan, CAPTURE_LABEL, _render(f"{CAPTURE_LABEL}.plist.template", values), launchctl=launchctl)
    _install_agent(plan, NIGHTLY_LABEL, _render(f"{NIGHTLY_LABEL}.plist.template", values), launchctl=launchctl)


# ── commands ──────────────────────────────────────────────────────────────────

def install(args: argparse.Namespace) -> dict[str, Any]:
    plan = Plan(args.dry_run)
    only = set((args.only or "package,hub,capture,harnesses").split(","))
    launchctl = not args.no_launchctl
    state = _load_state()
    if "package" in only and not args.skip_package:
        ensure_package(plan)
    if "hub" in only:
        if hub_enabled():
            install_hub(plan, launchctl=launchctl)
            state["hub"] = {"label": HUB_LABEL, "port": hub_port()}
        else:
            plan.log("hub disabled by DECISIONS_MEMPALACE_HUB=0")
    if "capture" in only:
        if capture_enabled():
            install_selfimprove(plan, launchctl=launchctl, skip_laya=args.skip_laya)
            state["selfimprove"] = {"labels": [CAPTURE_LABEL, NIGHTLY_LABEL]}
        else:
            plan.log("frustration capture disabled (DECISIONS_FRUSTRATION_CAPTURE=0 or settings)")
    if "harnesses" in only:
        if harness_wiring_enabled():
            from distr.core.mempalace import harnesses

            state["harnesses"] = harnesses.wire_all(plan.log, dry_run=plan.dry_run, previous=state.get("harnesses"))
        else:
            plan.log("harness wiring disabled by DECISIONS_MEMPALACE_HARNESS_WIRING=0")
    if not plan.dry_run:
        state["installed_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        state["mempalace_version"] = installed_version()
        _save_state(state)
        try:  # release the first-run bootstrap lock (see firstrun.py)
            (Path.home() / ".decisions" / "mempalace" / "firstrun.lock").unlink()
        except OSError:
            pass
    return {"steps": plan.steps}


def uninstall(args: argparse.Namespace) -> dict[str, Any]:
    plan = Plan(args.dry_run)
    launchctl = not args.no_launchctl
    state = _load_state()
    for label in (NIGHTLY_LABEL, CAPTURE_LABEL, HUB_LABEL):
        _remove_agent(plan, label, launchctl=launchctl)
    if not args.keep_harnesses:
        from distr.core.mempalace import harnesses

        harnesses.unwire_all(plan.log, dry_run=plan.dry_run, previous=state.get("harnesses"))
    plan.log("data kept: palace, journal, proposals (delete ~/.decisions/mempalace and ~/.decisions/self-improve manually if wanted)")
    if not plan.dry_run:
        state.update({"hub": None, "selfimprove": None, "uninstalled_at": time.strftime("%Y-%m-%dT%H:%M:%S%z")})
        if not args.keep_harnesses:
            state["harnesses"] = None
        _save_state(state)
    return {"steps": plan.steps}


def status() -> dict[str, Any]:
    from distr.core.mempalace import hub as _hub

    palace = default_palace_path()
    out: dict[str, Any] = {
        "mempalace_version": installed_version(),
        "pinned": PINNED_VERSION,
        "palace": str(palace),
        "hub": {"enabled": hub_enabled(), "port": hub_port(), "healthy": hub_healthy(),
                "loaded": agent_loaded(HUB_LABEL), "discovered": _hub.hub_is_up(str(palace)),
                "journal_pending": _hub.journal_pending(str(palace))},
        "capture": {"enabled": capture_enabled(), "capture_loaded": agent_loaded(CAPTURE_LABEL),
                    "nightly_loaded": agent_loaded(NIGHTLY_LABEL)},
        "harness_wiring_enabled": harness_wiring_enabled(),
        "state": _load_state(),
    }
    try:
        from distr.core.mempalace import harnesses

        out["harnesses"] = harnesses.status()
    except Exception as exc:  # pragma: no cover
        out["harnesses"] = {"error": str(exc)}
    return out


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m distr.core.mempalace.installer")
    sub = p.add_subparsers(dest="cmd", required=True)
    pi = sub.add_parser("install")
    pi.add_argument("--dry-run", action="store_true")
    pi.add_argument("--no-launchctl", action="store_true", help="write plists but do not load them")
    pi.add_argument("--skip-package", action="store_true")
    pi.add_argument("--skip-laya", action="store_true")
    pi.add_argument("--only", help="comma list of package,hub,capture,harnesses")
    pu = sub.add_parser("uninstall")
    pu.add_argument("--dry-run", action="store_true")
    pu.add_argument("--no-launchctl", action="store_true")
    pu.add_argument("--keep-harnesses", action="store_true")
    sub.add_parser("status")
    args = p.parse_args(argv)
    if args.cmd == "install":
        install(args)
    elif args.cmd == "uninstall":
        uninstall(args)
    else:
        print(json.dumps(status(), indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
