"""Thin MemPalace adapter — search / add drawer / availability.

Hub-first (MemPalace 3.7+ shared brain): when a ``mempalace serve`` hub owns
the Decisions palace, every read and write goes over HTTP MCP via
``distr.core.mempalace.hub`` and this process never opens Chroma.

- Hub up: ``mempalace_search`` / ``mempalace_add_drawer`` on the hub.
- Hub configured but down: writes are journaled (``hub-journal.jsonl``) and
  replayed later; reads fall back to MemPalace's read-only search
  (``DECISIONS_MEMPALACE_DIRECT_READ_FALLBACK=0`` disables that).
- No hub ever configured on this machine: legacy in-process path
  (``mempalace.searcher.search_memories`` + Chroma upsert).

No fake store. If chromadb / mempalace are unavailable, methods return
soft failures so legacy paths remain authoritative.
"""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime
from typing import Any

import os

from distr.core.mempalace import hub as _hub
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

    def _hub(self) -> dict | None:
        try:
            return _hub.discover(self.palace_path)
        except Exception:
            logger.debug("mempalace hub discovery failed", exc_info=True)
            return None

    def _hub_configured(self) -> bool:
        try:
            return _hub.hub_configured(self.palace_path)
        except Exception:
            return False

    @staticmethod
    def _direct_read_fallback() -> bool:
        raw = (os.environ.get("DECISIONS_MEMPALACE_DIRECT_READ_FALLBACK") or "1").strip().lower()
        return raw not in {"0", "false", "no", "off"}

    def available(self) -> bool:
        if self._hub() is not None:
            return True
        if self._hub_configured():
            # Writes queue to the journal; reads may use the read-only fallback.
            return True
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

    def _import_ok(self) -> bool:
        ok, err = try_import_mempalace()
        if not ok:
            self._error = err
            return False
        try:
            import chromadb  # noqa: F401
        except Exception as exc:
            self._error = f"chromadb missing: {exc}"
            return False
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
        hub = self._hub()
        configured = self._hub_configured()
        if hub is not None:
            backend = "mempalace-hub"
        elif configured:
            backend = "mempalace-hub-down(journal)"
        else:
            backend = "mempalace+chromadb" if ok else None
        return {
            "available": ok,
            "palace_path": self.palace_path,
            "error": None if hub is not None else err,
            "setup_message": None if (hub is not None or configured) else setup,
            "backend": backend,
            "hub": {
                "configured": configured,
                "up": hub is not None,
                "url": hub["base_url"] if hub else None,
                "agent_id": _hub.AGENT_ID,
                "journal_pending": _hub.journal_pending(self.palace_path),
            },
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
        """Semantic search: hub ``mempalace_search``, else ``search_memories``."""
        q = (query or "").strip()
        if not q:
            return {"results": [], "query": q}
        hub = self._hub()
        if hub is not None:
            args: dict[str, Any] = {"query": q, "limit": int(n_results)}
            if wing:
                args["wing"] = wing
            if room:
                args["room"] = room
            try:
                res = _hub.call_tool(self.palace_path, "mempalace_search", args, hub=hub)
                res.setdefault("results", [])
                res["via"] = "hub"
                return res
            except Exception as exc:
                logger.debug("mempalace hub search failed: %s", exc)
                hub = None
        if self._hub_configured() and not self._direct_read_fallback():
            return {"error": "mempalace hub down", "results": []}
        if not self._import_ok():
            return {"error": self._error or "mempalace unavailable", "results": []}
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
        text = (content or "").strip()
        if not text:
            return {"success": False, "error": "empty content"}
        hub = self._hub()
        if hub is not None or self._hub_configured():
            # MCP add_drawer has no free-form metadata; keep the provenance in
            # source_file so it stays searchable/filterable.
            src = source_file or ""
            if not src and extra_meta:
                parts = [f"{k}={v}" for k, v in extra_meta.items() if v not in (None, "")]
                src = "decisions://" + "&".join(parts)[:400] if parts else ""
            args = {
                "wing": wing,
                "room": room,
                "content": text,
                "source_file": src,
                "added_by": added_by if added_by and ":" in added_by else _hub.AGENT_ID,
            }
            if hub is not None:
                try:
                    _hub.journal_replay(self.palace_path, hub=hub)
                    res = _hub.call_tool(self.palace_path, "mempalace_add_drawer", args, hub=hub)
                    res.setdefault("success", True)
                    res["via"] = "hub"
                    return res
                except Exception as exc:
                    logger.debug("mempalace hub add_drawer failed, journaling: %s", exc)
            try:
                path = _hub.journal_append(self.palace_path, "mempalace_add_drawer", args)
                return {"success": True, "queued": True, "journal": str(path), "via": "journal"}
            except Exception as exc:
                return {"success": False, "error": f"journal failed: {exc}"}
        if not self._import_ok():
            return {"success": False, "error": self._error or "mempalace unavailable"}
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
