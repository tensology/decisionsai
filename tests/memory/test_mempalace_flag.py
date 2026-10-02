"""MemPalace backend flag defaults ON; adapter/seed soft-fail without chromadb."""

from __future__ import annotations

import re
from pathlib import Path

import pytest


def test_mempalace_flag_default_on(monkeypatch):
    monkeypatch.delenv("DECISIONS_MEMPALACE_MEMORY_BACKEND", raising=False)
    from distr.core.mempalace.flags import DEFAULT_ENABLED, is_mempalace_memory_backend_enabled

    assert DEFAULT_ENABLED is True
    # Empty settings → default ON
    assert is_mempalace_memory_backend_enabled({}) is True
    assert is_mempalace_memory_backend_enabled({"mempalace_memory_backend": False}) is False


def test_mempalace_flag_env_override_off(monkeypatch):
    monkeypatch.setenv("DECISIONS_MEMPALACE_MEMORY_BACKEND", "0")
    from distr.core.mempalace.flags import is_mempalace_memory_backend_enabled

    assert is_mempalace_memory_backend_enabled({"mempalace_memory_backend": True}) is False


def test_mempalace_flag_env_override_on(monkeypatch):
    monkeypatch.setenv("DECISIONS_MEMPALACE_MEMORY_BACKEND", "1")
    from distr.core.mempalace.flags import is_mempalace_memory_backend_enabled

    assert is_mempalace_memory_backend_enabled({"mempalace_memory_backend": False}) is True


def test_mempalace_flag_settings_on(monkeypatch):
    monkeypatch.delenv("DECISIONS_MEMPALACE_MEMORY_BACKEND", raising=False)
    from distr.core.mempalace.flags import is_mempalace_memory_backend_enabled

    assert is_mempalace_memory_backend_enabled({"mempalace_memory_backend": True}) is True


def test_settings_default_mempalace_true():
    """conftest stubs distr.core.settings as MagicMock — assert source default."""
    src = Path(__file__).resolve().parents[2] / "distr" / "core" / "settings.py"
    body = src.read_text(encoding="utf-8")
    m = re.search(r'"mempalace_memory_backend"\s*:\s*(True|False)', body)
    assert m is not None, "mempalace_memory_backend missing from DEFAULT_SETTINGS"
    assert m.group(1) == "True"


def test_wiring_noop_when_flag_off(monkeypatch):
    monkeypatch.delenv("DECISIONS_MEMPALACE_MEMORY_BACKEND", raising=False)
    from distr.core.mempalace import wiring

    assert wiring.dual_write_learning(insight="x", key="k", settings={"mempalace_memory_backend": False}) is None
    assert wiring.prefer_read_rag_context("hello world query", settings={"mempalace_memory_backend": False}) is None


def test_bootstrap_reference_path_exists():
    from distr.core.mempalace.bootstrap import reference_mempalace_root

    root = reference_mempalace_root()
    assert (root / "mempalace" / "__init__.py").is_file()


def test_bundled_seed_pack_resolves():
    from distr.core.mempalace.paths import bundled_seed_corpus_pack
    from distr.core.mempalace.mapping import WING_SEED_CORPUS_PACK

    pack = bundled_seed_corpus_pack()
    assert pack is not None, "expected distr/seed-corpus-pack to be vendored"
    assert (pack / "mempalace.yaml").is_file()
    assert (pack / "manifest.json").is_file()
    text = (pack / "mempalace.yaml").read_text(encoding="utf-8")
    assert "seed_corpus_pack" in text or WING_SEED_CORPUS_PACK in text


def test_palace_path_default_is_decisions_owned(monkeypatch):
    monkeypatch.delenv("DECISIONS_MEMPALACE_PALACE_PATH", raising=False)
    from distr.core.mempalace.paths import default_palace_path

    p = default_palace_path()
    assert p == (Path.home() / ".decisions" / "mempalace" / "palace").resolve()


def test_palace_path_env_override(monkeypatch, tmp_path):
    monkeypatch.setenv("DECISIONS_MEMPALACE_PALACE_PATH", str(tmp_path / "custom-palace"))
    from distr.core.mempalace.paths import default_palace_path

    assert default_palace_path() == (tmp_path / "custom-palace").resolve()


def test_adapter_status_soft_fail_without_chromadb():
    from distr.core.mempalace.adapter import MemPalaceAdapter

    st = MemPalaceAdapter().status()
    assert "palace_path" in st
    assert "available" in st
    assert isinstance(st["available"], bool)


def test_seed_status_reports_pack(monkeypatch):
    monkeypatch.delenv("DECISIONS_SEED_CORPUS_PACK", raising=False)
    from distr.core.mempalace.seed import last_seed_status, read_seed_manifest

    st = last_seed_status()
    assert st.get("bundled_pack")
    mf = read_seed_manifest()
    assert mf.get("mempalace_wing") == "seed_corpus_pack" or mf.get("id") == "seed-corpus-pack"
