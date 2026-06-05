"""Tests for bundled skills catalog and Hermes skill inference."""

from __future__ import annotations

from distr.core.skills.catalog import (
    filter_known_skill_ids,
    hermes_skill_catalog,
    infer_skills_for_ticket,
    is_google_skill,
    load_registry,
)


def test_load_registry_includes_google_skills():
    rows = load_registry()
    ids = {str(r.get("id") or "") for r in rows}
    assert "gemini-api" in ids
    assert "bigquery-basics" in ids


def test_infer_skills_for_ticket_bigquery():
    skills = infer_skills_for_ticket("Add BigQuery dataset export for analytics dashboard")
    assert "bigquery-basics" in skills


def test_infer_skills_for_ticket_gemini():
    skills = infer_skills_for_ticket("Integrate Gemini API on Agent Platform for chat")
    assert "gemini-api" in skills


def test_filter_known_skill_ids_drops_unknown():
    known = filter_known_skill_ids(["gemini-api", "not-a-real-skill", "bigquery-basics"])
    assert known == ["gemini-api", "bigquery-basics"]


def test_is_google_skill():
    assert is_google_skill("gemini-api")
    assert is_google_skill("google-cloud-waf-security")
    assert not is_google_skill("brainstorming")


def test_registry_includes_vendored_ecc_skills_without_duplicate_native_ids():
    rows = load_registry()
    by_id = {str(r.get("id") or ""): r for r in rows}

    assert "react-patterns" in by_id
    assert by_id["react-patterns"]["source"] == "ecc"
    assert "vendor/ecc/skills/react-patterns" in by_id["react-patterns"]["path"]

    safety_rows = [r for r in rows if str(r.get("id") or "") == "safety-guard"]
    assert len(safety_rows) == 1
    assert safety_rows[0]["source"] != "ecc"
    assert safety_rows[0]["vendor_sources"][0]["source"] == "ecc"


def test_hermes_skill_catalog_reserves_room_for_vendored_ecc_skills():
    rows = hermes_skill_catalog(limit=20)
    sources = {row["source"] for row in rows}
    ids = {row["id"] for row in rows}

    assert "local" in sources
    assert "ecc" in sources
    assert "react-patterns" in ids
