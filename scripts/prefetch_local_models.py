#!/usr/bin/env python3
"""
Pre-download local STT/TTS assets into standard caches (same idea as Whisper’s first
``Model()`` pull, but run up-front so ``diagnose_stt.py``, pytest audio tests, and the
app do not stall mid-run.

Caches touched:
  - Vosk: ``distr/core/agent/models/vosk-model-en-us-0.22`` (via ``bin/setup_vosk.py``)
  - Whisper.cpp (pywhispercpp): default model weights in the library’s cache
  - Supertonic: ``~/.cache/supertonic3`` ONNX assets and preset voice styles

Opt out (CI / air-gapped): ``export DECISIONS_AI_SKIP_MODEL_PREFETCH=1``
"""

from __future__ import annotations

import argparse
import importlib.util
import os
import sys
from pathlib import Path


def _project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _load_setup_vosk():
    root = _project_root()
    path = root / "bin" / "setup_vosk.py"
    spec = importlib.util.spec_from_file_location("decisions_setup_vosk", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def prefetch_vosk() -> None:
    print("==> Vosk (local STT fallback)")
    os.chdir(_project_root())
    try:
        _load_setup_vosk().setup_vosk()
    except Exception as e:
        print(f"    ⚠ Vosk prefetch failed: {e}")


def prefetch_whisper() -> None:
    print("==> Whisper.cpp (pywhispercpp — warms weight cache like first app load)")
    root = _project_root()
    os.chdir(root)
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    try:
        from distr.core.agent.libs import WHISPER_AVAILABLE, pwc
    except Exception as e:
        print(f"    ⚠ Skip: could not import pywhispercpp stack ({e})")
        return
    if not WHISPER_AVAILABLE:
        print("    ⚠ Skip: pywhispercpp not installed")
        return
    try:
        from distr.core.agent.services.stt.whisper import suppress_stderr
    except Exception as e:
        print(f"    ⚠ Skip: whisper service import failed ({e})")
        return

    model_id = (os.environ.get("DECISIONSAI_PREFETCH_WHISPER_MODEL") or "base.en").strip() or "base.en"
    try:
        with suppress_stderr():
            m = pwc.Model(
                model_id,
                print_progress=True,
                redirect_whispercpp_logs_to=os.devnull,
            )
        del m
        print(f"    ✓ Whisper model {model_id!r} ready in cache")
    except Exception as e:
        print(f"    ⚠ Whisper warm failed: {e}")


def prefetch_supertonic() -> None:
    print("==> Supertonic (local ONNX TTS)")
    try:
        from supertonic import TTS
    except Exception as e:
        print(f"    ⚠ Skip: supertonic not installed ({e})")
        return
    try:
        tts = TTS(auto_download=True)
        tts.get_voice_style(voice_name="M1")
        print(f"    ✓ Supertonic assets ready in {getattr(tts, 'model_dir', 'default cache')}")
    except Exception as e:
        print(f"    ⚠ Supertonic prefetch failed: {e}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--only",
        default="all",
        help="Comma-separated subset: vosk,whisper,supertonic (default: all)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Run even if DECISIONS_AI_SKIP_MODEL_PREFETCH=1",
    )
    args = parser.parse_args(argv)

    skip_global = (os.environ.get("DECISIONS_AI_SKIP_MODEL_PREFETCH") or "").strip() == "1" and not args.force
    if skip_global:
        print("Skipping prefetch (DECISIONS_AI_SKIP_MODEL_PREFETCH=1). Pass --force to run anyway.")
        return 0
    only_raw = (args.only or "all").strip().lower()
    parts = {p.strip() for p in only_raw.split(",") if p.strip()}
    if "all" in parts or not parts:
        parts = {"vosk", "whisper", "supertonic"}

    root = _project_root()
    os.chdir(root)
    print(f"Prefetch local models (cwd={root})")
    print(f"  components: {', '.join(sorted(parts))}")
    print("")

    if "vosk" in parts:
        prefetch_vosk()
        print("")
    if "whisper" in parts:
        prefetch_whisper()
        print("")
    if "supertonic" in parts:
        prefetch_supertonic()
        print("")
    print("Prefetch pass finished.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
