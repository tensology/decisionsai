"""Unit tests for the durable cost ledger — sqlite/temp, no network."""

from __future__ import annotations

from sqlalchemy import create_engine, text

from distr.core.cost_ledger import flags, schema, service
from distr.core.cost_ledger import pricing


def _isolated(monkeypatch, tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'cost_ledger.db'}")
    monkeypatch.setattr(schema, "engine", engine)
    monkeypatch.setattr(service, "engine", engine)
    schema.ensure_tables(bind=engine)
    monkeypatch.setenv("DECISIONS_COST_LEDGER_ENABLED", "1")
    return engine


def test_record_usage_openai_provider_cost(monkeypatch, tmp_path):
    engine = _isolated(monkeypatch, tmp_path)
    row = service.record_usage(
        run_id=10,
        ticket_id=3,
        project_id=None,
        client_key="acme",
        worker_id="w1",
        provider="openai",
        model="gpt-4o",
        tokens_in=1_000_000,
        tokens_out=1_000_000,
        source="workflow",
        duration_seconds=12,
        bind=engine,
    )
    assert row is not None
    assert row["provider_cost_usd"] > 0
    assert row["resource_cost_usd"] == 0.0
    assert row["blended_cost_usd"] == row["provider_cost_usd"]
    assert row["tokens_total"] == 2_000_000
    assert row["client_key"] == "acme"
    assert row["day_sast"]


def test_local_ollama_zero_provider_plus_resource(monkeypatch, tmp_path):
    engine = _isolated(monkeypatch, tmp_path)
    row = service.record_usage(
        run_id=11,
        provider="ollama",
        model="qwen2.5:27b",
        tokens_total=5000,
        duration_seconds=3600,
        source="harness",
        bind=engine,
    )
    assert row["provider_cost_usd"] == 0.0
    assert row["resource_cost_usd"] > 0
    assert "kWh" in (row["resource_notes"] or "")
    assert row["blended_cost_usd"] == row["resource_cost_usd"]


def test_disabled_is_noop(monkeypatch, tmp_path):
    engine = _isolated(monkeypatch, tmp_path)
    monkeypatch.setenv("DECISIONS_COST_LEDGER_ENABLED", "0")
    assert flags.is_cost_ledger_enabled({}) is False
    assert service.record_usage(run_id=1, provider="openai", tokens_total=100, bind=engine) is None
    assert service.link_deliverable(1, "/tmp/out.md", bind=engine) is None
    assert service.finalize_run(1, bind=engine) == 0


def test_link_deliverable_and_finalize(monkeypatch, tmp_path):
    engine = _isolated(monkeypatch, tmp_path)
    started = 1_700_000_000.0
    row = service.record_usage(
        run_id=42,
        provider="openai",
        tokens_in=100,
        tokens_out=50,
        started_at=started,
        ended_at=None,
        duration_seconds=None,
        source="workflow",
        bind=engine,
    )
    link = service.link_deliverable(42, "artifacts/report.md", deliverable_id="art-1", bind=engine)
    assert link["run_id"] == 42
    assert link["ledger_entry_id"] == row["id"]
    updated = service.finalize_run(42, ended_at=started + 90, bind=engine)
    assert updated >= 1
    with engine.connect() as conn:
        entry = conn.execute(
            text("SELECT ended_at, duration_seconds, deliverable_ref FROM cost_ledger_entries WHERE id=:id"),
            {"id": row["id"]},
        ).mappings().one()
        assert entry["ended_at"] == started + 90
        assert float(entry["duration_seconds"]) == 90.0
        assert entry["deliverable_ref"] == "artifacts/report.md"
        assert conn.execute(text("SELECT COUNT(*) FROM cost_ledger_deliverables")).scalar_one() == 1


def test_rollups_and_display_modes(monkeypatch, tmp_path):
    engine = _isolated(monkeypatch, tmp_path)
    service.record_usage(
        run_id=1,
        project_id=7,
        client_key="acme",
        provider="openai",
        tokens_in=1000,
        tokens_out=500,
        source="workflow",
        bind=engine,
    )
    service.record_usage(
        run_id=2,
        project_id=7,
        client_key="acme",
        provider="ollama",
        model="30b",
        tokens_total=200,
        duration_seconds=120,
        source="harness",
        bind=engine,
    )
    blended = service.list_entries(display="blended", bind=engine)
    assert blended["display"] == "blended"
    assert "tokens_in" not in blended["items"][0]
    assert "cost_usd" in blended["items"][0]

    explicit = service.list_entries(display="explicit", bind=engine)
    assert "tokens_total" in explicit["items"][0] or "tokens_in" in explicit["items"][0]
    assert "provider_cost_usd" in explicit["items"][0]

    rollups = service.cost_rollups(display="blended", bind=engine)
    assert rollups["entry_count"] == 2
    assert rollups["total_cost_usd"] > 0
    assert any(r["key"] == "7" for r in rollups["by_project"])
    assert any(r["key"] == "acme" for r in rollups["by_client"])
    assert rollups["by_day"]
    # Blended rollups omit token/provider split lines
    assert "tokens_total" not in rollups["by_day"][0]


def test_pricing_helpers_no_network():
    assert pricing.estimate_provider_cost_usd(provider="ollama", tokens_total=99999) == 0.0
    cost = pricing.estimate_provider_cost_usd(provider="anthropic", tokens_in=1_000_000, tokens_out=0)
    assert cost == pricing.rates_for("anthropic")["input"]
    res, notes = pricing.estimate_local_resource_cost_usd(duration_seconds=0)
    assert res == 0.0
    assert "no duration" in notes


def test_derive_client_key_slug():
    assert service.derive_client_key(project_name="Acme Corp!") == "acme-corp"
    assert service.derive_client_key(project_name="") == ""


def test_reports_facade(monkeypatch, tmp_path):
    engine = _isolated(monkeypatch, tmp_path)
    service.record_usage(
        run_id=9,
        provider="codex",
        tokens_in=10,
        tokens_out=10,
        source="execution",
        bind=engine,
    )
    from distr.core.reports import service as reports

    # Point reports → cost ledger service engine via monkeypatch on cost_ledger.service
    listing = reports.list_cost_entries(limit=10, display="explicit")
    assert listing["items"]
    rollups = reports.cost_rollups(display="blended")
    assert rollups["entry_count"] >= 1
