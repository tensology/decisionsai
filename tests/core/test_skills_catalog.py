"""Tests for bundled skills catalog and Hermes skill inference."""

from __future__ import annotations

from distr.core.skills.catalog import (
    filter_known_skill_ids,
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
