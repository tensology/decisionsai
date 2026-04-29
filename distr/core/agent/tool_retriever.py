"""
Semantic Tool Retriever — tool selection via embedding similarity.

Primary backend: ``sentence-transformers`` (all-MiniLM-L6-v2, 384-dim).
Fallback backend: ``sklearn`` TF-IDF (no torch dependency).

Three-layer selection:
  1. Semantic pre-retrieval — top-K tools by cosine similarity
  2. Always-on core set — 6 high-utility tools always included
  3. RequestToolTool fallback — meta-tool for on-demand injection

A model-tier gate short-circuits retrieval for micro models (≤1.5B params),
giving them only the 6 always-on tools.

Kill switch: set DECISIONS_TOOL_RETRIEVAL_ENABLED=false to bypass retrieval
and return all tools (checked at retrieval time, no restart needed).
"""

import logging
import os

from distr.core.agent.tool_telemetry import log_retrieval_summary
import re
import threading
import time
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

ALWAYS_ON_NAMES: set[str] = {
    "smart_open",
    "execute_code",
    "oracle_control",
    "mode_control",
    "new_chat",
    "system_info",
}

_MICRO_ALLOWLIST: list[str] = ["smollm", "tinyllama", "phi-1", "phi-1.5"]

# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_retriever_instance: Optional["ToolRetriever"] = None
_retriever_lock = threading.Lock()
_build_started = threading.Event()  # guards against duplicate build_index_async calls


def get_tool_retriever() -> "ToolRetriever":
    """Return the module-level singleton ToolRetriever."""
    global _retriever_instance
    if _retriever_instance is None:
        with _retriever_lock:
            if _retriever_instance is None:
                _retriever_instance = ToolRetriever()
    return _retriever_instance


def build_index_async(tools: list) -> None:
    """Trigger background index build in a daemon thread.

    Idempotent — if a build is already in progress or complete, this is a no-op.
    """
    if _build_started.is_set():
        return
    _build_started.set()

    def _build() -> None:
        retriever = get_tool_retriever()
        try:
            retriever.build_index(tools)
        except OSError:
            logger.info(
                "Downloading tool embedding model (~80MB), semantic tool "
                "retrieval will activate once complete."
            )
            try:
                retriever.build_index(tools)
            except Exception:
                logger.warning(
                    "Failed to download/build tool embedding index; "
                    "semantic tool retrieval falling back to all tools."
                )
        except Exception as e:
            logger.warning("build_index_async failed: %s", e)

    thread = threading.Thread(target=_build, daemon=True)
    thread.start()


# ---------------------------------------------------------------------------
# K resolution
# ---------------------------------------------------------------------------

_DEFAULT_K = 15
_SMALL_MODEL_K = 4  # small models get a tight tool set — fewer choices = less hallucination


def _resolve_k() -> int:
    """Resolve the retrieval budget *K*.

    Priority: env var → settings DB → default 10.
    """
    env_val = os.environ.get("DECISIONS_TOOL_RETRIEVAL_K")
    if env_val is not None and env_val.isdigit():
        return int(env_val)
    try:
        from distr.core.settings import load_settings_from_db
        settings = load_settings_from_db()
        db_val = settings.get("tool_retrieval_k")
        if db_val is not None:
            return int(db_val)
    except Exception:
        pass
    return _DEFAULT_K


# ---------------------------------------------------------------------------
# ToolRetriever
# ---------------------------------------------------------------------------

