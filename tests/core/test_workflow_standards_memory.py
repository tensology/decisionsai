from distr.core.workflow.standards_memory import (
    UNIVERSAL_WORKFLOW_STANDARDS,
    build_standards_context,
    feedback_to_standard,
    should_capture_feedback,
)


def test_standards_context_is_added_once():
    base = "Existing workflow rule."

    merged = build_standards_context(base)
    merged_again = build_standards_context(merged)

    assert "Existing workflow rule." in merged
    assert "[UNIVERSAL WORKFLOW QUALITY STANDARDS]" in merged
    assert merged_again.count("[UNIVERSAL WORKFLOW QUALITY STANDARDS]") == 1


def test_feedback_capture_ignores_short_acknowledgements():
    assert not should_capture_feedback("yes")
    assert not should_capture_feedback("ok carry on")


def test_feedback_capture_keeps_quality_rules_conservative():
    feedback = "This is not complete until the UI is validated with Playwright and evidence is visible."

    assert should_capture_feedback(feedback)
    standard = feedback_to_standard(feedback)

    assert standard.startswith("- Review feedback to apply on future workflow runs:")
    assert "Playwright" in standard
    assert UNIVERSAL_WORKFLOW_STANDARDS.strip()
