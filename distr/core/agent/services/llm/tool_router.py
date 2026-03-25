"""
Semantic Tool Router — vector-based tool selection for LLM assistance.

Instead of regex patterns or keyword lists, this embeds tool descriptions
and user queries into the same vector space, then selects the top-k most
relevant tools by cosine similarity.

The LLM still makes the final tool call — this just narrows the candidate
set from 70+ tools to the most relevant ones, improving both accuracy and
inference speed.

Architecture:
  1. At startup, embed each tool's name + description (zero curation needed)
  2. At query time, embed the user text, cosine-rank against tool vectors
  3. Return tools above a similarity threshold (adaptive, not fixed top-k)

Uses Ollama's /api/embeddings endpoint directly (no LlamaIndex dependency).
Falls back gracefully if embeddings are unavailable.

Zero maintenance: new tools get routed correctly just from their description.
"""

import hashlib
import json
import logging
import math
import time
import threading
from collections import OrderedDict
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import requests

logger = logging.getLogger(__name__)

# Cache file so we don't re-embed on every restart
_CACHE_DIR = Path(__file__).parent.parent.parent.parent / "db"
_CACHE_FILE = _CACHE_DIR / "tool_embeddings_cache.json"

# Singleton
_router_instance: Optional["ToolRouter"] = None
_router_lock = threading.Lock()


def get_tool_router() -> "ToolRouter":
    """Get or create the singleton ToolRouter instance."""
    global _router_instance
    if _router_instance is None:
        with _router_lock:
            if _router_instance is None:
                _router_instance = ToolRouter()
    return _router_instance


