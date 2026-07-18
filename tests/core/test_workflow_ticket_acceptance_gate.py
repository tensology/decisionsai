from types import SimpleNamespace

from distr.core.workflow.verification import _run_verification


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
