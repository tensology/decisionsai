"""Tests for bundled skills catalog and Hermes skill inference."""

from __future__ import annotations

from distr.core.skills.catalog import (
    filter_known_skill_ids,
    orchestrator_skill_catalog,
    infer_skills_for_ticket,
    is_google_skill,
    load_registry,
    skill_directory_for_id,
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


def test_load_registry_includes_vendored_ecc_skills_without_overriding_local():
    load_registry.cache_clear()
    rows = load_registry()
    by_id = {str(r.get("id") or ""): r for r in rows}

    assert by_id["configure-ecc"]["source"] == "ecc_vendor"
    assert by_id["configure-ecc"]["editable"] is False
    assert by_id["configure-ecc"]["provenance"]["license"] == "MIT"
    assert by_id["brainstorming"]["source"] != "ecc_vendor"
    assert len([r for r in rows if str(r.get("id") or "") == "brainstorming"]) == 1


def test_skill_directory_resolves_vendored_ecc_skill():
    load_registry.cache_clear()
    skill_dir = skill_directory_for_id("configure-ecc")

    assert skill_dir is not None
    assert skill_dir.name == "configure-ecc"
    assert "vendor/ecc/skills/configure-ecc" in str(skill_dir)


def test_registry_includes_ecc_skills_without_duplicate_native_ids():
    load_registry.cache_clear()
    rows = load_registry()
    by_id = {str(r.get("id") or ""): r for r in rows}

    assert "react-patterns" in by_id
    assert by_id["react-patterns"]["source"] == "ecc_vendor"
    assert "vendor/ecc/skills/react-patterns" in by_id["react-patterns"]["path"]

    safety_rows = [r for r in rows if str(r.get("id") or "") == "safety-guard"]
    assert len(safety_rows) == 1
    assert safety_rows[0]["source"] != "ecc_vendor"


def test_orchestrator_skill_catalog_reserves_room_for_vendored_ecc_skills():
    load_registry.cache_clear()
    rows = orchestrator_skill_catalog(limit=20)
    sources = {row["source"] for row in rows}
    ids = {row["id"] for row in rows}

    assert "local" in sources
    assert "ecc_vendor" in sources
    assert "react-patterns" in ids