class ToolRetriever:
    """In-memory embedding index for semantic tool selection.

    Tries sentence-transformers first (384-dim dense vectors).
    Falls back to sklearn TF-IDF (sparse vectors) if torch is broken.
    """

    def __init__(self) -> None:
        self._matrix: np.ndarray | None = None
        self._names: list[str] = []
        self._model = None          # SentenceTransformer or None
        self._vectorizer = None     # TfidfVectorizer (fallback)
        self._tfidf_matrix = None   # sparse matrix (fallback)
        self._backend: str = ""     # "sbert" or "tfidf"
        self._index_ready: threading.Event = threading.Event()
        self._build_lock: threading.Lock = threading.Lock()

    def is_ready(self) -> bool:
        return self._index_ready.is_set()

    @staticmethod
    def classify_model_tier(model_name: str) -> str:
        """Return ``"micro"``, ``"small"``, or ``"standard"``. Never raises."""
        try:
            lower = model_name.lower()
            for entry in _MICRO_ALLOWLIST:
                if entry in lower:
                    return "micro"
            # Parameter suffix grammar (Requirement 1.4):
            # 1. Find the last ':' or '-' separated token.
            # 2. Strip leading non-numeric prefix characters from that token.
            # 3. Extract the contiguous decimal number followed by 'b' (case-insensitive).
            # 4. Treat the number as billions.
            # Examples: gemma3:4b→4, gemma4:e2b→2, llama3.2:3b→3, qwen3:0.6b→0.6
            param_count: float | None = None
            # Split on ':' or '-' and take the last token
            tokens = re.split(r"[:\-]", lower)
            for token in reversed(tokens):
                token = token.strip()
                if not token:
                    continue
                # Strip leading non-numeric prefix (letters, underscores, etc.)
                stripped = re.sub(r"^[^0-9]+", "", token)
                if not stripped:
                    continue
                # Match a decimal number followed by 'b' (optionally followed by
                # non-alphanumeric chars like '/', ' ', etc.)
                m = re.match(r"^(\d+\.?\d*)b(?:[^a-z0-9]|$)", stripped)
                if m:
                    param_count = float(m.group(1))
                    break
            if param_count is not None:
                if param_count <= 1.5:
                    return "micro"
                if param_count <= 4.0:
                    return "small"
                return "standard"
            return "standard"
        except Exception:
            return "standard"


    def _collect_descriptions(self, tools: list) -> tuple:
        """Build parallel name/description lists from tools."""
        from distr.core.agent.tools.loader import TOOL_DESCRIPTIONS
        names, descriptions = [], []
        for tool in tools:
            tool_name = tool.name
            class_name = type(tool).__name__
            desc = TOOL_DESCRIPTIONS.get(class_name)
            if not desc:
                logger.warning("Tool %r (class %s) missing description; using class name", tool_name, class_name)
                desc = class_name
            names.append(tool_name)
            descriptions.append(desc)
        return names, descriptions

    def _try_build_sbert(self, descriptions: list) -> bool:
        """Try building index with sentence-transformers. Returns True on success."""
        try:
            from sentence_transformers import SentenceTransformer
            model = SentenceTransformer("all-MiniLM-L6-v2")
            embeddings = model.encode(descriptions, convert_to_numpy=True)
            norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
            norms = np.where(norms == 0, 1.0, norms)
            self._matrix = (embeddings / norms).astype(np.float32)
            self._model = model
            self._backend = "sbert"
            return True
        except Exception as e:
            logger.info("sentence-transformers unavailable (%s), trying TF-IDF fallback", e)
            return False

    def _try_build_tfidf(self, descriptions: list) -> bool:
        """Build index with sklearn TF-IDF. Returns True on success."""
        try:
            from sklearn.feature_extraction.text import TfidfVectorizer
            vectorizer = TfidfVectorizer()
            tfidf_matrix = vectorizer.fit_transform(descriptions)
            self._vectorizer = vectorizer
            self._tfidf_matrix = tfidf_matrix
            self._backend = "tfidf"
            return True
        except Exception as e:
            logger.warning("TF-IDF fallback also failed: %s", e)
            return False

    def build_index(self, tools: list) -> None:
        """Encode tool descriptions. Tries sbert, falls back to TF-IDF.

        Always builds TF-IDF alongside sbert so small-tier models can use
        the lightweight retriever without sbert GPU overhead per request.
        """
        with self._build_lock:
            if not tools:
                logger.warning("build_index called with empty tool list")
                return

            start = time.time()
            names, descriptions = self._collect_descriptions(tools)

            ok = self._try_build_sbert(descriptions) or self._try_build_tfidf(descriptions)
            if not ok:
                logger.warning("All embedding backends failed; retrieval disabled")
                return

            # Always build TF-IDF as a lightweight fallback for small-tier models,
            # even when sbert succeeded — it's fast (~10ms) and avoids per-request
            # GPU inference for 2-4B models.
            if self._backend == "sbert" and self._tfidf_matrix is None:
                self._try_build_tfidf(descriptions)

            self._names = names
            self._index_ready.set()

            elapsed_ms = (time.time() - start) * 1000
            logger.info(
                "Tool index built (%s): %d tools in %.1f ms",
                self._backend, len(names), elapsed_ms,
            )


    def _retrieve_sbert(self, user_message: str, k: int) -> set:
        """Retrieve top-K tool names using sentence-transformer cosine sim."""
        query_vec = self._model.encode([user_message], convert_to_numpy=True)[0]
        query_norm = np.linalg.norm(query_vec)
        if query_norm > 0:
            query_vec = query_vec / query_norm
        scores = query_vec @ self._matrix.T
        top_k = np.argsort(scores)[::-1][:k]
        return {self._names[i] for i in top_k}

    def _retrieve_tfidf(self, user_message: str, k: int) -> set:
        """Retrieve top-K tool names using TF-IDF cosine sim."""
        from sklearn.metrics.pairwise import cosine_similarity
        query_vec = self._vectorizer.transform([user_message])
        scores = cosine_similarity(query_vec, self._tfidf_matrix)[0]
        top_k = np.argsort(scores)[::-1][:k]
        return {self._names[i] for i in top_k}

    def retrieve(
        self, user_message: str, model_name: str, k: int | None = None
    ) -> list[str] | None:
        """Return Active_Tool_Set tool names, or None to fall back to all tools."""
        # Kill switch
        if os.environ.get("DECISIONS_TOOL_RETRIEVAL_ENABLED", "true").lower() == "false":
            return None

        if not self.is_ready():
            from distr.core.agent.tools.loader import _tool_cache
            all_tools = list(_tool_cache.values())
            logger.warning(
                "ToolRetriever: index not available — returning all %d tools (token cost may be elevated)",
                len(all_tools),
            )
            return None

        # Micro tier — always-on only
        tier = self.classify_model_tier(model_name)
        if tier == "micro":
            result = list(ALWAYS_ON_NAMES)
            logger.debug("Tool retrieval: query=%r tier=%s tools=%s", user_message[:100], tier, result)
            log_retrieval_summary(
                user_message_preview=user_message,
                tier=tier,
                tool_count=len(result),
                tool_names_preview=result,
                backend="micro_always_on",
            )
            return result

        if k is None:
            k = _SMALL_MODEL_K if tier == "small" else _resolve_k()

        # Small tier — use TF-IDF if available (avoids sbert GPU overhead on every request)
        if tier == "small" and self._backend == "sbert" and self._tfidf_matrix is not None:
            retrieved = self._retrieve_tfidf(user_message, k)
        elif self._backend == "sbert":
            retrieved = self._retrieve_sbert(user_message, k)
        elif self._backend == "tfidf":
            retrieved = self._retrieve_tfidf(user_message, k)
        else:
            return None

        # Merge with always-on, append request_tool
        merged = retrieved | ALWAYS_ON_NAMES
        result = [name for name in merged if name != "request_tool"]
        result.append("request_tool")

        logger.debug("Tool retrieval (%s): query=%r tier=%s tools=%s",
                      self._backend, user_message[:100], tier, result)
        log_retrieval_summary(
            user_message_preview=user_message,
            tier=tier,
            tool_count=len(result),
            tool_names_preview=result,
            backend=self._backend,
        )
        return result
