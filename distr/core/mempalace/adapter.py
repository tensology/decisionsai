"""Thin MemPalace adapter — search / add drawer / availability.

Uses the real MemPalace Python API from the local reference clone:
- ``mempalace.searcher.search_memories`` for reads
- Chroma collection upsert (same shape as ``mcp_server.tool_add_drawer``) for writes

No fake store. If chromadb / mempalace are unavailable, methods return
soft failures so legacy paths remain authoritative.
"""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime
from typing import Any

from distr.core.mempalace.bootstrap import last_import_error, try_import_mempalace
from distr.core.mempalace.mapping import WING_DECISIONS
from distr.core.mempalace.paths import default_palace_path, ensure_palace_dir

logger = logging.getLogger(__name__)


class MemPalaceAdapter:
    """Programmatic facade over MemPalace drawers for Decisions wiring."""

    def __init__(self, palace_path: str | None = None) -> None:
        self.palace_path = str(palace_path or default_palace_path())
        self._available: bool | None = None
        self._error: str | None = None

    def available(self) -> bool:
        if self._available is not None:
            return self._available
        ok, err = try_import_mempalace()
        if not ok:
            self._available = False
            self._error = err
            return False
        # chromadb is a hard dep of mempalace backends — probe it explicitly
        try:
            import chromadb  # noqa: F401
        except Exception as exc:
            self._available = False
            self._error = f"chromadb missing: {exc}"
            return False
        self._available = True
        self._error = None
        return True

    def status(self) -> dict[str, Any]:
        ok = self.available()
        err = self._error or last_import_error()
        setup = None
        if not ok:
            setup = (
                "Install MemPalace locally: pip install -e ../reference/mempalace "
                "(free chromadb). Then run: python python -m distr.core.mempalace "
                "— or restart Decisions to auto-mine distr/seed-corpus-pack into "
                "~/.decisions/mempalace/palace (wing seed_corpus_pack)."
            )
        return {
            "available": ok,
            "palace_path": self.palace_path,
            "error": err,
            "setup_message": setup,
            "backend": "mempalace+chromadb" if ok else None,
        }

    def _get_collection(self, *, create: bool):
        from mempalace.palace import get_collection

        ensure_palace_dir(path=__import__("pathlib").Path(self.palace_path))
        return get_collection(self.palace_path, create=create)

    def search(
        self,
        query: str,
        *,
        wing: str | None = None,
        room: str | None = None,
        n_results: int = 5,
    ) -> dict[str, Any]:
        """Semantic search via MemPalace ``search_memories`` (dict return)."""
        if not self.available():
            return {"error": self._error or "mempalace unavailable", "results": []}
        q = (query or "").strip()
        if not q:
            return {"results": [], "query": q}
        try:
            from mempalace.searcher import search_memories

            return search_memories(
                q,
                self.palace_path,
                wing=wing,
                room=room,
                n_results=n_results,
            )
        except Exception as exc:
            logger.debug("mempalace search failed: %s", exc, exc_info=True)
            return {"error": str(exc), "results": []}

    def add_drawer(
        self,
        content: str,
        *,
        wing: str = WING_DECISIONS,
        room: str,
        source_file: str = "",
        added_by: str = "decisions",
        extra_meta: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """File verbatim content into wing/room (idempotent drawer id)."""
        if not self.available():
            return {"success": False, "error": self._error or "mempalace unavailable"}
        text = (content or "").strip()
        if not text:
            return {"success": False, "error": "empty content"}
        try:
            from mempalace.config import sanitize_content, sanitize_name

            wing = sanitize_name(wing, "wing")
            room = sanitize_name(room, "room")
            text = sanitize_content(text)
        except Exception as exc:
            return {"success": False, "error": str(exc)}

        drawer_id = (
            f"drawer_{wing}_{room}_"
            f"{hashlib.sha256((wing + room + text).encode()).hexdigest()[:24]}"
        )
        try:
            col = self._get_collection(create=True)
            try:
                existing = col.get(ids=[drawer_id])
                ids = getattr(existing, "ids", None) or (
                    existing.get("ids") if isinstance(existing, dict) else None
                )
                if ids:
                    return {"success": True, "reason": "already_exists", "drawer_id": drawer_id}
            except Exception:
                pass

            meta: dict[str, Any] = {
                "wing": wing,
                "room": room,
                "source_file": source_file or "",
                "chunk_index": 0,
                "added_by": added_by,
                "filed_at": datetime.now().isoformat(),
            }
            if extra_meta:
                for k, v in extra_meta.items():
                    if v is None:
                        continue
                    # Chroma metadata values must be scalar
                    if isinstance(v, (str, int, float, bool)):
                        meta[str(k)] = v
                    else:
                        meta[str(k)] = str(v)[:500]

            col.upsert(
                ids=[drawer_id],
                documents=[text],
                metadatas=[meta],
            )
            return {
                "success": True,
                "drawer_id": drawer_id,
                "wing": wing,
                "room": room,
            }
        except Exception as exc:
            logger.debug("mempalace add_drawer failed: %s", exc, exc_info=True)
            return {"success": False, "error": str(exc)}

    def format_search_as_context(
        self,
        query: str,
        *,
        wing: str | None = None,
        room: str | None = None,
        n_results: int = 5,
        max_chars: int = 2500,
    ) -> str:
        """Render search hits as a prompt context block (empty if none)."""
        raw = self.search(query, wing=wing, room=room, n_results=n_results)
        if raw.get("error") and not raw.get("results") and not raw.get("matches"):
            return ""
        hits = raw.get("results") or raw.get("matches") or raw.get("drawers") or []
        # search_memories historically returns {"results": [...]} or nested shapes
        if isinstance(hits, dict):
            hits = hits.get("results") or []
        if not hits and isinstance(raw, dict):
            # Some versions put list at top under "documents"
            docs = raw.get("documents")
            if isinstance(docs, list):
                hits = [{"content": d} for d in docs]

        chunks: list[str] = []
        for hit in hits[:n_results]:
            if isinstance(hit, dict):
                text = (
                    hit.get("content")
                    or hit.get("text")
                    or hit.get("document")
                    or ""
                )
                meta = hit.get("metadata") or {}
                wing_n = meta.get("wing") or hit.get("wing") or "?"
                room_n = meta.get("room") or hit.get("room") or "?"
                prefix = f"[{wing_n}/{room_n}] "
            else:
                text = str(hit)
                prefix = ""
            text = str(text).strip()
            if text:
                chunks.append(prefix + text)
        if not chunks:
            return ""
        body = "\n---\n".join(chunks)
        if len(body) > max_chars:
            body = body[: max_chars - 3].rstrip() + "..."
        return body


_ADAPTER: MemPalaceAdapter | None = None


def get_adapter() -> MemPalaceAdapter:
    global _ADAPTER
    if _ADAPTER is None:
        _ADAPTER = MemPalaceAdapter()
    return _ADAPTER
