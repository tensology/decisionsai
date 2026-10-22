import os
from distr.core.project_cli_backends import catalog_probe


def test_resolve_prefers_path(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda name: "/x/bin/codex")
    assert catalog_probe.resolve_codex_binary() == "/x/bin/codex"


def test_resolve_falls_back_to_homebrew(monkeypatch, tmp_path):
    monkeypatch.setattr("shutil.which", lambda name: None)
    real_isfile = os.path.isfile
    monkeypatch.setattr(os.path, "isfile", lambda p: p == "/opt/homebrew/bin/codex" or (real_isfile(p) and False))
    monkeypatch.setattr(os, "access", lambda p, mode: p == "/opt/homebrew/bin/codex")
    assert catalog_probe.resolve_codex_binary() == "/opt/homebrew/bin/codex"


def test_missing_codex_gives_plain_message(monkeypatch):
    monkeypatch.setattr(catalog_probe, "resolve_codex_binary", lambda: None)
    models, source, message = catalog_probe.codex_models()
    assert source == "codex-unverified"
    assert message == "Codex CLI not found. Install it with: npm i -g @openai/codex"
    assert [m["id"] for m in models] == ["auto"]
