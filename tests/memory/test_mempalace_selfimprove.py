"""Self-improvement capture / nightly / harness wiring tests (no network, temp HOME)."""

from __future__ import annotations

import json
import tomllib
from pathlib import Path

import pytest


@pytest.fixture()
def fresh_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    bindir = tmp_path / "bin"
    bindir.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("PATH", str(bindir))  # no claude/codex/kilo CLIs
    monkeypatch.setenv("DECISIONS_MEMPALACE_PYTHON", "/venv/bin/python")
    monkeypatch.delenv("DECISIONS_MEMPALACE_HARNESSES", raising=False)
    monkeypatch.delenv("DECISIONS_FRUSTRATION_CAPTURE", raising=False)
    monkeypatch.delenv("DECISIONS_SELFIMPROVE_REVIEWER", raising=False)
    return home


# ── redaction ────────────────────────────────────────────────────────────────

def test_redact_masks_secrets_and_personal_data():
    from distr.core.mempalace.selfimprove.redact import redact

    raw = ("key sk-[REDACTED] mail paul@example.com "
           "url https://[REDACTED]@host/x Authorization: Bearer abc.def.ghi password=hunter22")
    out = redact(raw)
    for leaked in ("paul@example.com", "hunter22"):
        assert leaked not in out
    assert redact(raw, enabled=False) == raw


# ── detection ────────────────────────────────────────────────────────────────

def test_regex_signal_and_combine():
    from distr.core.mempalace.selfimprove import detect

    sig = detect.regex_signal("No, that's wrong again. I told you to use the hub", [])
    assert sig and sig["severity"] >= 2
    assert detect.regex_signal("Thanks, looks great", []) is None
    rep = detect.regex_signal("please restart decisions and check health",
                              ["please restart decisions and check the health"])
    assert rep and "repeated_ask" in rep["tags"]
    # Laya alone above threshold counts; below threshold with no regex does not
    assert detect.combine(None, {"frustrated": 0.9, "severity": 2.5, "kind": "correction"})
    assert detect.combine(None, {"frustrated": 0.1, "severity": 1.0, "kind": "normal"}) is None


def test_clean_user_text_drops_injected_wrappers():
    from distr.core.mempalace.selfimprove.sources import clean_user_text

    assert clean_user_text('<external_codex_apps_open_page>{"x":1}</external_codex_apps_open_page>') == ""
    assert clean_user_text("<environment_context>cwd</environment_context>") == ""
    assert clean_user_text("<realtime_delegation><input>No no, start again</input></realtime_delegation>") == "No no, start again"
    assert clean_user_text("# Files mentioned by the user:\n## a.png: /a.png\n## My request for Codex:\nwhy broken") == "why broken"


def test_sources_read_new_is_incremental(tmp_path):
    from distr.core.mempalace.selfimprove import sources

    f = tmp_path / "s.jsonl"
    lines = [
        {"type": "session", "cwd": "/x/Proj", "id": "s1"},
        {"type": "message", "message": {"role": "user", "content": [{"type": "text", "text": "do it"}]}},
        {"type": "message", "message": {"role": "toolResult", "content": [{"type": "text", "text": "boom"}],
                                         "isError": True, "toolName": "bash"}},
    ]
    f.write_text("\n".join(json.dumps(x) for x in lines) + "\n{partial", encoding="utf-8")
    meta: dict = {}
    turns, off = sources.read_new("pi", str(f), 0, meta)
    assert [t["role"] for t in turns] == ["user", "tool"]
    assert turns[0]["project"] == "proj" and turns[1]["is_error"]
    assert off < f.stat().st_size  # partial line left for next run
    again, off2 = sources.read_new("pi", str(f), off, meta)
    assert again == [] and off2 == off


# ── nightly ──────────────────────────────────────────────────────────────────

def test_nightly_excludes_live_money_and_clusters():
    from distr.core.mempalace.selfimprove import nightly

    def ev(project, quote, skill=None, sev=2):
        return {"stream": f"frustrations/{project}", "metadata": {
            "project": project, "skill": skill, "tags": ["correction"], "severity": sev,
            "user_quote": quote, "harness": "claude", "ts": "2026-10-22T09:00:00+02:00"}}

    events = [ev("auctionnow", "wrong bid again"), ev("decisions", "no, use the hub not chroma", "mempalace"),
              ev("decisions", "no, use the hub not chroma please", "mempalace"), ev("decisions", "wrong file")]
    kept = [e for e in events if not nightly._excluded(e)]
    assert len(kept) == 3
    clusters = nightly.cluster(kept)
    top = clusters[0]
    assert top["scope"] == "mempalace" and top["count"] == 2 and len(top["examples"]) == 1  # near-dupes merged


def test_nightly_only_every_second_night():
    from distr.core.mempalace.selfimprove import nightly

    day = 86400
    assert nightly.is_review_night(20000 * day + 7200) != nightly.is_review_night(20001 * day + 7200)


