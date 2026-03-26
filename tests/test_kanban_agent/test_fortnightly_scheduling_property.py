# Feature: kanban-cli-settings-restructure, Property 2: Fortnightly scheduling with day selection returns a matching weekday within the 14-day window
"""
Property 2: Fortnightly scheduling with day selection returns a matching
weekday within the 14-day window

For any last_run_at datetime, for any non-empty set of weekday indices
(integers in [0, 6]), and for any valid agent_time, compute_next_run(
"fortnightly", last_run_at, agent_time, created_date, agent_hours=None,
agent_days=days) should return a datetime that: (a) falls within the
14-day window after last_run_at, (b) has a weekday matching one of the
selected days, and (c) is the earliest such match. When agent_days is
empty, the result should be exactly last_run_at + 14 days at the
configured time.

**Validates: Requirements 2.2, 2.3**
"""
from datetime import datetime, timedelta

from hypothesis import given, settings, assume
from hypothesis import strategies as st

from distr.core.kanban.scheduler import compute_next_run, _to_python_weekday


# ── Strategies ──

# Reasonable datetime range to avoid overflow issues
last_run_st = st.datetimes(
    min_value=datetime(2000, 1, 1),
    max_value=datetime(2099, 12, 15),
)

# Valid agent_time strings in HH:MM format
agent_time_st = st.builds(
    lambda h, m: f"{h:02d}:{m:02d}",
    st.integers(min_value=0, max_value=23),
    st.integers(min_value=0, max_value=59),
)

# Non-empty subsets of valid weekday indices (0=Sun..6=Sat)
non_empty_days_st = st.lists(
    st.integers(min_value=0, max_value=6),
    min_size=1,
    max_size=7,
)


class TestFortnightlySchedulingProperty:
    """Property 2: Fortnightly scheduling with day selection returns a matching weekday within the 14-day window."""

    @given(
        last_run_at=last_run_st,
        agent_days=non_empty_days_st,
        agent_time=agent_time_st,
    )
    @settings(max_examples=100, deadline=None)
    def test_fortnightly_with_days_returns_matching_weekday_in_window(
        self, last_run_at, agent_days, agent_time
    ):
        """
        **Validates: Requirements 2.2, 2.3**

        For any non-empty set of weekday indices, the result must:
        (a) fall within the 14-day window after last_run_at,
        (b) have a weekday matching one of the selected days, and
        (c) be the earliest such match.
        """
        result = compute_next_run(
            frequency="fortnightly",
            last_run_at=last_run_at,
            agent_time=agent_time,
            agent_days=agent_days,
        )

        assert result is not None

        # Parse agent_time for expected hour/minute
        parts = agent_time.strip().split(":")
        expected_hour = int(parts[0])
        expected_minute = int(parts[1])

        # Convert agent_days (0=Sun..6=Sat) to Python weekdays (0=Mon..6=Sun)
        python_weekdays = set(_to_python_weekday(d) for d in agent_days if 0 <= d <= 6)

        if python_weekdays:
            # (a) Result must be within the 14-day window after last_run_at
            # The window is day 1 through day 14 after baseline
            window_start = last_run_at + timedelta(days=1)
            window_end = last_run_at + timedelta(days=14)
            result_date = result.replace(hour=0, minute=0, second=0, microsecond=0)
            window_start_date = window_start.replace(hour=0, minute=0, second=0, microsecond=0)
            window_end_date = window_end.replace(hour=0, minute=0, second=0, microsecond=0)
            assert window_start_date <= result_date <= window_end_date

            # (b) Result weekday must match one of the selected days
            assert result.weekday() in python_weekdays

            # (c) Result must be the earliest such match
            # Build all candidates in the window and verify result is the minimum
            candidates = []
            for offset in range(1, 15):
                candidate_date = last_run_at + timedelta(days=offset)
                if candidate_date.weekday() in python_weekdays:
                    candidates.append(
                        candidate_date.replace(
                            hour=expected_hour,
                            minute=expected_minute,
                            second=0,
                            microsecond=0,
                        )
                    )
            assert len(candidates) > 0
            expected = min(candidates)
            assert result == expected

        # Verify time components match agent_time
        assert result.hour == expected_hour
        assert result.minute == expected_minute
        assert result.second == 0
        assert result.microsecond == 0

    @given(
        last_run_at=last_run_st,
        agent_time=agent_time_st,
    )
    @settings(max_examples=100, deadline=None)
    def test_fortnightly_empty_days_falls_back_to_plus_14(
        self, last_run_at, agent_time
    ):
        """
        **Validates: Requirements 2.3**

        When agent_days is empty, the result should be exactly
        last_run_at + 14 days at the configured time.
        """
        result = compute_next_run(
            frequency="fortnightly",
            last_run_at=last_run_at,
            agent_time=agent_time,
            agent_days=[],
        )

        parts = agent_time.strip().split(":")
        expected_hour = int(parts[0])
        expected_minute = int(parts[1])

        expected = (last_run_at + timedelta(days=14)).replace(
            hour=expected_hour,
            minute=expected_minute,
            second=0,
            microsecond=0,
        )

        assert result is not None
        assert result == expected

    @given(
        last_run_at=last_run_st,
        agent_time=agent_time_st,
    )
    @settings(max_examples=100, deadline=None)
    def test_fortnightly_no_days_arg_falls_back_to_plus_14(
        self, last_run_at, agent_time
    ):
        """
        **Validates: Requirements 2.3**

        When agent_days is None (not provided), the result should be exactly
        last_run_at + 14 days at the configured time.
        """
        result = compute_next_run(
            frequency="fortnightly",
            last_run_at=last_run_at,
            agent_time=agent_time,
            agent_days=None,
        )

        parts = agent_time.strip().split(":")
        expected_hour = int(parts[0])
        expected_minute = int(parts[1])

        expected = (last_run_at + timedelta(days=14)).replace(
            hour=expected_hour,
            minute=expected_minute,
            second=0,
            microsecond=0,
        )

        assert result is not None
        assert result == expected
