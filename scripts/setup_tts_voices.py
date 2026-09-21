#!/usr/bin/env python3
"""Bind Fish Audio / ElevenLabs / Pixazo to voices that already exist on the API key.

Does not clone and does not insert custom_voices rows for library voices.
Usage (from DecisionsAI repo root):

  PYTHONPATH=. ~/.virtualenvs/decisions/bin/python scripts/setup_tts_voices.py
  PYTHONPATH=. ~/.virtualenvs/decisions/bin/python scripts/setup_tts_voices.py --dry-run
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from typing import Any, Callable

PROVIDERS = ("fishaudio", "elevenlabs", "pixazo")
PREFERRED = {
    "fishaudio": ("Sarah", "Energetic Male"),
    "elevenlabs": ("Sarah", "Roger"),
    "pixazo": ("VoxCPM",),
}
ENABLED_KEY = {
    "fishaudio": "fishaudio_enabled",
    "elevenlabs": "elevenlabs_enabled",
    "pixazo": "pixazo_enabled",
}
API_KEY = {
    "fishaudio": "fishaudio_key",
    "elevenlabs": "elevenlabs_key",
    "pixazo": "pixazo_key",
}
_JA_ZH = re.compile(r"\((?:ja|zh|zh-cn|zh-tw|ko)\)\s*$", re.I)


def _norm(name: str) -> str:
    return re.sub(r"\s+", " ", (name or "").strip().lower())


def _base_name(name: str) -> str:
    return _norm(re.sub(r"\s*\([^)]*\)\s*$", "", name or ""))


def _score(voice: dict, preferred: tuple[str, ...]) -> int:
    name = _norm(voice.get("name") or "")
    base = _base_name(voice.get("name") or "")
    for i, want in enumerate(preferred):
        w = _norm(want)
        if base == w or name.startswith(w) or w in base:
            return 100 - i
    return 0


def pick_library_voice(
    voices: list[dict],
    *,
    preferred: tuple[str, ...],
    current_id: str = "",
    replace_eastern: bool = False,
) -> dict | None:
    """Keep the current library id unless it is missing or an Eastern-only default."""
    by_id = {str(v.get("id") or ""): v for v in voices if v.get("id")}
    cur = by_id.get((current_id or "").strip())
    if cur and not (replace_eastern and _JA_ZH.search(cur.get("name") or "")):
        return cur
    ranked = sorted(
        voices,
        key=lambda v: (_score(v, preferred), not _JA_ZH.search(v.get("name") or "")),
        reverse=True,
    )
    for voice in ranked:
        if voice.get("id") and (_score(voice, preferred) or not _JA_ZH.search(voice.get("name") or "")):
            return voice
    return ranked[0] if ranked and ranked[0].get("id") else None


def fetch_provider_voices(provider_id: str) -> list[dict]:
    from distr.core.agent.services.tts.registry import tts_registry

    return list(tts_registry.get(provider_id).get_voices() or [])


def plan_library_setup(
    settings: dict,
    *,
    fetch_voices: Callable[[str], list[dict]] = fetch_provider_voices,
    activate: str = "fishaudio",
) -> dict[str, Any]:
    """Return bindings from live libraries. Never proposes clones."""
    bindings: dict[str, dict] = {}
    skipped: list[str] = []
    for pid in PROVIDERS:
        key = (settings.get(API_KEY[pid]) or "").strip()
        if not key:
            skipped.append(f"{pid}: no API key")
            continue
        voices = fetch_voices(pid)
        if not voices:
            skipped.append(f"{pid}: empty library")
            continue
        from distr.core.agent.services.tts.registry import tts_registry

        desc = tts_registry.get(pid)
        current = str(settings.get(desc.settings_key) or "")
        picked = pick_library_voice(
            voices,
            preferred=PREFERRED[pid],
            current_id=current,
            replace_eastern=(pid == "fishaudio"),
        )
        if not picked:
            skipped.append(f"{pid}: no usable library voice")
            continue
        bindings[pid] = {
            "id": picked["id"],
            "name": picked.get("name") or picked["id"],
            "count": len(voices),
            "enabled_key": ENABLED_KEY[pid],
            "settings_key": desc.settings_key,
        }
    return {
        "bindings": bindings,
        "skipped": skipped,
        "activate": activate if activate in bindings else "",
    }


def apply_library_setup(settings: dict, plan: dict[str, Any]) -> dict[str, Any]:
    from distr.core.agent.services.tts.registry import tts_registry

    for pid, row in plan["bindings"].items():
        settings[row["enabled_key"]] = True
        settings[row["settings_key"]] = row["id"]
    activate = plan.get("activate") or ""
    if activate and activate in plan["bindings"]:
        desc = tts_registry.get(activate)
        settings["voice_provider"] = activate
        settings["tts_provider"] = desc.name
        settings["tts_voice"] = plan["bindings"][activate]["id"]
        if activate == "fishaudio":
            from distr.core.agent.services.tts.fishaudio_config import DEFAULT_FISHAUDIO_TTS_MODEL

            settings["fishaudio_tts_model"] = (
                settings.get("fishaudio_tts_model") or DEFAULT_FISHAUDIO_TTS_MODEL
            )
    return settings


def run(*, dry_run: bool, activate: str) -> int:
    from distr.core.settings import load_settings_from_db, save_settings_to_db
    from distr.core.services.settings_service import notify_voice_hot_reload_for_running_agent

    settings = load_settings_from_db()
    plan = plan_library_setup(settings, activate=activate)
    for line in plan["skipped"]:
        print(line)
    for pid, row in plan["bindings"].items():
        print(f"{pid}: {row['count']} library voices, using {row['name']} ({row['id']})")
    if dry_run:
        print("dry-run: no save")
        return 0
    apply_library_setup(settings, plan)
    save_settings_to_db(settings)
    if plan["activate"]:
        notify_voice_hot_reload_for_running_agent(
            plan["activate"], plan["bindings"][plan["activate"]]["id"]
        )
    print("saved")
    return 0


def main() -> int:
    root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    if root not in sys.path:
        sys.path.insert(0, root)
    parser = argparse.ArgumentParser(description="Bind TTS providers to existing API library voices.")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--activate", default="fishaudio", choices=("",) + PROVIDERS)
    args = parser.parse_args()
    return run(dry_run=args.dry_run, activate=args.activate)


if __name__ == "__main__":
    raise SystemExit(main())
