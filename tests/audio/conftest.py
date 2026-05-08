"""Audio tests: warm local STT/TTS caches once per pytest process (Whisper-style prefetch).

Skip with: ``DECISIONS_AI_SKIP_MODEL_PREFETCH=1 pytest ...``
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="session", autouse=True)
def _decisions_prefetch_local_models_for_audio_tests():
    skip = (os.environ.get("DECISIONS_AI_SKIP_MODEL_PREFETCH") or "").strip() == "1"
    script = _PROJECT_ROOT / "scripts" / "prefetch_local_models.py"
    if not skip and script.is_file():
        subprocess.run(
            [sys.executable, str(script), "--only", "all"],
            cwd=str(_PROJECT_ROOT),
            check=False,
        )
    yield
