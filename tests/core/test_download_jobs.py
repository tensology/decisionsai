"""Tests for download job tracking."""

from distr.core.download_jobs import create_job, get_job, list_jobs, parse_ytdlp_progress


def test_parse_ytdlp_progress_line():
    parsed = parse_ytdlp_progress("[download]  42.5% of  12.34MiB at  1.23MiB/s ETA 00:15")
    assert parsed is not None
    assert parsed["progress"] == 42.5
    assert parsed["speed"] == "1.23MiB/s"
    assert parsed["eta"] == "00:15"


def test_create_job_and_list():
    job_id = create_job(["https://example.com/watch?v=abc"], title="Test batch")
    row = get_job(job_id)
    assert row is not None
    assert row["status"] == "queued"
    assert row["title"] == "Test batch"
    jobs = list_jobs()
    assert any(j["id"] == job_id for j in jobs)
