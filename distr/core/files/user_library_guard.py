"""
Hard guardrails against wiping user data: protected library roots, bulk deletes, execute_code scans.

Bulk deletion (recursive folders, mass scripted deletes, rm -rf) is disabled in tooling by policy.
"""

from __future__ import annotations

import logging
import os
import platform
import re
from typing import Optional

logger = logging.getLogger(__name__)

# execute_code refusal when static scan hits bulk-delete patterns
EXECUTE_CODE_BULK_DELETE_REFUSAL = (
    "Blocked for safety: bulk deletion is disabled and cannot be overridden (any mode). "
    "Recursive folder deletes, rm -rf, rmtree, loops that remove many files, or multiple "
    "remove/unlink calls in one script are not allowed. Delete a single file at a time with "
    "file_operations (file path only), or use Finder."
)

REFUSAL_TOOL_DIRECTORY_DELETE = (
    "Directory deletion through DecisionsAI is disabled to prevent bulk data loss. "
    "Use delete only on a single file path, or remove folders yourself in Finder."
)


def _norm_home() -> str:
    return os.path.normcase(os.path.realpath(os.path.expanduser("~")))


def _compute_protected_library_roots() -> frozenset[str]:
    """Resolved, normcase paths for top-level ~/Downloads, ~/Desktop, etc."""
    roots: list[str] = []
    home_expanded = os.path.expanduser("~")
    for name in ("Downloads", "Desktop", "Documents", "Pictures", "Movies", "Music"):
        p = os.path.join(home_expanded, name)
        try:
            roots.append(os.path.normcase(os.path.realpath(p)))
        except OSError:
            roots.append(os.path.normcase(os.path.abspath(p)))
    if platform.system() == "Darwin":
        lib = os.path.join(home_expanded, "Library")
        try:
            roots.append(os.path.normcase(os.path.realpath(lib)))
        except OSError:
            roots.append(os.path.normcase(os.path.abspath(lib)))
    return frozenset(roots)


_PROTECTED_ROOTS_CACHE: Optional[frozenset[str]] = None


def protected_library_roots() -> frozenset[str]:
    global _PROTECTED_ROOTS_CACHE
    if _PROTECTED_ROOTS_CACHE is None:
        _PROTECTED_ROOTS_CACHE = _compute_protected_library_roots()
    return _PROTECTED_ROOTS_CACHE


def is_protected_library_root(path: str) -> bool:
    """True if *path* resolves to ~ itself or a standard library folder root."""
    if not path or not str(path).strip():
        return False
    try:
        resolved = os.path.normcase(os.path.realpath(path))
    except OSError:
        resolved = os.path.normcase(os.path.abspath(path))
    try:
        if resolved == _norm_home():
            return True
    except OSError:
        pass
    return resolved in protected_library_roots()


def refusal_protected_library_root(path: str, action: str) -> str:
    """User-visible refusal when *action* would target home or a standard library folder root."""
    return (
        f"Refusing to {action}: this path is your home folder or a protected "
        f"library folder (Downloads, Desktop, Documents, …): {path}. "
        "Operations on individual files inside these folders may still be allowed with confirmation, "
        "but not the folder root itself. Use Finder or run locally if you truly need that."
    )


def refusal_delete_library_root(path: str) -> str:
    return refusal_protected_library_root(path, "delete")


