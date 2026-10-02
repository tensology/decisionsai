"""Local palace paths for Decisions-owned MemPalace store + bundled seed pack."""

from __future__ import annotations

import os
from pathlib import Path


def default_palace_path() -> Path:
    """Decisions-local palace (not ~/.mempalace) — keeps rollback isolated."""
    override = (os.environ.get("DECISIONS_MEMPALACE_PALACE_PATH") or "").strip()
    if override:
        return Path(os.path.expanduser(override)).resolve()
    return (Path.home() / ".decisions" / "mempalace" / "palace").resolve()


def ensure_palace_dir(path: Path | None = None) -> Path:
    p = path or default_palace_path()
    p.mkdir(parents=True, exist_ok=True)
    return p


def decisions_ai_root() -> Path:
    """DecisionsAI repo / app root (distr/core/mempalace/paths.py → parents[3])."""
    return Path(__file__).resolve().parents[3]


def bundled_seed_corpus_pack() -> Path | None:
    """Resolve vendored seed pack (``distr/seed-corpus-pack``) if present.

    Order:
    1. ``DECISIONS_SEED_CORPUS_PACK`` env override
    2. In-tree ``<DecisionsAI>/distr/seed-corpus-pack`` (ships with app via build_app rsync)
    3. Sibling ``../seed-corpus-pack`` (dev checkout next to DecisionsAI)
    """
    override = (os.environ.get("DECISIONS_SEED_CORPUS_PACK") or "").strip()
    if override:
        p = Path(os.path.expanduser(override)).resolve()
        return p if (p / "mempalace.yaml").is_file() or (p / "manifest.json").is_file() else None

    candidates = [
        decisions_ai_root() / "distr" / "seed-corpus-pack",
        decisions_ai_root().parent / "seed-corpus-pack",
    ]
    for cand in candidates:
        try:
            resolved = cand.resolve()
        except Exception:
            continue
        if (resolved / "mempalace.yaml").is_file() or (resolved / "manifest.json").is_file():
            return resolved
    return None


def seed_mine_marker_path() -> Path:
    """Marker file recording successful first-run mine of the seed wing."""
    return (Path.home() / ".decisions" / "mempalace" / ".seed_corpus_pack_mined.json").resolve()
