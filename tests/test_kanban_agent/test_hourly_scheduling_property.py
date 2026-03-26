# Feature: kanban-cli-settings-restructure, Property 1: Hourly scheduling returns the earliest valid next hour
"""
Property 1: Hourly scheduling returns the earliest valid next hour

For any last_run_at datetime and for any non-empty set of valid hours
(integers in [0, 23]), compute_next_run("hourly", last_run_at, agent_time,
created_date, agent_hours) should return a datetime whose hour is in the
provided set, is strictly after last_run_at, and is the earliest such hour
(either later the same day, or the first matching hour the next day if all
same-day hours have passed). If agent_hours is empty (after filtering
out-of-range values), the result should be None.

**Validates: Requirements 1.2, 1.3, 1.6**
"""
from datetime import datetime, timedelta

from hypothesis import given, settings, assume
from hypothesis import strategies as st

from distr.core.kanban.scheduler import compute_next_run


# ── Strategies ──

# Reasonable datetime range to avoid overflow issues
last_run_st = st.datetimes(
    min_value=datetime(2000, 1, 1),
    max_value=datetime(2099, 12, 30),
)

# Non-empty subsets of valid hours [0, 23]
valid_hours_st = st.lists(
    st.integers(min_value=0, max_value=23),
    min_size=1,
    max_size=24,
)

# Lists that may include out-of-range values (for filtering tests)
mixed_hours_st = st.lists(
    st.integers(min_value=-10, max_value=50),
    min_size=0,
    max_size=30,
)


class TestHourlySchedulingProperty:
    """Property 1: Hourly scheduling returns the earliest valid next hour."""

    @given(
        last_run_at=last_run_st,
        agent_hours=valid_hours_st,
    )
    @settings(max_examples=100, deadline=None)
    def test_hourly_returns_earliest_valid_next_hour(self, last_run_at, agent_hours):
        """
        **Validates: Requirements 1.2, 1.3, 1.6**

        For any non-empty set of valid hours, the result hour must be in the
        provided set, the result must be strictly after last_run_at, and it
        must be the earliest such candidate.
        """
        result = compute_next_run(
            frequency="hourly",
            last_run_at=last_run_at,
            agent_time="09:00",  # agent_time is unused for hourly
            agent_hours=agent_hours,
        )

        # Valid hours exist, so result must not be None
        valid = sorted(set(h for h in agent_hours if 0 <= h <= 23))
        assert len(valid) > 0  # precondition from strategy
        assert result is not None

        # 1) Result hour must be in the provided valid set
        assert result.hour in valid

        # 2) Result must be strictly after last_run_at
        assert result > last_run_at

        # 3) Minutes, seconds, microseconds must be zeroed
        assert result.minute == 0
        assert result.second == 0
        assert result.microsecond == 0

        # 4) Result must be the earliest valid candidate
        # Build all candidates: same-day hours after last_run_at, then next-day hours
        candidates = []
        for h in valid:
            same_day = last_run_at.replace(hour=h, minute=0, second=0, microsecond=0)
            if same_day > last_run_at:
                candidates.append(same_day)

        if not candidates:
            # All same-day hours passed; earliest must be first valid hour next day
            next_day = last_run_at + timedelta(days=1)
            expected = next_day.replace(hour=valid[0], minute=0, second=0, microsecond=0)
            assert result == expected
        else:
            # Earliest same-day candidate
            expected = min(candidates)
            assert result == expected

    @given(
        last_run_at=last_run_st,
        agent_hours=mixed_hours_st,
    )
    @settings(max_examples=100, deadline=None)
    def test_hourly_empty_after_filtering_returns_none(self, last_run_at, agent_hours):
        """
        **Validates: Requirements 1.3, 1.6**

        If agent_hours is empty or contains only out-of-range values,
        the result should be None.
        """
        valid = [h for h in agent_hours if 0 <= h <= 23]
        assume(len(valid) == 0)

        result = compute_next_run(
            frequency="hourly",
            last_run_at=last_run_at,
            agent_time="09:00",
            agent_hours=agent_hours,
        )

        assert result is None

    @given(
        last_run_at=last_run_st,
        agent_hours=mixed_hours_st,
    )
    @settings(max_examples=100, deadline=None)
    def test_hourly_filters_out_of_range_values(self, last_run_at, agent_hours):
        """
        **Validates: Requirements 1.6**

        Out-of-range hours are filtered; the result (if not None) must have
        its hour within [0, 23] and in the valid subset of agent_hours.
        """
        valid = sorted(set(h for h in agent_hours if 0 <= h <= 23))

        result = compute_next_run(
            frequency="hourly",
            last_run_at=last_run_at,
            agent_time="09:00",
            agent_hours=agent_hours,
        )

        if not valid:
            assert result is None
        else:
            assert result is not None
            assert result.hour in valid
            assert result > last_run_at
