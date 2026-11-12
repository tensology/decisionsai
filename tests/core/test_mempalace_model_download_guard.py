"""A missing embedding model must not block optional memory recall.

When ~/.cache/chroma lost the MiniLM ONNX model, the first direct-read search
downloaded ~80MB inline. Agent startup and chat turns waited on it, so the voice
pipeline never became ready and push-to-talk captured nothing.
"""

from __future__ import annotations

import json

from distr.core.mempalace import adapter as adapter_module
from distr.core.mempalace import wiring


def _palace(tmp_path, model="minilm"):
    p = tmp_path / "palace"
    p.mkdir()
    (p / "mempalace_embedder.json").write_text(
        json.dumps({"mempalace_drawers": {"model_name": model, "dimension": 384}})
    )
    return p


def test_model_missing_means_not_ready(tmp_path, monkeypatch):
    monkeypatch.delenv("DECISIONS_MEMPALACE_ALLOW_INLINE_MODEL_DOWNLOAD", raising=False)
    monkeypatch.setattr(adapter_module, "_MINILM_ONNX_DIR", str(tmp_path / "nope"))
    assert adapter_module.embedding_model_ready(str(_palace(tmp_path))) is False


def test_model_present_means_ready(tmp_path, monkeypatch):
    onnx = tmp_path / "onnx"
    onnx.mkdir()
    for name in adapter_module._MINILM_REQUIRED_FILES:
        (onnx / name).write_text("x")
    monkeypatch.setattr(adapter_module, "_MINILM_ONNX_DIR", str(onnx))
    assert adapter_module.embedding_model_ready(str(_palace(tmp_path))) is True


def test_other_embedder_is_not_gated(tmp_path, monkeypatch):
    monkeypatch.setattr(adapter_module, "_MINILM_ONNX_DIR", str(tmp_path / "nope"))
    assert adapter_module.embedding_model_ready(str(_palace(tmp_path, "embeddinggemma"))) is True


def test_search_skips_direct_read_when_model_missing(tmp_path, monkeypatch):
    palace = _palace(tmp_path)
    monkeypatch.setattr(adapter_module, "_MINILM_ONNX_DIR", str(tmp_path / "nope"))
    ad = adapter_module.MemPalaceAdapter(palace_path=str(palace))
    monkeypatch.setattr(ad, "_hub", lambda: None)
    monkeypatch.setattr(ad, "_hub_configured", lambda: True)
    monkeypatch.setattr(ad, "_import_ok", lambda: True)

    import sys
    import types

    called = {"n": 0}
    fake = types.ModuleType("mempalace.searcher")

    def search_memories(*a, **k):
        called["n"] += 1
        return {"results": [{"text": "x"}]}

    fake.search_memories = search_memories
    monkeypatch.setitem(sys.modules, "mempalace.searcher", fake)

    res = ad.search("what did we decide", wing="decisions")
    assert called["n"] == 0
    assert res["results"] == []
    assert "not cached" in res["error"]


def test_rag_and_markdown_recall_use_short_timeout(monkeypatch):
    seen = []

    class _Adapter:
        def format_search_as_context(self, query, **kwargs):
            seen.append(kwargs.get("timeout"))
            return ""

    monkeypatch.setattr(wiring, "_enabled", lambda settings=None: True)
    monkeypatch.setattr(wiring, "_adapter", lambda: _Adapter())

    wiring.prefer_read_rag_context("a question about the project")
    wiring.prefer_read_markdown_context("prefs")

    assert seen and all(t == wiring.ORCHESTRATOR_CONTEXT_TIMEOUT_S for t in seen)
    assert len(seen) == 4
