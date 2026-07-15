from pathlib import Path

from scripts.summarize_pytest_junit import summarize


def test_summarizer_emits_actionable_annotation_and_markdown(tmp_path: Path):
    report = tmp_path / "pytest.xml"
    report.write_text(
        """<?xml version='1.0'?>
<testsuites><testsuite><testcase classname="tests.core.test_release" name="test_gate">
<failure message="failed">AssertionError: expected ready\nactual blocked</failure>
</testcase></testsuite></testsuites>""",
        encoding="utf-8",
    )

    annotations, markdown = summarize(report)

    assert len(annotations) == 1
    assert "file=tests/core/test_release.py" in annotations[0]
    assert "test_gate" in annotations[0]
    assert "%0A" in annotations[0]
    assert "actual blocked" in markdown
