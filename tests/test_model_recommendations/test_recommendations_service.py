"""
Property-based tests for the model recommendations service.

Properties 1-5 from the design doc — updated for the two-lane
paid/free schema.
"""
import json
import os
import tempfile
from datetime import datetime, timezone, timedelta
from unittest.mock import patch

from hypothesis import given, settings, assume
from hypothesis import strategies as st


# ── Strategies ──────────────────────────────────────────────────

def _lane_strategy():
    """Generate a valid paid or free lane entry (or None)."""
    lane = st.fixed_dictionaries({
        "model_id": st.text(min_size=1, max_size=60, alphabet=st.characters(whitelist_categories=("L", "N", "P"))),
        "model_name": st.text(min_size=1, max_size=80),
        "description": st.text(min_size=1, max_size=200),
        "released": st.from_regex(r"20[0-9]{2}-[01][0-9]", fullmatch=True),
        "context_window": st.integers(min_value=0, max_value=2_000_000),
        "pricing": st.fixed_dictionaries({
            "input": st.floats(min_value=0, max_value=500, allow_nan=False, allow_infinity=False),
            "output": st.floats(min_value=0, max_value=500, allow_nan=False, allow_infinity=False),
            "per_prompt_est": st.from_regex(r"~?\$[0-9]+\.[0-9]{2,4}", fullmatch=True),
        }),
        "quality": st.fixed_dictionaries({
            "overall": st.integers(min_value=1, max_value=10),
            "speed": st.integers(min_value=1, max_value=10),
            "reasoning": st.integers(min_value=1, max_value=10),
            "cost_efficiency": st.integers(min_value=1, max_value=10),
        }),
        "sources": st.lists(
            st.fixed_dictionaries({
                "title": st.text(min_size=1, max_size=60),
                "url": st.text(min_size=5, max_size=120),
            }),
            min_size=0, max_size=3,
        ),
    })
    return st.one_of(st.none(), lane)


def _category_strategy():
    """Generate a valid two-lane category entry."""
    return st.fixed_dictionaries({
        "paid": _lane_strategy(),
        "free": _lane_strategy(),
    })


def _provider_strategy():
    """Generate a valid provider entry with the four required categories."""
    return st.fixed_dictionaries({
        "display_name": st.text(min_size=1, max_size=40),
        "categories": st.fixed_dictionaries({
            "tool_calling": _category_strategy(),
            "coding": _category_strategy(),
            "vision": _category_strategy(),
            "image_generation": _category_strategy(),
        }),
    })


def _recommendations_strategy():
    """Generate a full recommendations dict with 1-6 providers."""
    provider_ids = ["ollama", "openai", "anthropic", "groq", "openrouter", "kilocode"]
    return st.fixed_dictionaries({
        "last_updated": st.just(datetime.now(timezone.utc).isoformat()),
        "generated_by": st.just("test"),
        "providers": st.dictionaries(
            keys=st.sampled_from(provider_ids),
            values=_provider_strategy(),
            min_size=1,
            max_size=6,
        ),
    })


# ── Property 1: JSON schema validation ─────────────────────────

class TestSchemaValidation:
    """Every provider must have display_name + categories with paid/free lanes."""

    @given(data=_recommendations_strategy())
    @settings(max_examples=30, deadline=None)
    def test_all_required_fields_present(self, data):
        providers = data["providers"]
        for pid, pdata in providers.items():
            assert "display_name" in pdata and pdata["display_name"]
            assert "categories" in pdata
            for cat_key in ("tool_calling", "coding", "vision", "image_generation"):
                cat = pdata["categories"][cat_key]
                assert "paid" in cat
                assert "free" in cat
                for lane_key in ("paid", "free"):
                    lane = cat[lane_key]
                    if lane is None:
                        continue
                    assert lane["model_id"] and len(lane["model_id"]) > 0
                    assert lane["model_name"] and len(lane["model_name"]) > 0
                    assert lane["description"] and len(lane["description"]) > 0
                    assert "pricing" in lane
                    assert "quality" in lane
                    q = lane["quality"]
                    for k in ("overall", "speed", "reasoning", "cost_efficiency"):
                        assert 1 <= q[k] <= 10


