"""Single entrypoint to bootstrap ECC, Ponytail/Fallow, browser/content, and RTK hooks."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


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

    return results


def ensure_harness_stack_setup_quiet() -> None:
    if (os.environ.get("DECISIONSAI_SKIP_HARNESS_STACK_SETUP") or "").strip() == "1":
        return
    try:
        ensure_harness_stack_setup(run_full=False, install_editor_extension=False)
    except Exception:
        pass
