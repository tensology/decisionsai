# Feature: kanban-agent-workflow, Property 3: Frequency schedule computation
"""
Property 3: Frequency schedule computation

*For any* valid `agent_frequency` value ('daily', 'weekly', 'fortnightly', 'monthly'),
a given `last_run_at` datetime, and a configured `agent_time`, the computed
`next_run_at` should be:
  - daily: next day at agent_time
  - weekly: 7 days later at agent_time
  - fortnightly: exactly 14 days later at agent_time
  - monthly: same day next month at agent_time

**Validates: Requirements 1.6, 1.7**
"""
import calendar
from datetime import datetime, timedelta

from hypothesis import given, settings, assume
from hypothesis import strategies as st

from distr.core.kanban.scheduler import compute_next_run


# ── Strategies ──

hours = st.integers(min_value=0, max_value=23)
minutes = st.integers(min_value=0, max_value=59)

agent_time_st = st.builds(
    lambda h, m: f"{h:02d}:{m:02d}",
    hours,
    minutes,
)

# Reasonable datetime range to avoid overflow issues
last_run_st = st.datetimes(
    min_value=datetime(2000, 1, 1),
    max_value=datetime(2099, 12, 30),
)

frequency_st = st.sampled_from(["daily", "weekly", "fortnightly", "monthly"])


class TestFrequencyScheduleComputation:
    """Property 3: Frequency schedule computation."""

    @given(
        frequency=frequency_st,
        last_run_at=last_run_st,
        agent_time=agent_time_st,
    )
    @settings(max_examples=50, deadline=None)
    def test_schedule_computation(self, frequency, last_run_at, agent_time):
        """Validates: Requirements 1.6, 1.7"""
        result = compute_next_run(
            frequency=frequency,
            last_run_at=last_run_at,
            agent_time=agent_time,
        )
        assert result is not None

        hour, minute = map(int, agent_time.split(":"))

        # Time component must match agent_time
        assert result.hour == hour
        assert result.minute == minute
        assert result.second == 0
        assert result.microsecond == 0

        if frequency == "daily":
            expected_date = last_run_at + timedelta(days=1)
            assert result.year == expected_date.year
            assert result.month == expected_date.month
            assert result.day == expected_date.day

        elif frequency == "weekly":
            expected_date = last_run_at + timedelta(days=7)
            assert result.year == expected_date.year
            assert result.month == expected_date.month
            assert result.day == expected_date.day

        elif frequency == "fortnightly":
            expected_date = last_run_at + timedelta(days=14)
            assert result.year == expected_date.year
            assert result.month == expected_date.month
            assert result.day == expected_date.day

        elif frequency == "monthly":
            # Next month, same day (clamped to max day)
            exp_year = last_run_at.year
            exp_month = last_run_at.month + 1
            if exp_month > 12:
                exp_month = 1
                exp_year += 1
            max_day = calendar.monthrange(exp_year, exp_month)[1]
            exp_day = min(last_run_at.day, max_day)
            assert result.year == exp_year
            assert result.month == exp_month
            assert result.day == exp_day

    @given(
        frequency=frequency_st,
        created_date=last_run_st,
        agent_time=agent_time_st,
    )
    @settings(max_examples=50, deadline=None)
    def test_fallback_to_created_date(self, frequency, created_date, agent_time):
        """When last_run_at is None, created_date is used as baseline."""
        result = compute_next_run(
            frequency=frequency,
            last_run_at=None,
            agent_time=agent_time,
            created_date=created_date,
        )
        assert result is not None

        # Verify it computes the same as using created_date as last_run_at
        expected = compute_next_run(
            frequency=frequency,
            last_run_at=created_date,
            agent_time=agent_time,
        )
        assert result == expected

    def test_no_baseline_returns_none(self):
        """When both last_run_at and created_date are None, returns None."""
        result = compute_next_run(
            frequency="daily",
            last_run_at=None,
            agent_time="09:00",
            created_date=None,
        )
        assert result is None

    def test_invalid_frequency_returns_none(self):
        """Unknown frequency returns None."""
        result = compute_next_run(
            frequency="biweekly",
            last_run_at=datetime(2024, 1, 1),
            agent_time="09:00",
        )
        assert result is None

    def test_fortnightly_is_exactly_14_days(self):
        """Fortnightly is exactly 14 days from baseline."""
        base = datetime(2024, 3, 1, 10, 30, 0)
        result = compute_next_run(
            frequency="fortnightly",
            last_run_at=base,
            agent_time="08:00",
        )
        assert result == datetime(2024, 3, 15, 8, 0, 0)
