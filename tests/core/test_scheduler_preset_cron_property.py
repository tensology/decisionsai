# Feature: workflow-step-runner-unification, Property 8: Schedule preset-to-cron and next-run computation
"""
Property-based test verifying that:
1. For any preset schedule name in {hourly, daily, weekly} with optional
   schedule_time and schedule_days, schedule_to_cron() returns a valid cron
   expression.
2. For any valid cron expression and reference timestamp,
   _next_run_from_cron() returns a datetime strictly after the reference
   timestamp that satisfies the cron pattern.

**Validates: Requirements 5.3, 5.4**
"""

from datetime import datetime, timedelta
from unittest.mock import patch

import pytest

pytest.importorskip("croniter")
from croniter import croniter
from hypothesis import given, settings, assume
from hypothesis import strategies as st

from distr.core.workflow.scheduler import schedule_to_cron, _next_run_from_cron


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

_preset_strategy = st.sampled_from(["hourly", "daily", "weekly"])

_schedule_time_strategy = st.one_of(
    st.none(),
    st.builds(
        lambda h, m: f"{h:02d}:{m:02d}",
        h=st.integers(min_value=0, max_value=23),
        m=st.integers(min_value=0, max_value=59),
    ),
)

# Cron weekday values: 0-6 (Sun-Sat) or comma-separated subsets
_schedule_days_strategy = st.one_of(
    st.none(),
    st.lists(
        st.integers(min_value=0, max_value=6), min_size=1, max_size=7, unique=True
    ).map(lambda days: ",".join(str(d) for d in sorted(days))),
)

# Reference timestamps spanning a reasonable range
_reference_dt_strategy = st.builds(
    datetime,
    year=st.just(2025),
    month=st.integers(min_value=1, max_value=12),
    day=st.integers(min_value=1, max_value=28),
    hour=st.integers(min_value=0, max_value=23),
    minute=st.integers(min_value=0, max_value=59),
    second=st.just(0),
)

# Valid cron expressions for the next-run sub-property
_valid_cron_strategy = st.one_of(
    # Hourly
    st.just("0 * * * *"),
    # Daily at random hour:minute
    st.builds(
        lambda m, h: f"{m} {h} * * *",
        m=st.integers(min_value=0, max_value=59),
        h=st.integers(min_value=0, max_value=23),
    ),
    # Weekly at random hour:minute on random day(s)
    st.builds(
        lambda m, h, d: f"{m} {h} * * {d}",
        m=st.integers(min_value=0, max_value=59),
        h=st.integers(min_value=0, max_value=23),
        d=st.lists(
            st.integers(min_value=0, max_value=6), min_size=1, max_size=7, unique=True
        ).map(lambda days: ",".join(str(x) for x in sorted(days))),
    ),
)


# ---------------------------------------------------------------------------
# Property tests
# ---------------------------------------------------------------------------


class TestSchedulePresetToCron:
    """Property 8: Schedule preset-to-cron and next-run computation."""

    @settings(max_examples=100, deadline=None)
    @given(
        preset=_preset_strategy,
        schedule_time=_schedule_time_strategy,
        schedule_days=_schedule_days_strategy,
    )
    def test_preset_produces_valid_cron(self, preset, schedule_time, schedule_days):
        """**Validates: Requirements 5.3**

        For any preset schedule name in {hourly, daily, weekly} with optional
        schedule_time and schedule_days, schedule_to_cron() SHALL return a
        valid cron expression.
        """
        result = schedule_to_cron(
            preset,
            schedule_time=schedule_time,
            schedule_days=schedule_days,
        )

        # Must return a non-None string
        assert result is not None, (
            f"schedule_to_cron({preset!r}, schedule_time={schedule_time!r}, "
            f"schedule_days={schedule_days!r}) returned None"
        )
        assert isinstance(result, str) and result.strip(), (
            f"schedule_to_cron returned empty/non-string: {result!r}"
        )

        # Must be parseable by croniter (i.e. a valid cron expression)
        assert croniter.is_valid(result), (
            f"schedule_to_cron({preset!r}, schedule_time={schedule_time!r}, "
            f"schedule_days={schedule_days!r}) returned invalid cron: {result!r}"
        )

    @settings(max_examples=100, deadline=None)
    @given(
        cron_expr=_valid_cron_strategy,
        ref_dt=_reference_dt_strategy,
    )
    def test_next_run_strictly_after_reference(self, cron_expr, ref_dt):
        """**Validates: Requirements 5.4**

        For any valid cron expression and reference timestamp,
        _next_run_from_cron() SHALL return a datetime strictly after the
        reference timestamp that satisfies the cron pattern.
        """
        # Patch _utc_offset to zero so local == UTC, avoiding timezone
        # complications in the test while still exercising the core logic.
        with patch(
            "distr.core.workflow.scheduler._utc_offset",
            return_value=timedelta(0),
        ):
            next_run = _next_run_from_cron(
                cron_expr, from_dt=ref_dt, allow_current_minute=False
            )

        assert next_run is not None, (
            f"_next_run_from_cron({cron_expr!r}, {ref_dt!r}) returned None"
        )

        # Must be strictly after the reference timestamp
        assert next_run > ref_dt, (
            f"_next_run_from_cron({cron_expr!r}, {ref_dt!r}) returned "
            f"{next_run!r} which is not strictly after the reference"
        )

        # The returned datetime must satisfy the cron pattern
        assert croniter.match(cron_expr, next_run), (
            f"_next_run_from_cron({cron_expr!r}, {ref_dt!r}) returned "
            f"{next_run!r} which does not match the cron pattern"
        )