class ToolRouter:
    """Semantic tool selection via embedding similarity.

    Design principles:
      - Zero curation: tool name + description is the only input.
        No hand-maintained keyword lists, voice tags, or regex patterns.
      - Threshold-based: returns all tools above a similarity floor,
        not a fixed top-k. A clear command like "take a screenshot"
        might match 2 tools; an ambiguous one might match 8.
      - Always-safe: if embeddings fail, returns all tools (LLM decides).
    """

    # Similarity floor: tools scoring below this are excluded.
    # Tuned empirically: 0.45 keeps relevant tools while cutting noise.
    # The top scorer is always included regardless of threshold.
    SIMILARITY_FLOOR = 0.45

    # Minimum tools to return (even if below threshold)
    MIN_TOOLS = 6

    # Maximum tools to return (cap for very ambiguous queries)
    MAX_TOOLS = 15

    def __init__(
        self,
        embedding_model: str = "nomic-embed-text",
        ollama_base_url: str = "http://localhost:11434",
    ):
        self.embedding_model = embedding_model
        self.ollama_base_url = ollama_base_url.rstrip("/")

        # tool_name -> embedding vector
        self._tool_embeddings: Dict[str, List[float]] = {}
        # tool_name -> text that was embedded (for cache invalidation)
        self._tool_texts: Dict[str, str] = {}
        # Whether the index is ready
        self._ready = False
        self._building = False

        # LRU cache for query embeddings — avoids re-embedding similar/repeated
        # user messages.  Keyed by text hash, stores embedding vectors.
        self._query_cache: OrderedDict[str, List[float]] = OrderedDict()
        self._query_cache_max = 128

    # ----------------------------------------------------------
    # Public API
    # ----------------------------------------------------------

    def build_index(self, tools: List) -> None:
        """Build the tool embedding index from tool instances.

        Each tool gets a single embedding from its name + description.
        No curated tags — the description IS the semantic signature.
        Called once at agent startup; cached across restarts.
        """
        if self._building:
            return
        self._building = True

        try:
            text_map = self._build_text_map(tools)
            cached = self._load_cache()

            to_embed: Dict[str, str] = {}
            for name, text in text_map.items():
                if name in cached and cached[name].get("text") == text:
                    self._tool_embeddings[name] = cached[name]["vector"]
                else:
                    to_embed[name] = text

            self._tool_texts = text_map

            if to_embed:
                t0 = time.time()
                vectors = self._batch_embed(list(to_embed.values()))
                if vectors and len(vectors) == len(to_embed):
                    for (name, _), vec in zip(to_embed.items(), vectors):
                        self._tool_embeddings[name] = vec
                    logger.info(
                        "ToolRouter: embedded %d tools in %.2fs (%d from cache)",
                        len(to_embed), time.time() - t0,
                        len(text_map) - len(to_embed),
                    )
                    self._save_cache()
                else:
                    logger.warning(
                        "ToolRouter: embedding failed, will fall back to all tools"
                    )
            else:
                logger.info(
                    "ToolRouter: all %d tool embeddings loaded from cache",
                    len(text_map),
                )

            self._ready = bool(self._tool_embeddings)
        except Exception as e:
            logger.error("ToolRouter: build_index failed: %s", e, exc_info=True)
            self._ready = False
        finally:
            self._building = False

    def route(self, user_text: str, all_tools: List) -> List:
        """Return the most relevant tools for the user's text.

        Uses adaptive thresholding:
          - All tools above SIMILARITY_FLOOR are included
          - At least MIN_TOOLS are always returned
          - At most MAX_TOOLS are returned
          - Falls back to all tools if router isn't ready
        """
        if not self._ready or not self._tool_embeddings:
            return all_tools

        query_vec = self._embed_single(user_text)
        if query_vec is None:
            return all_tools

        # Score all tools
        scores: List[Tuple[str, float]] = []
        for name, tool_vec in self._tool_embeddings.items():
            sim = self._cosine_similarity(query_vec, tool_vec)
            scores.append((name, sim))

        scores.sort(key=lambda x: x[1], reverse=True)

        # Adaptive selection: above threshold, with min/max bounds
        selected_names = set()
        for name, sim in scores:
            if len(selected_names) >= self.MAX_TOOLS:
                break
            if sim >= self.SIMILARITY_FLOOR or len(selected_names) < self.MIN_TOOLS:
                selected_names.add(name)

        # Filter tools list preserving original order
        tool_by_name = {t.name: t for t in all_tools}
        selected = [tool_by_name[n] for n in tool_by_name if n in selected_names]

        if logger.isEnabledFor(logging.DEBUG):
            top5 = scores[:5]
            logger.debug(
                "ToolRouter: '%s' -> top5: %s | selected %d/%d",
                user_text[:60],
                [(n, f"{s:.3f}") for n, s in top5],
                len(selected), len(all_tools),
            )

        return selected

    def get_scores(self, user_text: str) -> List[Tuple[str, float]]:
        """Return all (tool_name, similarity_score) pairs sorted descending.
        Useful for debugging and telemetry."""
        if not self._ready:
            return []
        query_vec = self._embed_single(user_text)
        if query_vec is None:
            return []
        scores = []
        for name, tool_vec in self._tool_embeddings.items():
            sim = self._cosine_similarity(query_vec, tool_vec)
            scores.append((name, sim))
        scores.sort(key=lambda x: x[1], reverse=True)
        return scores

    @property
    def is_ready(self) -> bool:
        return self._ready

    # ----------------------------------------------------------
    # Text map: tool name + description only. Zero curation.
    # ----------------------------------------------------------

    @staticmethod
    def _build_text_map(tools: List) -> Dict[str, str]:
        """Build embedding text for each tool from name + description.

        No curated voice tags. The tool description is the semantic
        signature — if it's good enough for the LLM to pick the tool,
        it's good enough for the embedding model to rank it.
        """
        text_map: Dict[str, str] = {}
        for tool in tools:
            name = tool.name
            desc = getattr(tool, "description", "") or ""
            # tool name as words + first 500 chars of description
            name_words = name.replace("_", " ")
            text_map[name] = f"{name_words}: {desc[:500]}".strip()
        return text_map

    # ----------------------------------------------------------
    # Embedding helpers (Ollama /api/embeddings)
    # ----------------------------------------------------------

    def _embed_single(self, text: str) -> Optional[List[float]]:
        """Embed a single text string with LRU caching. Returns None on failure."""
        cache_key = hashlib.md5(text.encode()).hexdigest()

        # Check LRU cache first
        if cache_key in self._query_cache:
            self._query_cache.move_to_end(cache_key)
            return self._query_cache[cache_key]

        try:
            resp = requests.post(
                f"{self.ollama_base_url}/api/embeddings",
                json={"model": self.embedding_model, "prompt": text},
                timeout=10,
            )
            resp.raise_for_status()
            vec = resp.json().get("embedding")
            if vec:
                self._query_cache[cache_key] = vec
                if len(self._query_cache) > self._query_cache_max:
                    self._query_cache.popitem(last=False)
            return vec
        except Exception as e:
            logger.debug("ToolRouter: embed failed: %s", e)
            return None

    def _batch_embed(self, texts: List[str]) -> Optional[List[List[float]]]:
        """Embed multiple texts sequentially."""
        vectors = []
        for text in texts:
            vec = self._embed_single(text)
            if vec is None:
                return None
            vectors.append(vec)
        return vectors

    # ----------------------------------------------------------
    # Cosine similarity (pure Python, no numpy needed)
    # ----------------------------------------------------------

    @staticmethod
    def _cosine_similarity(a: List[float], b: List[float]) -> float:
        dot = sum(x * y for x, y in zip(a, b))
        norm_a = math.sqrt(sum(x * x for x in a))
        norm_b = math.sqrt(sum(x * x for x in b))
        if norm_a == 0 or norm_b == 0:
            return 0.0
        return dot / (norm_a * norm_b)

    # ----------------------------------------------------------
    # Cache (avoid re-embedding on every restart)
    # ----------------------------------------------------------

    def _load_cache(self) -> Dict[str, dict]:
        """Load cached embeddings. Returns {tool_name: {text, vector}}."""
        try:
            if _CACHE_FILE.exists():
                with open(_CACHE_FILE, "r") as f:
                    data = json.load(f)
                if data.get("model") == self.embedding_model:
                    return data.get("tools", {})
        except Exception as e:
            logger.debug("ToolRouter: cache load failed: %s", e)
        return {}

    def _save_cache(self) -> None:
        """Persist tool embeddings to disk."""
        try:
            _CACHE_DIR.mkdir(parents=True, exist_ok=True)
            data = {
                "model": self.embedding_model,
                "tools": {
                    name: {
                        "text": self._tool_texts.get(name, ""),
                        "vector": vec,
                    }
                    for name, vec in self._tool_embeddings.items()
                },
            }
            with open(_CACHE_FILE, "w") as f:
                json.dump(data, f)
        except Exception as e:
            logger.debug("ToolRouter: cache save failed: %s", e)
