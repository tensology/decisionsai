"""Single entrypoint to bootstrap ECC, Ponytail/Fallow, browser/content, and RTK hooks."""

from __future__ import annotations

import os
import sys
import json
import threading
import time
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_maintenance_thread: threading.Thread | None = None
_maintenance_lock = threading.Lock()


def _app_version() -> str:
    try:
        return (PROJECT_ROOT / "VERSION").read_text(encoding="utf-8").strip() or "unknown"
    except OSError:
        return "unknown"


def _maintenance_is_current(home: Path) -> bool:
    """Avoid reinstalling harnesses on every desktop restart.

    Startup maintenance copies large skill trees and can invoke external plugin
    installers.  It is needed once for each DecisionsAI release, not once for
    every process. Failed or interrupted maintenance is deliberately retried.
    """
    path = home / ".decisions" / "harness-maintenance.json"
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return False
    if state.get("status") != "completed":
        return False
    recorded_version = state.get("app_version")
    if recorded_version:
        return recorded_version == _app_version()
    # Upgrade legacy state without forcing one more expensive pass after a
    # maintenance run that already completed against this source tree.
    try:
        return path.stat().st_mtime >= (PROJECT_ROOT / "VERSION").stat().st_mtime
    except OSError:
        return False


def _project_reference_skills(base_home: Path) -> list[str]:
    from distr.core.harness_bootstrap import (
        detected_harnesses,
        install_skills_to_harnesses,
    )

    detected = detected_harnesses()
    for harness_id, config_dir in (
        ("codex", ".codex"),
        ("claude", ".claude"),
        ("cursor", ".cursor"),
        ("pi", ".pi"),
        ("cline", ".cline"),
        ("gemini", ".gemini"),
    ):
        if (base_home / config_dir).exists():
            detected[harness_id] = True
    reference_ids = (
        "decisions-frontier-prep",
        "decisions-harness-audit",
        "decisions-harness-optimize",
        "codebase-design",
        "domain-modeling",
        "architecture-deepening-review",
    )
    reference_sources = {
        skill_id: PROJECT_ROOT / "skills" / skill_id
        for skill_id in reference_ids
        if (PROJECT_ROOT / "skills" / skill_id / "SKILL.md").is_file()
    }
    return install_skills_to_harnesses(
        home=base_home,
        detected=detected,
        skill_sources=reference_sources,
        also_commands=True,
        overwrite=True,
    )


def ensure_harness_stack_setup(
    *,
    home: Path | None = None,
    run_full: bool = False,
    install_codex_plugin: bool = True,
    install_editor_extension: bool = True,
    install_fallow_cli: bool = True,
    install_browser_use: bool = True,
    init_rtk_hooks: bool = True,
) -> dict[str, Any]:
    from distr.core.capabilities_pack import ensure_capabilities_pack_setup
    from distr.core.competition_pack import ensure_competition_pack_setup
    from distr.core.harness_pack import ensure_harness_pack_setup

    base_home = Path(home).expanduser() if home is not None else Path.home()
    results: dict[str, Any] = {}

    results["ecc"] = ensure_harness_pack_setup(
        home=base_home,
        run_full=run_full,
        install_codex_plugin=False,
        install_editor_extension=install_editor_extension and run_full,
    )
    results["competition"] = ensure_competition_pack_setup(
        home=base_home,
        run_full=run_full,
        install_codex_plugin=False,
        install_fallow_cli=install_fallow_cli,
    )

    try:
        if str(PROJECT_ROOT) not in sys.path:
            sys.path.insert(0, str(PROJECT_ROOT))
        from scripts.verify_agent_harness_setup import verify_agent_harness_setup

        results["plugins"] = verify_agent_harness_setup(PROJECT_ROOT, home=base_home, quiet=True)
    except Exception:
        results["plugins"] = {}

    # Project skills after plugin reinstall so Codex/Cursor trees are not wiped.
    results["ecc"] = ensure_harness_pack_setup(
        home=base_home,
        run_full=False,
        install_codex_plugin=install_codex_plugin,
        install_editor_extension=False,
    )
    results["competition"] = ensure_competition_pack_setup(
        home=base_home,
        run_full=False,
        install_codex_plugin=False,
        install_fallow_cli=False,
    )
    results["capabilities"] = ensure_capabilities_pack_setup(
        home=base_home,
        run_full=run_full,
        install_browser_use=install_browser_use,
    )

    try:
        from distr.core.design_reference_pack import ensure_design_reference_setup

        results["design_references"] = ensure_design_reference_setup(
            home=base_home,
            run_full=run_full,
        )
    except Exception:
        results["design_references"] = {"status": "skipped"}

    try:
        from distr.core.agent_reach_pack import ensure_agent_reach_pack_setup

        results["agent_reach"] = ensure_agent_reach_pack_setup(
            home=base_home,
            run_full=run_full,
            install_cli=run_full,
            run_doctor=run_full,
        )
    except Exception:
        results["agent_reach"] = {"status": "skipped"}

    try:
        from distr.core.community_skills_pack import ensure_community_skills_pack_setup

        results["community_skills"] = ensure_community_skills_pack_setup(
            home=base_home,
            run_full=run_full,
        )
    except Exception:
        results["community_skills"] = {"status": "skipped"}

    try:
        from distr.core.yt_dlp_pack import ensure_yt_dlp_pack_setup

        results["yt_dlp"] = ensure_yt_dlp_pack_setup(
            home=base_home,
            run_full=run_full,
            install_package=run_full,
        )
    except Exception:
        results["yt_dlp"] = {"status": "skipped"}

    try:
        from distr.core.composio_pack import ensure_composio_pack_setup

        results["composio"] = ensure_composio_pack_setup(
            home=base_home,
            run_full=run_full,
        )
    except Exception:
        results["composio"] = {"status": "skipped"}

    try:
        from distr.core.visual_plan_pack import ensure_visual_plan_pack_setup

        results["visual_plan"] = ensure_visual_plan_pack_setup(
            home=base_home,
            run_full=run_full,
        )
    except Exception:
        results["visual_plan"] = {"status": "skipped"}

    if init_rtk_hooks:
        try:
            from distr.core.rtk_hooks import init_rtk_agent_hooks

            init_rtk_agent_hooks(home=base_home, quiet=True)
            results["rtk_hooks"] = True
        except Exception:
            results["rtk_hooks"] = False

    try:
        from distr.core.mcp_harness import recalibrate_mcp_harness

        results["mcp"] = recalibrate_mcp_harness(home=base_home, run_full=run_full)
    except Exception:
        results["mcp"] = {"status": "skipped"}

    # Some CLI hook/plugin commands reconcile their own source trees late in
    # setup. Finish with a lightweight projection-only pass so setup cannot
    # report success while leaving Codex or another harness partially reset.
    results["ecc"] = ensure_harness_pack_setup(
        home=base_home,
        run_full=False,
        install_codex_plugin=False,
        install_editor_extension=False,
    )
    results["competition"] = ensure_competition_pack_setup(
        home=base_home,
        run_full=False,
        install_codex_plugin=False,
        install_fallow_cli=False,
    )
    results["capabilities"] = ensure_capabilities_pack_setup(
        home=base_home,
        run_full=False,
        install_browser_use=False,
    )
    try:
        results["community_skills"] = ensure_community_skills_pack_setup(
            home=base_home,
            run_full=False,
        )
        results["reference_skills"] = _project_reference_skills(base_home)
    except Exception:
        results["reference_skills"] = []

    return results


