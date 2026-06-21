"""
macOS permission setup — probe desktop-tool access and guide the user to fix it.

Run:
  ./decisions --permissions
  ./decisions --permissions --interactive
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import platform
import shutil
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from typing import Any

logger = logging.getLogger(__name__)

PERMISSIONS_DISMISS_PATH = os.path.expanduser("~/.decisions/run/macos_permissions_dismissed")

PRIVACY_PANES = {
    "accessibility": (
        "x-apple.systempreferences:com.apple.settings.PrivacySecurity.extension"
        "?Privacy_Accessibility"
    ),
    "screen_recording": (
        "x-apple.systempreferences:com.apple.settings.PrivacySecurity.extension"
        "?Privacy_ScreenCapture"
    ),
    "microphone": (
        "x-apple.systempreferences:com.apple.settings.PrivacySecurity.extension"
        "?Privacy_Microphone"
    ),
    "automation": (
        "x-apple.systempreferences:com.apple.settings.PrivacySecurity.extension"
        "?Privacy_Automation"
    ),
    "files": (
        "x-apple.systempreferences:com.apple.settings.PrivacySecurity.extension"
        "?Privacy_FilesAndFolders"
    ),
}


def is_permissions_setup_dismissed() -> bool:
    return os.path.isfile(PERMISSIONS_DISMISS_PATH)


def mark_permissions_setup_dismissed() -> None:
    os.makedirs(os.path.dirname(PERMISSIONS_DISMISS_PATH), exist_ok=True)
    with open(PERMISSIONS_DISMISS_PATH, "w", encoding="utf-8") as handle:
        handle.write("1\n")


@dataclass
class PermissionItem:
    id: str
    title: str
    ok: bool
    detail: str
    settings_pane: str | None = None
    enable_in_settings: str | None = None
    can_prompt: bool = False
    prompt_target: str | None = None


def _project_root() -> str:
    try:
        from distr.core.paths import CORE_DIR

        return os.path.abspath(CORE_DIR)
    except Exception:
        return os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


def sidecar_executable() -> str:
    return os.path.join(_project_root(), "sidecar", "dist", "decisionsai-sidecar")


def ensure_sidecar_running() -> bool:
    from distr.core.agent.tools.input.sidecar_http import is_sidecar_reachable

    if is_sidecar_reachable():
        return True

    project_root = _project_root()
    sidecar_sh = os.path.join(project_root, "bin", "decisions-sidecar.sh")
    if os.path.isfile(sidecar_sh):
        try:
            subprocess.run(
                ["/bin/bash", sidecar_sh, project_root],
                cwd=project_root,
                check=False,
                capture_output=True,
                timeout=45,
            )
        except Exception as exc:
            logger.debug("sidecar start failed: %s", exc)

    for _ in range(24):
        if is_sidecar_reachable():
            return True
        time.sleep(0.25)

    return _start_sidecar_process(project_root)


def _start_sidecar_process(project_root: str) -> bool:
    """Last-resort: spawn the sidecar binary directly from Python."""
    from distr.core.agent.tools.input.sidecar_http import is_sidecar_reachable

    if is_sidecar_reachable():
        return True

    sidecar_bin = os.path.join(project_root, "sidecar", "dist", "decisionsai-sidecar")
    if not os.path.isfile(sidecar_bin):
        return False

    log_dir = os.path.expanduser("~/.decisions/logs")
    os.makedirs(log_dir, exist_ok=True)
    env = os.environ.copy()
    venv_py = os.path.expanduser("~/.virtualenvs/decisions/bin/python")
    if os.path.isfile(venv_py):
        env["DECISIONS_PYTHON"] = venv_py
    elif sys.executable:
        env.setdefault("DECISIONS_PYTHON", sys.executable)

    try:
        with open(os.path.join(log_dir, "sidecar.log"), "a", encoding="utf-8") as logf:
            subprocess.Popen(
                [sidecar_bin, "--local"],
                stdout=logf,
                stderr=subprocess.STDOUT,
                cwd=project_root,
                env=env,
                start_new_session=True,
            )
    except Exception as exc:
        logger.debug("direct sidecar spawn failed: %s", exc)
        return False

    for _ in range(20):
        time.sleep(0.25)
        if is_sidecar_reachable():
            return True
    return is_sidecar_reachable()


def ensure_cliclick_installed() -> str | None:
    """Install cliclick via Homebrew when missing (macOS mouse control)."""
    if platform.system() != "Darwin":
        return None
    try:
        path = (
            subprocess.check_output(["which", "cliclick"], text=True, stderr=subprocess.DEVNULL)
            .strip()
        )
        if path:
            return path
    except Exception:
        pass
    if not shutil.which("brew"):
        return None
    try:
        subprocess.run(
            ["brew", "install", "cliclick"],
            check=False,
            capture_output=True,
            timeout=120,
        )
        return (
            subprocess.check_output(["which", "cliclick"], text=True, stderr=subprocess.DEVNULL)
            .strip()
        )
    except Exception as exc:
        logger.debug("cliclick install failed: %s", exc)
        return None


def _probe_cliclick_cursor() -> tuple[bool, str]:
    try:
        out = subprocess.check_output(["cliclick", "p"], text=True, stderr=subprocess.DEVNULL, timeout=3)
        parts = out.strip().split(",")
        if len(parts) == 2:
            return True, "ok (cliclick)"
    except Exception as exc:
        return False, str(exc)
    return False, "cliclick returned unexpected output"


def _probe_sidecar_via_http() -> dict[str, Any]:
    """Functional probes when /health has no permissions block (older sidecar)."""
    from distr.core.agent.tools.input.sidecar_http import call_sidecar_tool

    perms: dict[str, Any] = {}
    # Do not call capture_screen here — it runs screencapture and retriggers macOS prompts.
    perms["screen_recording"] = {
        "ok": False,
        "verified": False,
        "detail": "use sidecar /health permissions (no screenshot probe)",
    }

    try:
        tree = call_sidecar_tool("get_window_tree", {"depth": 2}, timeout=15)
        if tree.get("error"):
            perms["automation"] = {"ok": False, "detail": str(tree.get("error"))}
        else:
            elements = tree.get("elements") or []
            title = tree.get("window_title") or "frontmost window"
            perms["automation"] = {
                "ok": True,
                "detail": f"{title} ({len(elements)} elements)",
            }
    except Exception as exc:
        msg = str(exc)
        denied = any(
            token in msg.lower()
            for token in ("not allowed", "assistive", "denied", "automation")
        )
        perms["automation"] = {"ok": False, "detail": msg if denied else msg}

    cliclick_ok, cliclick_detail = _probe_cliclick_cursor()
    if cliclick_ok:
        perms["accessibility"] = {"ok": True, "detail": cliclick_detail, "via": "cliclick"}
    else:
        try:
            pos = call_sidecar_tool("get_cursor_pos", {}, timeout=10)
            perms["accessibility"] = {
                "ok": "x" in pos and "y" in pos,
                "detail": "ok" if "x" in pos else str(pos),
                "via": "sidecar",
            }
        except Exception as exc:
            perms["accessibility"] = {
                "ok": False,
                "detail": cliclick_detail or str(exc),
                "via": "none",
            }

    return perms


def open_privacy_pane(pane: str) -> bool:
    url = PRIVACY_PANES.get(pane)
    if not url:
        return False
    try:
        subprocess.run(["open", url], check=False, timeout=5)
        return True
    except Exception:
        return False


def _probe_python_accessibility(*, prompt: bool = False) -> tuple[bool, str]:
    try:
        from ApplicationServices import AXIsProcessTrusted, AXIsProcessTrustedWithOptions
        from Foundation import NSDictionary

        if prompt:
            options = NSDictionary.dictionaryWithObjects_forKeys_(
                [True],
                ["AXTrustedCheckOptionPrompt"],
            )
            ok = bool(AXIsProcessTrustedWithOptions(options))
        else:
            ok = bool(AXIsProcessTrusted())
        return ok, "ok" if ok else "Not trusted — enable Python in Accessibility"
    except Exception as exc:
        return False, f"Could not check Accessibility: {exc}"


def _probe_python_screen_recording(*, prompt: bool = False) -> tuple[bool, str]:
    try:
        from Quartz import CGPreflightScreenCaptureAccess, CGRequestScreenCaptureAccess

        if prompt:
            ok = bool(CGRequestScreenCaptureAccess())
        else:
            ok = bool(CGPreflightScreenCaptureAccess())
        return ok, "ok" if ok else "Not granted — enable Python in Screen Recording"
    except Exception as exc:
        return False, f"Could not check Screen Recording: {exc}"


def _probe_python_microphone(*, prompt: bool = False) -> tuple[bool, str]:
    """Use the same audio stack as voice input (sounddevice), not AVFoundation status."""
    try:
        import sounddevice as sd

        if prompt:
            import numpy as np

            rec = sd.rec(int(0.1 * 16000), samplerate=16000, channels=1, dtype=np.float32)
            sd.wait()
            return True, "ok"

        default = sd.default.device
        input_index = None
        if hasattr(default, "input"):
            input_index = default.input
        elif isinstance(default, (list, tuple)) and default:
            input_index = default[0]
        else:
            try:
                input_index = default[0]
            except Exception:
                input_index = default
        if input_index is None:
            return False, "No input device selected"
        input_index = int(input_index)
        if input_index < 0:
            return False, "No input device selected"
        info = sd.query_devices(input_index)
        name = info.get("name") if isinstance(info, dict) else getattr(info, "name", "")
        return True, f"ok ({name})" if name else "ok"
    except Exception as exc:
        return False, f"Microphone unavailable: {exc}"


def request_python_permission(kind: str) -> tuple[bool, str]:
    if kind == "accessibility":
        return _probe_python_accessibility(prompt=True)
    if kind == "screen_recording":
        return _probe_python_screen_recording(prompt=True)
    if kind == "microphone":
        return _probe_python_microphone(prompt=True)
    return False, f"Unknown permission kind: {kind}"


def collect_permission_report(*, start_sidecar: bool = True) -> dict[str, Any]:
    if platform.system() != "Darwin":
        return {"platform": platform.system(), "supported": False, "items": []}

    python_path = sys.executable
    sidecar_path = sidecar_executable()
    sidecar_ok = False
    sidecar_perms: dict[str, Any] = {}

    if start_sidecar:
        sidecar_ok = ensure_sidecar_running()

    cliclick_path = ensure_cliclick_installed() or ""
    if not cliclick_path:
        try:
            cliclick_path = (
                subprocess.check_output(["which", "cliclick"], text=True, stderr=subprocess.DEVNULL)
                .strip()
            )
        except Exception:
            cliclick_path = ""

    if sidecar_ok:
        try:
            from distr.core.agent.tools.input.sidecar_http import sidecar_health

            health = sidecar_health(timeout=3.0) or {}
            sidecar_perms = health.get("permissions") or {}
            if not sidecar_perms:
                sidecar_perms = _probe_sidecar_via_http()
        except Exception as exc:
            sidecar_perms = {"error": str(exc)}

    py_a11y_ok, py_a11y_detail = _probe_python_accessibility()
    py_scr_ok, py_scr_detail = _probe_python_screen_recording()
    py_mic_ok, py_mic_detail = _probe_python_microphone()

    sidecar_scr = (sidecar_perms.get("screen_recording") or {}).get("ok")
    sidecar_auto = (sidecar_perms.get("automation") or {}).get("ok")
    sidecar_a11y = (sidecar_perms.get("accessibility") or {}).get("ok")
    sidecar_a11y_via = (sidecar_perms.get("accessibility") or {}).get("via") or "sidecar"

    items: list[PermissionItem] = [
        PermissionItem(
            id="sidecar_running",
            title="Desktop automation service (sidecar)",
            ok=sidecar_ok,
            detail="Running on port 11435" if sidecar_ok else "Not responding — check ~/.decisions/logs/sidecar.log",
            settings_pane=None,
            enable_in_settings=None,
        ),
        PermissionItem(
            id="sidecar_screen_recording",
            title="Screenshots (sidecar)",
            ok=bool(sidecar_scr) if sidecar_ok else False,
            detail=str((sidecar_perms.get("screen_recording") or {}).get("detail") or "Sidecar not running"),
            settings_pane="screen_recording",
            enable_in_settings=f"decisionsai-sidecar\n{sidecar_path}",
        ),
        PermissionItem(
            id="sidecar_automation",
            title="UI tree & keystrokes (Automation)",
            ok=bool(sidecar_auto) if sidecar_ok else False,
            detail=str((sidecar_perms.get("automation") or {}).get("detail") or "Sidecar not running"),
            settings_pane=None if sidecar_auto else "automation",
            enable_in_settings=None
            if sidecar_auto
            else (
                "decisionsai-sidecar → System Events\n"
                f"Binary: {sidecar_path}"
            ),
        ),
        PermissionItem(
            id="sidecar_accessibility",
            title="Mouse & keyboard control",
            ok=bool(sidecar_a11y) if sidecar_ok else False,
            detail=str((sidecar_perms.get("accessibility") or {}).get("detail") or "Sidecar not running"),
            settings_pane=None if sidecar_a11y else "accessibility",
            enable_in_settings=None
            if sidecar_a11y
            else (
                f"{'cliclick' if sidecar_a11y_via == 'cliclick' else 'python3'} "
                f"(used by sidecar for clicks)\n"
                f"{cliclick_path or 'install: brew install cliclick'}"
            ),
        ),
        PermissionItem(
            id="python_accessibility",
            title="Accessibility (Python app)",
            ok=py_a11y_ok,
            detail=py_a11y_detail,
            settings_pane="accessibility",
            enable_in_settings=f"Python\n{python_path}",
            can_prompt=True,
            prompt_target="accessibility",
        ),
        PermissionItem(
            id="python_screen_recording",
            title="Screen Recording (Python app)",
            ok=py_scr_ok,
            detail=py_scr_detail,
            settings_pane="screen_recording",
            enable_in_settings=f"Python\n{python_path}",
            can_prompt=True,
            prompt_target="screen_recording",
        ),
        PermissionItem(
            id="python_microphone",
            title="Microphone (voice input)",
            ok=py_mic_ok,
            detail=py_mic_detail,
            settings_pane="microphone",
            enable_in_settings=f"Python\n{python_path}",
            can_prompt=True,
            prompt_target="microphone",
        ),
        PermissionItem(
            id="files_desktop",
            title="Files (Desktop/Documents)",
            ok=os.access(os.path.expanduser("~/Desktop"), os.R_OK | os.W_OK),
            detail="Can read/write Desktop",
            settings_pane="files",
            enable_in_settings="Python — allow Desktop & Documents if prompted",
        ),
    ]

    item_dicts = [asdict(i) for i in items]
    setup_needed = permissions_setup_needed({"supported": True, "items": item_dicts})

    return {
        "platform": "darwin",
        "supported": True,
        "all_ok": not setup_needed,
        "setup_needed": setup_needed,
        "python_executable": python_path,
        "sidecar_executable": sidecar_path,
        "cliclick_path": cliclick_path or None,
        "items": item_dicts,
    }


def _failure_requires_user_action(item: dict[str, Any]) -> bool:
    """Ignore probe/tooling errors — only surface real macOS privacy gaps."""
    if item.get("ok"):
        return False
    detail = str(item.get("detail") or "").lower()
    tooling_markers = (
        "modulenotfounderror",
        "no module named",
        "traceback (most recent call last)",
        "int() argument must be",
    )
    if any(marker in detail for marker in tooling_markers):
        return False
    item_id = str(item.get("id"))
    if item_id == "sidecar_running":
        return True
    if item_id == "sidecar_screen_recording" and "verified on first screenshot" in detail:
        return False
    privacy_markers = (
        "not allowed",
        "denied",
        "assistive",
        "enable screen recording",
        "automation denied",
        "not trusted",
        "not granted",
        "microphone unavailable",
        "no input device",
    )
    if any(marker in detail for marker in privacy_markers):
        return True
    if detail in ("ok", ""):
        return False
    # Ambiguous short errors during boot (e.g. "exit status 1") are not actionable yet.
    if detail.strip() in ("exit status 1", "exit status 1."):
        return False
    return item_id in (
        "sidecar_screen_recording",
        "sidecar_automation",
        "sidecar_accessibility",
        "python_microphone",
    )


def permissions_setup_needed(report: dict[str, Any] | None = None) -> bool:
    """
    True when the user still needs to fix something before desktop tools work.

    Skips the dialog when core capabilities already work, or failures are probe noise.
    """
    if is_permissions_setup_dismissed():
        return False
    if report is None:
        report = collect_permission_report()
    if not report.get("supported"):
        return False

    items = {str(i.get("id")): i for i in (report.get("items") or [])}

    if not items.get("sidecar_running", {}).get("ok"):
        return True

    if _core_capabilities_ok(items):
        return False

    return any(
        _failure_requires_user_action(item)
        for item in actionable_permission_failures(report)
    )


def _core_capabilities_ok(items: dict[str, dict[str, Any]]) -> bool:
    screen_ok = bool(
        items.get("sidecar_screen_recording", {}).get("ok")
        or items.get("python_screen_recording", {}).get("ok")
    )
    control_ok = bool(
        items.get("sidecar_accessibility", {}).get("ok")
        or items.get("python_accessibility", {}).get("ok")
    )
    auto_ok = bool(items.get("sidecar_automation", {}).get("ok"))
    mic_ok = bool(items.get("python_microphone", {}).get("ok"))
    return screen_ok and control_ok and auto_ok and mic_ok


def actionable_permission_failures(report: dict[str, Any]) -> list[dict[str, Any]]:
    """Items that failed and still need user attention (for the setup dialog)."""
    items = report.get("items") or []
    by_id = {str(i.get("id")): i for i in items}
    failures: list[dict[str, Any]] = []
    for item in items:
        if item.get("ok"):
            continue
        item_id = str(item.get("id"))
        if item_id == "files_desktop":
            continue
        if item_id == "python_screen_recording" and by_id.get("sidecar_screen_recording", {}).get("ok"):
            continue
        if item_id == "python_accessibility" and by_id.get("sidecar_accessibility", {}).get("ok"):
            continue
        if not _failure_requires_user_action(item):
            continue
        failures.append(item)
    return failures


def user_facing_permission_failures(report: dict[str, Any]) -> list[dict[str, Any]]:
    """
    Collapse internal sidecar/python probes into a short list for the setup dialog.

    I am showing grouped desktop + voice permissions so the user can enable Decisions
    once in System Settings without reading implementation paths.
    """
    items = {str(i.get("id")): i for i in (report.get("items") or [])}
    failures: list[dict[str, Any]] = []

    sidecar_ok = bool(items.get("sidecar_running", {}).get("ok"))
    screen_ok = bool(
        items.get("sidecar_screen_recording", {}).get("ok")
        or items.get("python_screen_recording", {}).get("ok")
    )
    control_ok = bool(
        items.get("sidecar_accessibility", {}).get("ok")
        or items.get("python_accessibility", {}).get("ok")
    )
    auto_ok = bool(items.get("sidecar_automation", {}).get("ok"))
    desktop_ok = sidecar_ok and screen_ok and control_ok and auto_ok

    if not desktop_ok:
        if not sidecar_ok:
            detail = "Desktop tools are still starting. Wait a moment, then click Check again."
        else:
            missing: list[str] = []
            if not screen_ok:
                missing.append("Screen Recording")
            if not control_ok:
                missing.append("Accessibility")
            if not auto_ok:
                missing.append("Automation")
            detail = (
                f"Turn on Decisions for {', '.join(missing)}."
                if missing
                else "Finish enabling Decisions in System Settings."
            )

        failures.append(
            {
                "id": "desktop_control",
                "title": "Screenshots & desktop control",
                "ok": False,
                "detail": detail,
                "settings_pane": "screen_recording",
                "settings_panes": ["screen_recording", "accessibility", "automation"],
                "enable_in_settings": (
                    "In Privacy & Security, enable Decisions (decisionsai-sidecar) for "
                    "Screen Recording, Accessibility, and Automation."
                ),
                "can_prompt": False,
            }
        )

    mic = items.get("python_microphone") or {}
    if not mic.get("ok") and _failure_requires_user_action(mic):
        failures.append(
            {
                "id": "voice_input",
                "title": "Microphone (voice input)",
                "ok": False,
                "detail": "Turn on Decisions for Microphone if you use voice.",
                "settings_pane": "microphone",
                "settings_panes": ["microphone"],
                "enable_in_settings": "In Privacy & Security, enable Decisions (Python) for Microphone.",
                "can_prompt": True,
                "prompt_target": "microphone",
            }
        )

    return failures


def format_report_text(report: dict[str, Any]) -> str:
    if not report.get("supported"):
        return f"Permission setup is only available on macOS (this system: {report.get('platform')})."

    lines = [
        "DecisionsAI — macOS permission check",
        "====================================",
        "",
    ]
    for item in report.get("items") or []:
        mark = "OK" if item.get("ok") else "MISSING"
        lines.append(f"[{mark}] {item.get('title')}")
        if item.get("detail"):
            lines.append(f"       {item.get('detail')}")
        if not item.get("ok") and item.get("enable_in_settings"):
            lines.append("       Enable in System Settings:")
            for part in str(item["enable_in_settings"]).splitlines():
                lines.append(f"         {part}")
        lines.append("")

    if report.get("all_ok"):
        lines.append("All checks passed — desktop tools should work.")
    elif not report.get("setup_needed"):
        lines.append("All required checks passed — desktop tools should work.")
    else:
        lines.append("Run guided setup:  ./decisions --permissions --interactive")
    return "\n".join(lines)


def run_interactive_setup() -> int:
    print(format_report_text(collect_permission_report()))
    report = collect_permission_report()
    failed = [i for i in report.get("items") or [] if not i.get("ok")]

    if not failed:
        print("\nNothing to fix.")
        return 0

    print("\nGuided setup")
    print("------------")
    print("For each missing item, System Settings will open. Enable the listed app, then press Enter.\n")

    for item in failed:
        if item.get("id") == "sidecar_running":
            print("Starting sidecar...")
            ensure_sidecar_running()
            continue

        print(f"\n→ {item.get('title')}")
        if item.get("enable_in_settings"):
            print("  Enable:")
            for line in str(item["enable_in_settings"]).splitlines():
                print(f"    {line}")

        if item.get("can_prompt") and item.get("prompt_target"):
            try:
                answer = input("  Trigger macOS permission prompt now? [Y/n] ").strip().lower()
            except EOFError:
                answer = "n"
            if answer in ("", "y", "yes"):
                ok, detail = request_python_permission(str(item["prompt_target"]))
                print(f"  Prompt result: {detail}")
                if ok:
                    continue

        pane = item.get("settings_pane")
        if pane:
            open_privacy_pane(str(pane))
            try:
                input("  Press Enter after enabling in System Settings...")
            except EOFError:
                pass

    print("\nRe-checking...")
    print(format_report_text(collect_permission_report()))
    return 0 if collect_permission_report().get("all_ok") else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="DecisionsAI macOS permission setup")
    parser.add_argument("--json", action="store_true", help="Print JSON report")
    parser.add_argument("--interactive", action="store_true", help="Guided setup walkthrough")
    parser.add_argument("--open-missing", action="store_true", help="Open System Settings for each failure")
    parser.add_argument("--prompt", action="store_true", help="Trigger macOS prompts for Python permissions")
    args = parser.parse_args(argv)

    if args.prompt:
        for kind in ("accessibility", "screen_recording", "microphone"):
            ok, detail = request_python_permission(kind)
            print(f"{kind}: {'ok' if ok else 'missing'} — {detail}")

    report = collect_permission_report()

    if args.open_missing:
        opened: set[str] = set()
        for item in report.get("items") or []:
            if item.get("ok"):
                continue
            pane = item.get("settings_pane")
            if pane and pane not in opened:
                open_privacy_pane(str(pane))
                opened.add(str(pane))

    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(format_report_text(report))

    if args.interactive:
        return run_interactive_setup()

    return 0 if not permissions_setup_needed(collect_permission_report()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
