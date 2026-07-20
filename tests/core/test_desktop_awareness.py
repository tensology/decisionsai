"""Desktop awareness: cache-only hot path, hash dedup, size cap, expiry."""

from __future__ import annotations

from unittest.mock import patch

from distr.core import desktop_awareness as da


def setup_function(_fn=None):
    da._reset_cache_for_tests()


def test_inject_is_cache_only_no_sidecar():
    da._set_cache_for_tests(
        line='desktop: Safari — "Inbox"',
        content_hash="abc123",
        captured_at=da.time.time(),
        app="Safari",
        title="Inbox",
        stale=False,
    )
    with patch.object(da, "compact_desktop_snapshot") as mock_compact, patch.object(
        da, "refresh_desktop_awareness_cache"
    ) as mock_refresh:
        block = da.get_desktop_inject_block(mark_injected=True)
        assert "Safari" in block
        block2 = da.get_desktop_inject_block(mark_injected=True)
        assert block2 == "desktop: (unchanged)"
        mock_compact.assert_not_called()
        mock_refresh.assert_not_called()


def test_format_line_size_cap_and_hash_stable():
    line = da._format_line(
        app="Safari",
        title="x" * 200,
        focused="y" * 100,
        ui=[f"[Button {i}]" for i in range(20)],
    )
    assert len(line) <= da.MAX_LINE_CHARS
    assert da._hash_line(line) == da._hash_line(line)


def test_refresh_keeps_prior_cache_on_empty_failure():
    da._set_cache_for_tests(
        line='desktop: Cursor — "plan"',
        content_hash="keepme",
        captured_at=da.time.time(),
        app="Cursor",
        title="plan",
    )
    with patch.object(
        da,
        "compact_desktop_snapshot",
        return_value={
            "line": "",
            "content_hash": "",
            "captured_at": da.time.time(),
            "sidecar_ok": False,
            "app": "",
            "title": "",
            "focused": "",
            "ui": [],
            "stale": False,
        },
    ):
        out = da.refresh_desktop_awareness_cache()
    assert out["line"].startswith("desktop: Cursor")
    assert out["content_hash"] == "keepme"


def test_refresh_overwrites_single_slot():
    da._set_cache_for_tests(
        line='desktop: A — "one"',
        content_hash="h1",
        captured_at=da.time.time() - 10,
        app="A",
        title="one",
    )
    with patch.object(
        da,
        "compact_desktop_snapshot",
        return_value={
            "line": 'desktop: B — "two"',
            "content_hash": "h2",
            "captured_at": da.time.time(),
            "sidecar_ok": False,
            "app": "B",
            "title": "two",
            "focused": "",
            "ui": [],
            "stale": False,
        },
    ):
        out = da.refresh_desktop_awareness_cache()
    assert out["app"] == "B"
    assert out["content_hash"] == "h2"
    assert "A" not in out["line"]


def test_situational_reads_cache_only():
    from distr.core.initiative.situational import build_situational, situational_one_liner

    da._set_cache_for_tests(
        line='desktop: Mail — "Inbox"',
        content_hash="m1",
        captured_at=da.time.time(),
        app="Mail",
        title="Inbox",
        stale=False,
    )
    with patch("distr.core.desktop_awareness.refresh_desktop_awareness_cache") as mock_refresh:
        sit = build_situational(active_project={}, developer_context={})
    assert sit.get("desktop", {}).get("app") == "Mail"
    assert "desktop: Mail" in situational_one_liner(sit)
    mock_refresh.assert_not_called()


def test_dead_cache_is_purged():
    da._set_cache_for_tests(
        line='desktop: Old — "gone"',
        content_hash="dead",
        captured_at=da.time.time() - da.DELETE_AFTER_S - 10,
        app="Old",
        title="gone",
    )
    assert da.purge_dead_desktop_awareness() is True
    snap = da.get_cached_snapshot()
    assert not snap.get("line")
    assert snap.get("content_hash") == ""


def test_compact_snapshot_skips_tier_b_when_not_forced_and_slow_tier_a():
    with patch.object(da, "_tier_a_frontmost_and_title", return_value=("Safari", "Inbox")) as tier_a, patch.object(
        da, "_tier_b_interactive_labels"
    ) as tier_b:
        # Make tier A appear slow by patching monotonic around the call
        times = iter([100.0, 100.0 + da.TIER_A_BUDGET_S + 0.05])

        def mono():
            return next(times, 100.0 + da.TIER_A_BUDGET_S + 0.05)

        with patch.object(da.time, "monotonic", side_effect=mono):
            snap = da.compact_desktop_snapshot(force_tier_b=False)
        tier_a.assert_called_once()
        tier_b.assert_not_called()
        assert "Safari" in snap["line"]


def test_force_tier_b_calls_sidecar_path():
    with patch.object(da, "_tier_a_frontmost_and_title", return_value=("Safari", "Inbox")), patch.object(
        da, "_tier_b_interactive_labels", return_value=("Search", ["[Button Compose]"], True)
    ) as tier_b:
        snap = da.compact_desktop_snapshot(force_tier_b=True)
    tier_b.assert_called_once()
    assert "Compose" in snap["line"]
    assert snap["sidecar_ok"] is True