def test_extract_diff():
    from distr.core.mempalace.selfimprove.nightly import _extract_diff

    assert _extract_diff("NO_CHANGE") is None
    d = _extract_diff("```diff\n--- a/SKILL.md\n+++ b/SKILL.md\n@@ -1 +1,2 @@\n x\n+y\n```")
    assert d.startswith("--- a/SKILL.md") and d.endswith("+y\n")


def test_reviewer_chain_falls_through(fresh_home, monkeypatch):
    from distr.core.mempalace.selfimprove import config

    claude = Path(fresh_home.parent / "bin" / "claude")
    claude.write_text("#!/bin/sh\necho 'Credit balance is too low'\nexit 1\n")
    claude.chmod(0o755)
    real_cli_path = config._cli_path
    monkeypatch.setattr(config, "_cli_path", lambda name: None if name == "codex" else real_cli_path(name))
    monkeypatch.setattr(config, "_decisions_llm", lambda prompt: ("openrouter:test-model", "NO_CHANGE"))
    assert [n for n, _ in config.reviewer_chain()] == ["claude", "decisions"]
    who, text = config.run_reviewer("prompt")
    assert who == "decisions(openrouter:test-model)" and text == "NO_CHANGE"


def test_llm_defaults_to_free_openrouter_models(monkeypatch):
    from distr.core.mempalace.selfimprove import llm

    monkeypatch.delenv(llm.PROVIDER_ENV, raising=False)
    monkeypatch.delenv(llm.MODELS_ENV, raising=False)
    provider, models = llm.config()
    assert provider == "openrouter"
    assert models == ["minimax/minimax-m3.1-flash", "thinkingmachines/inkling-small:free"]


def test_capture_off_switch(fresh_home, monkeypatch):
    from distr.core.mempalace.selfimprove import capture, nightly

    monkeypatch.setenv("DECISIONS_FRUSTRATION_CAPTURE", "0")
    assert "skipped" in capture.run(dry_run=True)
    assert "skipped" in nightly.run(dry_run=True)


# ── harness wiring ───────────────────────────────────────────────────────────

def test_wire_and_unwire_preserve_existing_entries(fresh_home):
    from distr.core.mempalace import harnesses

    (fresh_home / ".cursor").mkdir()
    (fresh_home / ".cursor" / "mcp.json").write_text(json.dumps({"mcpServers": {"mailshot": {"url": "http://x"}}}))
    (fresh_home / ".codex").mkdir()
    (fresh_home / ".codex" / "config.toml").write_text('model = "o3"\n\n[mcp_servers.mailshot]\ncommand = "ms"\n')
    (fresh_home / ".config" / "kilo").mkdir(parents=True)
    (fresh_home / ".config" / "kilo" / "kilo.jsonc").write_text(
        '{\n  // my settings\n  "$schema": "https://app.kilo.ai/config.json",\n  "model": "x",\n}\n')
    logs: list[str] = []
    out = harnesses.wire_all(logs.append)
    assert out["cursor"]["installed"] and out["codex"]["installed"] and out["kilo"]["installed"]
    assert out.get("claude", {}).get("installed") is False  # not installed -> skipped

    cur = json.loads((fresh_home / ".cursor" / "mcp.json").read_text())["mcpServers"]
    assert set(cur) == {"mailshot", "mempalace"}
    assert cur["mempalace"]["command"] == "/venv/bin/python"
    toml = tomllib.loads((fresh_home / ".codex" / "config.toml").read_text())
    assert set(toml["mcp_servers"]) == {"mailshot", "mempalace"} and toml["model"] == "o3"
    kilo_txt = (fresh_home / ".config" / "kilo" / "kilo.jsonc").read_text()
    assert "// my settings" in kilo_txt and '"mempalace"' in kilo_txt
    assert list(fresh_home.glob(".cursor/mcp.json.bak-*-mempalace"))
    assert not any("token" in json.dumps(v).lower() for v in cur.values())  # no secret in configs

    # idempotent
    harnesses.wire_all(logs.append)
    assert (fresh_home / ".codex" / "config.toml").read_text().count("[mcp_servers.mempalace]") == 1

    harnesses.unwire_all(logs.append)
    assert set(json.loads((fresh_home / ".cursor" / "mcp.json").read_text())["mcpServers"]) == {"mailshot"}
    t2 = tomllib.loads((fresh_home / ".codex" / "config.toml").read_text())
    assert set(t2["mcp_servers"]) == {"mailshot"}
    assert '"mempalace"' not in (fresh_home / ".config" / "kilo" / "kilo.jsonc").read_text()


def test_harness_wiring_opt_out(fresh_home, monkeypatch):
    from distr.core.mempalace import installer

    monkeypatch.setenv("DECISIONS_MEMPALACE_HARNESS_WIRING", "0")
    (fresh_home / ".cursor").mkdir()
    (fresh_home / ".cursor" / "mcp.json").write_text("{}")
    installer.main(["install", "--only", "harnesses", "--no-launchctl"])
    assert json.loads((fresh_home / ".cursor" / "mcp.json").read_text()) == {}
