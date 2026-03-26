"""Unit tests for scheduler edge cases.

Validates: Requirements 1.3, 1.6, 2.3
"""
import calendar
from datetime import datetime, timedelta

import pytest

from distr.core.kanban.scheduler import compute_next_run


class TestHourlyEmptyHours:
    """Requirement 1.3: Empty agent_hours returns None."""

    def test_empty_hours_list_returns_none(self):
        result = compute_next_run(
            frequency="hourly",
            last_run_at=datetime(2024, 6, 15, 10, 0, 0),
            agent_time="09:00",
            agent_hours=[],
        )
        assert result is None

    def test_none_hours_returns_none(self):
        result = compute_next_run(
            frequency="hourly",
            last_run_at=datetime(2024, 6, 15, 10, 0, 0),
            agent_time="09:00",
            agent_hours=None,
        )
        assert result is None


class TestHourlyOutOfRange:
    """Requirement 1.6: Out-of-range hours are filtered."""

    def test_all_out_of_range_returns_none(self):
        result = compute_next_run(
            frequency="hourly",
            last_run_at=datetime(2024, 6, 15, 10, 0, 0),
            agent_time="09:00",
            agent_hours=[-1, 24, 100, -50],
        )
        assert result is None

    def test_mixed_valid_and_invalid_hours(self):
        result = compute_next_run(
            frequency="hourly",
            last_run_at=datetime(2024, 6, 15, 10, 0, 0),
            agent_time="09:00",
            agent_hours=[-1, 14, 24, 18],
        )
        assert result is not None
        # Should pick hour 14 (the earliest valid hour after 10)
        assert result == datetime(2024, 6, 15, 14, 0, 0)

    def test_negative_hours_filtered(self):
        result = compute_next_run(
            frequency="hourly",
            last_run_at=datetime(2024, 6, 15, 5, 0, 0),
            agent_time="09:00",
            agent_hours=[-5, -1, 8],
        )
        assert result is not None
        assert result.hour == 8

    def test_duplicates_are_deduplicated(self):
        result = compute_next_run(
            frequency="hourly",
            last_run_at=datetime(2024, 6, 15, 10, 0, 0),
            agent_time="09:00",
            agent_hours=[14, 14, 14, 18, 18],
        )
        assert result is not None
        assert result == datetime(2024, 6, 15, 14, 0, 0)


class TestFortnightlyEmptyDays:
    """Requirement 2.3: Fortnightly with empty days falls back to +14 days."""

    def test_empty_days_falls_back_to_14_days(self):
        base = datetime(2024, 6, 1, 12, 0, 0)
        result = compute_next_run(
            frequency="fortnightly",
            last_run_at=base,
            agent_time="09:00",
            agent_days=[],
        )
        expected = datetime(2024, 6, 15, 9, 0, 0)
        assert result == expected

    def test_none_days_falls_back_to_14_days(self):
        base = datetime(2024, 6, 1, 12, 0, 0)
        result = compute_next_run(
            frequency="fortnightly",
            last_run_at=base,
            agent_time="09:00",
            agent_days=None,
        )
        expected = datetime(2024, 6, 15, 9, 0, 0)
        assert result == expected


class TestMonthlyDayClamping:
    """Monthly frequency clamps day to last day of the target month."""

    def test_jan31_clamps_to_feb28_non_leap(self):
        base = datetime(2023, 1, 31, 10, 0, 0)
        result = compute_next_run(
            frequency="monthly",
            last_run_at=base,
            agent_time="09:00",
        )
        assert result == datetime(2023, 2, 28, 9, 0, 0)

    def test_jan31_clamps_to_feb29_leap(self):
        base = datetime(2024, 1, 31, 10, 0, 0)
        result = compute_next_run(
            frequency="monthly",
            last_run_at=base,
            agent_time="09:00",
        )
        assert result == datetime(2024, 2, 29, 9, 0, 0)

    def test_mar31_clamps_to_apr30(self):
        base = datetime(2024, 3, 31, 10, 0, 0)
        result = compute_next_run(
            frequency="monthly",
            last_run_at=base,
            agent_time="09:00",
        )
        assert result == datetime(2024, 4, 30, 9, 0, 0)

    def test_dec_wraps_to_january(self):
        base = datetime(2024, 12, 15, 10, 0, 0)
        result = compute_next_run(
            frequency="monthly",
            last_run_at=base,
            agent_time="14:30",
        )
        assert result == datetime(2025, 1, 15, 14, 30, 0)

    def test_day_fits_no_clamping(self):
        base = datetime(2024, 1, 15, 10, 0, 0)
        result = compute_next_run(
            frequency="monthly",
            last_run_at=base,
            agent_time="09:00",
        )
        assert result == datetime(2024, 2, 15, 9, 0, 0)
