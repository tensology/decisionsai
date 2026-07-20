from types import SimpleNamespace

from distr.core.workflow.verification import _run_verification, ticket_acceptance_findings


def _review_step():
    return SimpleNamespace(
        id=4,
        name="Independently review and validate the change",
        validation_type="llm_judgment",
        validation_prompt="Check the ticket acceptance criteria and evidence.",
    )


def test_review_fails_when_ticket_requires_screenshot_but_only_capture_notes_exist():
    passed = _run_verification(
        _review_step(),
        "Documentation looks good. Browser evidence is capture-note based because screenshot tooling was unavailable.",
        True,
        ticket_context="Browser evidence required: screenshots or a short screen recording.",
    )

    assert passed is False


def test_review_fails_when_research_only_ticket_accepts_code_cleanup():
    passed = _run_verification(
        _review_step(),
        "The docs are present and the innocuous code cleanup in the frontend is acceptable.",
        True,
        ticket_context="Research only. Non-goals: No code changes.",
    )

    assert passed is False


def test_review_fails_when_copy_first_ticket_has_no_copy_evidence():
    passed = _run_verification(
        _review_step(),
        "The implementation files look complete and tests pass.",
        True,
        ticket_context="Copy-first constraint: files must copy from the source repository before editing.",
    )

    assert passed is False


def test_review_allows_configured_verifier_when_required_artifacts_are_reported(monkeypatch):
    monkeypatch.setattr(
        "distr.core.workflow.verification._verify_llm_judgment",
        lambda *_args, **_kwargs: True,
    )
    passed = _run_verification(
        _review_step(),
        "Evidence: docs/evidence/home.png. Copied from source with rsync -a; tests pass.",
        True,
        ticket_context="Browser evidence required: screenshot. Copy-first constraint applies.",
    )

    assert passed is True


def test_ticket_acceptance_finding_explains_missing_spotify_and_youtube_media(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "distr.core.workflow.verification._project_folder",
        lambda _project_id: str(tmp_path),
    )
    (tmp_path / "spotify.png").write_bytes(b"png")

    findings = ticket_acceptance_findings(
        _review_step(),
        "Browser evidence: spotify.png. YouTube evidence is N/A.",
        "Browser evidence required: screenshots or capture of the Spotify and YouTube pages.",
        project_id=16,
    )

    assert [item["code"] for item in findings] == ["missing_browser_media"]
    assert "found 1" in findings[0]["message"]
    assert "Do not report browser evidence as N/A" in findings[0]["correction_hint"]


def test_ticket_acceptance_rejects_reported_media_paths_that_do_not_exist(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "distr.core.workflow.verification._project_folder",
        lambda _project_id: str(tmp_path),
    )

    findings = ticket_acceptance_findings(
        _review_step(),
        "Evidence: docs/evidence/spotify.png and docs/evidence/youtube.png.",
        "Browser evidence required: screenshots or capture of the Spotify and YouTube pages.",
        project_id=16,
    )

    assert findings[0]["code"] == "missing_browser_media"
    assert "found 0" in findings[0]["message"]


def test_correction_step_does_not_reperform_the_independent_visual_acceptance_gate():
    correction = SimpleNamespace(
        id=5,
        name="Correct defects found by validation",
        config='{"step_role": "implementation"}',
    )

    findings = ticket_acceptance_findings(
        correction,
        "rerun_results: N/A; skip_or_blocker_reason: no ticket defect; next_action: report",
        "Browser evidence required: screenshots of Spotify and YouTube.",
        project_id=16,
    )

    assert findings == []