def ensure_harness_stack_setup_quiet() -> None:
    if (os.environ.get("DECISIONSAI_SKIP_HARNESS_STACK_SETUP") or "").strip() == "1":
        return
    try:
        ensure_harness_stack_setup(run_full=False, install_editor_extension=False)
    except Exception:
        pass


def _write_maintenance_state(home: Path, **state: Any) -> None:
    """Persist small, neutral startup diagnostics without blocking the caller."""
    try:
        path = home / ".decisions" / "harness-maintenance.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(f".tmp.{os.getpid()}")
        temporary.write_text(json.dumps(state, sort_keys=True), encoding="utf-8")
        temporary.replace(path)
    except OSError:
        pass


def schedule_harness_stack_setup(
    *,
    home: Path | None = None,
    delay_seconds: float = 5.0,
) -> threading.Thread | None:
    """Run harness repair after the UI startup path has been released.

    Setup used to run synchronously before ``distr.app.main.run`` and could
    leave the packaged app alive but unavailable for more than a minute on a
    clean home directory. A single daemon worker keeps repair automatic while
    making its state inspectable and preventing duplicate work per process.
    """
    if (os.environ.get("DECISIONSAI_SKIP_HARNESS_STACK_SETUP") or "").strip() == "1":
        return None
    base_home = Path(home).expanduser() if home is not None else Path.home()
    if _maintenance_is_current(base_home):
        return None
    global _maintenance_thread
    with _maintenance_lock:
        if _maintenance_thread is not None and _maintenance_thread.is_alive():
            return _maintenance_thread

        def maintain() -> None:
            if delay_seconds > 0:
                time.sleep(delay_seconds)
            started = time.monotonic()
            _write_maintenance_state(
                base_home,
                status="running",
                pid=os.getpid(),
                app_version=_app_version(),
                started_at=time.time(),
            )
            try:
                ensure_harness_stack_setup_quiet()
            except BaseException as exc:
                _write_maintenance_state(
                    base_home,
                    status="failed",
                    app_version=_app_version(),
                    elapsed_seconds=round(time.monotonic() - started, 3),
                    error_type=type(exc).__name__,
                    finished_at=time.time(),
                )
            else:
                _write_maintenance_state(
                    base_home,
                    status="completed",
                    app_version=_app_version(),
                    elapsed_seconds=round(time.monotonic() - started, 3),
                    finished_at=time.time(),
                )

        _maintenance_thread = threading.Thread(
            target=maintain,
            name="decisions-harness-maintenance",
            daemon=True,
        )
        _maintenance_thread.start()
        return _maintenance_thread
