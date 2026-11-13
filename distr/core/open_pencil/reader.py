"""Read-side bridge: inspect a local OpenPencil ``.fig`` / ``.pen`` scene graph.

Uses only the pinned ``@open-pencil/cli`` in file mode (never the running app):

* ``openpencil --version``                  pin check
* ``openpencil info <file> --json``         page / node / type counts
* ``openpencil eval <file> --stdin --json`` read-only script ``inspect_scene.js``
  (no ``--write`` / ``--output``, so nothing is saved; the file hash is
  checked before and after and the read fails closed if it changed)

The output is the scene-graph structure itself (pages, top-level frames,
components with variant/slot properties, slots, instances, variable bindings,
layer tree). It is deliberately not translated to Wireframe DSL.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from distr.core.open_pencil.pin import (
    PIN_CLI_VERSION,
    PIN_SHA,
    PIN_TAG,
    OpenPencilBridgeError,
    OpenPencilInvalidDocument,
    cli_environment,
    require_pin,
)

SUPPORTED_SUFFIXES = (".fig", ".pen")
MAX_FILE_BYTES = 200 * 1024 * 1024
DEFAULT_MAX_DEPTH = 8
DEFAULT_MAX_NODES = 5000
SCRIPT_PATH = Path(__file__).with_name("inspect_scene.js")


def validate_document_path(raw: str | Path) -> Path:
    """Accept only an existing local .fig/.pen file. URLs are refused."""
    text = str(raw or "").strip()
    if not text:
        raise OpenPencilInvalidDocument("No OpenPencil file path given.")
    if "://" in text or text.startswith(("http:", "https:", "file:", "data:")):
        raise OpenPencilInvalidDocument("Only local file paths are accepted; remote URLs are not fetched.")
    path = Path(text).expanduser()
    if not path.is_absolute():
        raise OpenPencilInvalidDocument("OpenPencil file path must be absolute.")
    path = path.resolve()
    if path.suffix.lower() not in SUPPORTED_SUFFIXES:
        raise OpenPencilInvalidDocument(f"Unsupported file type {path.suffix!r}; expected .fig or .pen.")
    if not path.is_file():
        raise OpenPencilInvalidDocument(f"OpenPencil file not found: {path}")
    size = path.stat().st_size
    if size == 0 or size > MAX_FILE_BYTES:
        raise OpenPencilInvalidDocument(f"OpenPencil file size {size} bytes is outside the accepted range.")
    with path.open("rb") as handle:
        head = handle.read(64)
    if head.startswith(b"version https://git-lfs"):
        raise OpenPencilInvalidDocument(f"{path} is a Git LFS pointer, not a design file.")
    return path


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _run_cli(cli: str, args: list[str], *, stdin: str | None, timeout: float) -> object:
    try:
        proc = subprocess.run(
            [cli, *args], input=stdin, capture_output=True, text=True, timeout=timeout,
            env=cli_environment(), stdin=None if stdin is not None else subprocess.DEVNULL,
        )
    except subprocess.TimeoutExpired as exc:
        raise OpenPencilBridgeError(f"OpenPencil CLI timed out after {timeout:.0f}s: {args[0]}") from exc
    except OSError as exc:
        raise OpenPencilBridgeError(f"OpenPencil CLI could not start: {exc}") from exc
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip().splitlines()[-3:]
        raise OpenPencilInvalidDocument(f"OpenPencil CLI `{args[0]}` failed: {' | '.join(detail) or proc.returncode}")
    try:
        return json.loads(proc.stdout)
    except ValueError as exc:
        raise OpenPencilInvalidDocument(f"OpenPencil CLI `{args[0]}` did not return JSON.") from exc


def _script(max_depth: int, max_nodes: int) -> str:
    limits = json.dumps({"maxDepth": int(max_depth), "maxNodes": int(max_nodes)})
    return SCRIPT_PATH.read_text(encoding="utf-8").replace("const LIMITS = __DECISIONS_LIMITS__", f"const LIMITS = {limits}", 1)


def inspect_document(
    path: str | Path,
    *,
    max_depth: int = DEFAULT_MAX_DEPTH,
    max_nodes: int = DEFAULT_MAX_NODES,
    timeout: float = 120.0,
    require_reference: bool = True,
) -> dict:
    """Return a structured, read-only inspection of one OpenPencil document."""
    status = require_pin(require_reference=require_reference)
    doc = validate_document_path(path)
    before = _sha256(doc)
    info = _run_cli(status.cli_path, ["info", str(doc), "--json"], stdin=None, timeout=timeout)
    scene = _run_cli(status.cli_path, ["eval", str(doc), "--stdin", "--json"], stdin=_script(max_depth, max_nodes), timeout=timeout)
    after = _sha256(doc)
    if before != after:
        raise OpenPencilBridgeError(f"{doc} changed while it was being read; refusing a partial inspection.")
    if not isinstance(info, dict) or not isinstance(scene, dict) or not isinstance(scene.get("pages"), list):
        raise OpenPencilInvalidDocument("OpenPencil CLI returned an unexpected shape.")

    components = scene.get("components") or []
    slots = scene.get("slots") or []
    return {
        "source": {
            "path": str(doc),
            "format": doc.suffix.lower().lstrip("."),
            "sha256": before,
            "bytes": doc.stat().st_size,
            "inspected_at": datetime.now(timezone.utc).isoformat(),
        },
        "reader": {
            "tool": "@open-pencil/cli",
            "cli_version": PIN_CLI_VERSION,
            "pin_tag": PIN_TAG,
            "pin_sha": PIN_SHA,
            "commands": ["openpencil --version", "openpencil info <file> --json", "openpencil eval <file> --stdin --json"],
            "mode": "file (headless, read-only)",
        },
        "summary": {
            "pages": len(scene["pages"]),
            "total_nodes": info.get("totalNodes"),
            "node_types": info.get("types") or {},
            "fonts": info.get("fonts") or [],
            "top_level_frames": len(scene.get("frames") or []),
            "components": sum(1 for c in components if c.get("type") == "COMPONENT"),
            "component_sets": sum(1 for c in components if c.get("type") == "COMPONENT_SET"),
            "slots": len(slots),
            "instances": len(scene.get("instances") or []),
            "bound_layers": len(scene.get("bindings") or []),
            "variables": (scene.get("variables") or {}).get("count") if scene.get("variables") is not None else None,
            "layer_tree_truncated": bool(scene.get("truncated")),
        },
        "pages": scene["pages"],
        "frames": scene.get("frames") or [],
        "components": components,
        "slots": slots,
        "instances": scene.get("instances") or [],
        "bindings": scene.get("bindings") or [],
        "text_in_components": scene.get("textInComponents") or [],
        "variables": scene.get("variables"),
        "limits": {"max_depth": max_depth, "max_nodes": max_nodes},
    }
