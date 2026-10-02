"""MemPalace memory backend for DecisionsAI (feature-flagged; default ON).

When ``mempalace_memory_backend`` is ON (default): dual-write + prefer-read,
and first-run mines the bundled ``distr/seed-corpus-pack`` into wing
``seed_corpus_pack`` under ``~/.decisions/mempalace/palace``.

Soft-fails if chromadb / mempalace are missing — legacy stores stay authoritative.

Install (local, no PyPI auth/pay)::

    # Prefer editable path from the Decisions reference clone:
    pip install -e ../reference/mempalace
    # (pulls free chromadb dep — network; do not run under no-spend if blocked)

Or rely on ``bootstrap.ensure_mempalace_on_path()`` which adds the reference
clone to ``sys.path`` without installing (still needs chromadb importable).
"""

from distr.core.mempalace.adapter import MemPalaceAdapter, get_adapter
from distr.core.mempalace.flags import (
    DEFAULT_ENABLED,
    FLAG_KEY,
    is_mempalace_memory_backend_enabled,
)
from distr.core.mempalace.wiring import backend_status

__all__ = [
    "DEFAULT_ENABLED",
    "FLAG_KEY",
    "MemPalaceAdapter",
    "backend_status",
    "get_adapter",
    "is_mempalace_memory_backend_enabled",
]
