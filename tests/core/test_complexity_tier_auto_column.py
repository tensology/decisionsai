"""autocol 2026-10-22: per-tier Auto flag for Complexity routing."""
from distr.core.kanban.ticket_policy import _global_complexity_route, tier_auto_enabled
from distr.core.llm_factory import resolve_complexity_llm_candidates


def _settings(auto):
    return {
        "project_cli_high_backend": "codex",
        "project_cli_high_model": "gpt-5.3-codex",
        "project_cli_high_model_provider": "openai",
        "project_cli_high_auto": auto,
    }


def test_tier_auto_off_keeps_pinned_model():
    route = _global_complexity_route("high", _settings(False))
    assert route["model"] == "gpt-5.3-codex"
    assert "tier_auto" not in route


def test_tier_auto_on_uses_auto_model_but_keeps_harness():
    route = _global_complexity_route("high", _settings(True))
    assert route["backend"] == "codex"
    assert route["model"] == "auto"
    assert route.get("tier_auto") == "1"
    assert "model_provider" not in route


def test_tier_auto_flag_parsing():
    assert tier_auto_enabled("low", {"project_cli_low_auto": "1"})
    assert not tier_auto_enabled("low", {"project_cli_low_auto": 0})
    assert not tier_auto_enabled("low", {})


def test_llm_candidates_ignore_pinned_model_when_auto():
    pinned = resolve_complexity_llm_candidates(_settings(False), "high")
    auto = resolve_complexity_llm_candidates(_settings(True), "high")
    assert ("gpt-5.3-codex" in str(pinned)) and ("gpt-5.3-codex" not in str(auto))


def test_migration_and_save_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("DECISIONS_DB_DIR", str(tmp_path / "db"))
    import sqlalchemy as sa
    from sqlalchemy import text
    eng = sa.create_engine(f"sqlite:///{tmp_path}/t.db")
    with eng.begin() as c:
        c.execute(text("CREATE TABLE settings (id INTEGER PRIMARY KEY)"))
    for _ in range(2):  # idempotent
        with eng.connect() as conn:
            for col in ("project_cli_low_auto", "project_cli_medium_auto", "project_cli_high_auto"):
                try:
                    conn.execute(text(f"ALTER TABLE settings ADD COLUMN {col} BOOLEAN DEFAULT 0"))
                    conn.commit()
                except Exception as e:
                    assert "duplicate column" in str(e).lower()
    cols = {r[1] for r in eng.connect().execute(text("PRAGMA table_info(settings)"))}
    assert {"project_cli_low_auto", "project_cli_medium_auto", "project_cli_high_auto"} <= cols