# ── Property 2: Staleness detection ────────────────────────────

class TestStalenessDetection:
    """is_stale() returns True iff >14 days old or file missing."""

    @given(days_old=st.integers(min_value=0, max_value=60))
    @settings(max_examples=40, deadline=None)
    def test_staleness_by_age(self, days_old):
        from distr.core.services.model_recommendations import is_stale, STALE_DAYS

        ts = (datetime.now(timezone.utc) - timedelta(days=days_old)).isoformat()
        data = json.dumps({"last_updated": ts, "providers": {}, "generated_by": "test"})

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            f.write(data)
            tmp = f.name

        try:
            with patch("distr.core.services.model_recommendations.RECOMMENDATIONS_FILE") as mock_path:
                mock_path.exists.return_value = True
                mock_path.read_text.return_value = data
                result = is_stale()
                assert result == (days_old >= STALE_DAYS)
        finally:
            os.unlink(tmp)

    def test_missing_file_is_stale(self):
        from distr.core.services.model_recommendations import is_stale

        with patch("distr.core.services.model_recommendations.RECOMMENDATIONS_FILE") as mock_path:
            mock_path.exists.return_value = False
            assert is_stale() is True


# ── Property 3: Provider filter returns correct subset ──────────

class TestProviderFilter:
    """Filtered response contains only the requested provider."""

    @given(data=_recommendations_strategy())
    @settings(max_examples=30, deadline=None)
    def test_filter_returns_single_provider(self, data):
        from distr.core.services.model_recommendations import load_recommendations

        providers = data["providers"]
        assume(len(providers) > 0)
        target = list(providers.keys())[0]

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(data, f)
            tmp = f.name

        try:
            from pathlib import Path
            with patch("distr.core.services.model_recommendations.RECOMMENDATIONS_FILE", Path(tmp)):
                result = load_recommendations(provider=target)
                assert target in result["providers"]
                assert len(result["providers"]) == 1
                assert result["last_updated"] == data["last_updated"]
        finally:
            os.unlink(tmp)

    @given(data=_recommendations_strategy())
    @settings(max_examples=20, deadline=None)
    def test_filter_nonexistent_returns_empty(self, data):
        from distr.core.services.model_recommendations import load_recommendations

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(data, f)
            tmp = f.name

        try:
            from pathlib import Path
            with patch("distr.core.services.model_recommendations.RECOMMENDATIONS_FILE", Path(tmp)):
                result = load_recommendations(provider="nonexistent_provider_xyz")
                assert len(result["providers"]) == 0
                assert result["last_updated"] == data["last_updated"]
        finally:
            os.unlink(tmp)


# ── Property 4: Atomic file write round-trip ────────────────────

class TestAtomicWrite:
    """Write via _write_recommendations, read back, verify equality."""

    @given(data=_recommendations_strategy())
    @settings(max_examples=30, deadline=None)
    def test_write_read_round_trip(self, data):
        from distr.core.services.model_recommendations import _write_recommendations

        with tempfile.TemporaryDirectory() as tmpdir:
            target = os.path.join(tmpdir, "recs.json")
            from pathlib import Path
            with patch("distr.core.services.model_recommendations.RECOMMENDATIONS_FILE", Path(target)):
                _write_recommendations(data)
                with open(target, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                assert loaded == data


# ── Property 5: Concurrent refresh prevention ──────────────────

class TestConcurrentRefreshPrevention:
    """If _refresh_running is True, refresh_recommendations returns immediately."""

    def test_no_double_refresh(self):
        import distr.core.services.model_recommendations as mod

        original = mod._refresh_running
        try:
            mod._refresh_running = True
            with patch.object(mod, "_do_refresh") as mock_do:
                mod.refresh_recommendations()
                mock_do.assert_not_called()
        finally:
            mod._refresh_running = original

    def test_flag_reset_after_refresh(self):
        import distr.core.services.model_recommendations as mod

        original = mod._refresh_running
        try:
            mod._refresh_running = False
            with patch.object(mod, "_do_refresh"):
                mod.refresh_recommendations()
                assert mod._refresh_running is False
        finally:
            mod._refresh_running = original
