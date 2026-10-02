"""Bootstrap local MemPalace package without network install.

Uses the Decisions reference clone at
``../reference/mempalace`` (sibling of DecisionsAI) by putting it on
``sys.path``. Does not pip-install from the internet.

Optional: ``pip install -e ../reference/mempalace`` once chromadb is
acceptable locally (free OSS; still a network fetch of deps).
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

_BOOTSTRAPPED = False
_IMPORT_ERROR: str | None = None


def reference_mempalace_root() -> Path:
    """Absolute path to the local MemPalace clone (editable source tree)."""
    # distr/core/mempalace/bootstrap.py -> DecisionsAI repo root
    decisions_ai = Path(__file__).resolve().parents[3]
    return (decisions_ai.parent / "reference" / "mempalace").resolve()


def ensure_mempalace_on_path() -> Path | None:
    """Insert reference clone on sys.path if present. Returns root or None."""
    global _BOOTSTRAPPED
    root = reference_mempalace_root()
    pkg = root / "mempalace" / "__init__.py"
    if not pkg.is_file():
        logger.debug("MemPalace reference clone missing at %s", root)
        return None
    root_s = str(root)
    if root_s not in sys.path:
        sys.path.insert(0, root_s)
    _BOOTSTRAPPED = True
    return root


def try_import_mempalace() -> tuple[bool, str | None]:
    """Attempt import after path bootstrap. Returns (ok, error_message)."""
    global _IMPORT_ERROR
    ensure_mempalace_on_path()
    try:
        import mempalace  # noqa: F401

        _IMPORT_ERROR = None
        return True, None
    except Exception as exc:  # ImportError or chromadb missing transitively
        _IMPORT_ERROR = f"{type(exc).__name__}: {exc}"
        logger.debug("mempalace import failed: %s", _IMPORT_ERROR)
        return False, _IMPORT_ERROR


def last_import_error() -> str | None:
    return _IMPORT_ERROR
