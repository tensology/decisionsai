from scripts.soak_web_ui import summarize


def test_soak_summary_records_release_metrics_and_failures() -> None:
    result = summarize([1.0, 2.0, 3.0, 10.0], [{"error": "timeout"}], 600.2)

    assert result == {
        "duration_seconds": 600.2,
        "requests": 5,
        "successful_requests": 4,
        "failures": 1,
        "average_ms": 4.0,
        "median_ms": 2.5,
        "p95_ms": 10.0,
        "max_ms": 10.0,
        "failure_details": [{"error": "timeout"}],
    }
