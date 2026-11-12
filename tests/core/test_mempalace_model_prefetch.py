"""Missing MiniLM model: never downloaded inline, restored in the background.

The fetch goes to a private temp dir, is SHA-checked, and only a complete
``onnx/`` is moved into place. No test here touches the network or ~/.cache.
"""

from __future__ import annotations

import hashlib
import io
import json
import tarfile

from distr.core.mempalace import adapter as adapter_module
from distr.core.mempalace import model_cache


def _archive_bytes(names=model_cache.REQUIRED_FILES) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name in names:
            data = f"content of {name}".encode()
            info = tarfile.TarInfo(f"onnx/{name}")
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def _fake_home(tmp_path, monkeypatch):
    monkeypatch.setattr(model_cache, "model_dir", lambda: tmp_path / "all-MiniLM-L6-v2")
    monkeypatch.setattr(model_cache, "onnx_dir", lambda: tmp_path / "all-MiniLM-L6-v2" / "onnx")


def _fake_download(payload: bytes):
    def _dl(url, dest):
        dest.write_bytes(payload)
        return hashlib.sha256(payload).hexdigest()

    return _dl


def test_required_files_match_chromadb():
    assert adapter_module._MINILM_REQUIRED_FILES == model_cache.REQUIRED_FILES
    try:
        from chromadb.utils.embedding_functions import ONNXMiniLM_L6_V2
    except Exception:
        return
    assert ONNXMiniLM_L6_V2.MODEL_NAME == model_cache.MODEL_NAME
    assert ONNXMiniLM_L6_V2.MODEL_DOWNLOAD_URL == model_cache.DOWNLOAD_URL
    assert ONNXMiniLM_L6_V2._MODEL_SHA256 == model_cache.SHA256


def test_fetch_installs_verified_model(tmp_path, monkeypatch):
    _fake_home(tmp_path, monkeypatch)
    payload = _archive_bytes()
    monkeypatch.setattr(model_cache, "_download", _fake_download(payload))
    status = model_cache.fetch_sync(sha256=hashlib.sha256(payload).hexdigest())
    assert status == "installed"
    assert model_cache.is_cached()
    leftovers = [p.name for p in (tmp_path / "all-MiniLM-L6-v2").iterdir()]
    assert leftovers == ["onnx"]  # temp dir and lock cleaned up


def test_fetch_rejects_bad_sha(tmp_path, monkeypatch):
    _fake_home(tmp_path, monkeypatch)
    monkeypatch.setattr(model_cache, "_download", _fake_download(_archive_bytes()))
    assert model_cache.fetch_sync(sha256="0" * 64) == "sha_mismatch"
    assert not (tmp_path / "all-MiniLM-L6-v2" / "onnx").exists()


def test_fetch_fills_partial_dir_without_deleting(tmp_path, monkeypatch):
    _fake_home(tmp_path, monkeypatch)
    onnx = tmp_path / "all-MiniLM-L6-v2" / "onnx"
    onnx.mkdir(parents=True)
    (onnx / "model.onnx").write_text("keep me")
    (onnx / "extra.bin").write_text("unrelated")
    payload = _archive_bytes()
    monkeypatch.setattr(model_cache, "_download", _fake_download(payload))
    assert model_cache.fetch_sync(sha256=hashlib.sha256(payload).hexdigest()) == "installed"
    assert (onnx / "model.onnx").read_text() == "keep me"
    assert (onnx / "extra.bin").exists()


def test_live_lock_blocks_second_fetch(tmp_path, monkeypatch):
    import os

    _fake_home(tmp_path, monkeypatch)
    parent = tmp_path / "all-MiniLM-L6-v2"
    parent.mkdir()
    (parent / model_cache.LOCK_NAME).write_text(str(os.getpid()))
    monkeypatch.setattr(model_cache, "_download", lambda *a: (_ for _ in ()).throw(AssertionError("no download")))
    assert model_cache.fetch_sync() == "locked"


def test_prefetch_off_switch(monkeypatch):
    monkeypatch.setattr(model_cache, "is_cached", lambda directory=None: False)
    monkeypatch.setenv(model_cache.ENV_PREFETCH, "0")
    assert model_cache.request_background_fetch("test") == "disabled"


def _palace(tmp_path):
    p = tmp_path / "palace"
    p.mkdir()
    (p / "mempalace_embedder.json").write_text(
        json.dumps({"mempalace_drawers": {"model_name": "minilm", "dimension": 384}})
    )
    return p


def test_inprocess_write_skips_and_requests_background_fetch(tmp_path, monkeypatch):
    monkeypatch.delenv("DECISIONS_MEMPALACE_ALLOW_INLINE_MODEL_DOWNLOAD", raising=False)
    missing = tmp_path / "onnx"
    monkeypatch.setattr(adapter_module, "_MINILM_ONNX_DIR", str(missing))
    monkeypatch.setattr(model_cache, "onnx_dir", lambda: missing)
    calls = []
    monkeypatch.setattr(model_cache, "request_background_fetch", lambda reason="": calls.append(reason) or "started")

    ad = adapter_module.MemPalaceAdapter(palace_path=str(_palace(tmp_path)))
    monkeypatch.setattr(ad, "_hub", lambda: None)
    monkeypatch.setattr(ad, "_hub_configured", lambda: False)
    monkeypatch.setattr(ad, "_import_ok", lambda: True)
    monkeypatch.setattr(ad, "_get_collection", lambda **k: (_ for _ in ()).throw(AssertionError("no embed")))

    res = ad.add_drawer("remember this", room="notes")
    assert res["success"] is False and res.get("deferred") is True
    assert calls == ["add_drawer"]


def test_search_guard_requests_fetch_only_for_real_cache_dir(tmp_path, monkeypatch):
    monkeypatch.delenv("DECISIONS_MEMPALACE_ALLOW_INLINE_MODEL_DOWNLOAD", raising=False)
    calls = []
    monkeypatch.setattr(model_cache, "request_background_fetch", lambda reason="": calls.append(reason) or "started")
    palace = _palace(tmp_path)

    def run():
        ad = adapter_module.MemPalaceAdapter(palace_path=str(palace))
        monkeypatch.setattr(ad, "_hub", lambda: None)
        monkeypatch.setattr(ad, "_hub_configured", lambda: True)
        monkeypatch.setattr(ad, "_import_ok", lambda: True)
        return ad.search("what did we decide")

    # Overridden readiness dir (not chromadb's): no fetch.
    monkeypatch.setattr(adapter_module, "_MINILM_ONNX_DIR", str(tmp_path / "elsewhere"))
    assert run()["results"] == []
    assert calls == []
    # The real cache dir is missing: fetch requested, search still returns at once.
    real = tmp_path / "chroma" / "onnx"
    monkeypatch.setattr(adapter_module, "_MINILM_ONNX_DIR", str(real))
    monkeypatch.setattr(model_cache, "onnx_dir", lambda: real)
    assert run()["results"] == []
    assert calls == ["search"]