def scan_execute_code_forbidden_bulk_delete(code: str) -> Optional[str]:
    """
    Static deny-list for execute_code: never allow bulk / recursive deletion patterns.

    This is not a full sandbox; escape via exotic APIs may still exist — complement with tooling policy.
    """
    if not code or not isinstance(code, str):
        return None
    if len(code) > 400_000:
        return None

    lower = code.lower()

    # Recursive tree removal (any path / alias / commented code still risky — err strict)
    if "rmtree(" in lower:
        logger.warning("[USER_LIBRARY_GUARD] Blocked execute_code: rmtree(")
        return EXECUTE_CODE_BULK_DELETE_REFUSAL

    if "os.removedirs(" in lower or "os.removedirs (" in lower:
        logger.warning("[USER_LIBRARY_GUARD] Blocked execute_code: os.removedirs")
        return EXECUTE_CODE_BULK_DELETE_REFUSAL

    if "remove_tree(" in lower:
        logger.warning("[USER_LIBRARY_GUARD] Blocked execute_code: remove_tree")
        return EXECUTE_CODE_BULK_DELETE_REFUSAL

    # Shell-style recursive rm (spacing) and subprocess list form e.g. ["rm","-rf","/path"]
    _rm_rf_list = r'["\']rm["\']\s*,\s*["\']-\s*rf["\']'
    _rm_fr_list = r'["\']rm["\']\s*,\s*["\']-\s*fr["\']'
    _rm_r_list = r'["\']rm["\']\s*,\s*["\']-\s*r["\']'
    if (
        re.search(r"\brm\s+-\s*rf\b", lower)
        or re.search(r"\brm\s+-\s*fr\b", lower)
        or re.search(_rm_rf_list, lower)
        or re.search(_rm_fr_list, lower)
    ):
        logger.warning("[USER_LIBRARY_GUARD] Blocked execute_code: rm -rf")
        return EXECUTE_CODE_BULK_DELETE_REFUSAL
    if re.search(r"\brm\s+-\s*r\b", lower) or re.search(_rm_r_list, lower):
        logger.warning("[USER_LIBRARY_GUARD] Blocked execute_code: rm -r")
        return EXECUTE_CODE_BULK_DELETE_REFUSAL

    # find … -delete
    if re.search(r"\bfind\b.+-\s*delete\b", lower):
        logger.warning("[USER_LIBRARY_GUARD] Blocked execute_code: find -delete")
        return EXECUTE_CODE_BULK_DELETE_REFUSAL

    # Multiple explicit removes in one snippet (bulk delete without a loop)
    n_remove = len(re.findall(r"\bos\.remove\s*\(", lower))
    n_unlink = len(re.findall(r"\bos\.unlink\s*\(", lower))
    n_path_unlink = len(re.findall(r"\.unlink\s*\(", lower))
    if n_remove + n_unlink >= 2:
        logger.warning("[USER_LIBRARY_GUARD] Blocked execute_code: multiple os.remove/unlink")
        return EXECUTE_CODE_BULK_DELETE_REFUSAL
    if n_path_unlink >= 2:
        logger.warning("[USER_LIBRARY_GUARD] Blocked execute_code: multiple .unlink(")
        return EXECUTE_CODE_BULK_DELETE_REFUSAL

    if "map(" in lower and ("os.remove" in lower or "os.unlink" in lower):
        if any(x in lower for x in ("glob.glob", ".glob(", ".rglob(", "os.listdir", "os.walk")):
            logger.warning("[USER_LIBRARY_GUARD] Blocked execute_code: map()+delete over glob/list")
            return EXECUTE_CODE_BULK_DELETE_REFUSAL

    # Loops / comprehensions that walk directories and delete (classic Downloads wipe pattern)
    iteration_hint = any(
        x in lower
        for x in (
            "os.listdir",
            "os.walk",
            ".iterdir",
            "iterdir(",
            "os.scandir",
            "glob.glob",
            ".glob(",
            ".rglob(",
            "iglob(",
        )
    )
    delete_hint = any(
        x in lower for x in ("os.remove", "os.unlink", ".unlink(", "path.unlink")
    )
    if iteration_hint and delete_hint and re.search(r"\bfor\b", lower):
        logger.warning("[USER_LIBRARY_GUARD] Blocked execute_code: iteration + delete pattern")
        return EXECUTE_CODE_BULK_DELETE_REFUSAL

    return None


def scan_execute_code_for_library_wipe(code: str) -> Optional[str]:
    """Backward-compatible alias — policy is global bulk-delete denial."""
    return scan_execute_code_forbidden_bulk_delete(code)


def scan_execute_code_for_home_library_wipe(code: str) -> Optional[str]:
    """Backward-compatible alias."""
    return scan_execute_code_forbidden_bulk_delete(code)
