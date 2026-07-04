from __future__ import annotations

from pathlib import Path

import pytest

from scripts import workflow_ticket_loop_e2e as harness


def test_backend_matrix_all_ready_skips_unavailable_backends() -> None:
    statuses = {
        "pi": {"ready": True, "state": "ready"},
        "cursor": {"ready": False, "state": "auth_required"},
        "codex": {"ready": True, "state": "ready"},
    }

    matrix = harness.select_backend_matrix(
        "all-ready",
        statuses=statuses,
        registered_backend_ids=["pi", "cursor", "codex"],
    )

    assert matrix.selected == ["pi", "codex"]
    assert matrix.skipped == {
        "cursor": {"ready": False, "state": "auth_required"},
    }


def test_backend_matrix_accepts_comma_separated_subset() -> None:
    statuses = {
        "pi": {"ready": True, "state": "ready"},
        "cursor": {"ready": True, "state": "ready"},
        "codex": {"ready": True, "state": "ready"},
    }

    matrix = harness.select_backend_matrix(
        "codex,cursor",
        statuses=statuses,
        registered_backend_ids=["pi", "cursor", "codex"],
    )

    assert matrix.selected == ["codex", "cursor"]
    assert matrix.skipped == {"pi": {"ready": True, "state": "ready"}}


def test_backend_matrix_all_strict_fails_when_backend_unavailable() -> None:
    statuses = {
        "pi": {"ready": True, "state": "ready"},
        "cursor": {"ready": False, "state": "missing"},
    }

    with pytest.raises(AssertionError, match="not ready"):
        harness.select_backend_matrix(
            "all",
            statuses=statuses,
            registered_backend_ids=["pi", "cursor"],
            fail_on_unavailable=True,
        )


def test_spotify_ticket_specs_cover_priority_and_complexity_mix() -> None:
    specs = harness.build_spotify_ticket_specs()

    assert [spec.priority for spec in specs] == ["high", "medium", "high", "critical"]
    assert [spec.complexity for spec in specs] == ["high", "medium", "medium", "high"]
    assert [spec.sequence for spec in specs] == [1, 2, 3, 4]
    assert all(spec.acceptance for spec in specs)


def test_model_resolution_uses_backend_and_complexity_policy() -> None:
    policy = harness.build_spotify_board_policy(
        backend_id="codex",
        model_map={
            "codex": {
                "medium": "gpt-5-codex-standard",
                "high": "gpt-5-codex-high",
            }
        },
    )

    resolved = harness.resolve_model_for_ticket(
        backend_id="codex",
        complexity="high",
        priority="critical",
        board_policy=policy,
    )

    assert resolved.model == "gpt-5-codex-high"
    assert resolved.reason == "complexity policy"


def test_model_resolution_falls_back_to_auto_with_reason() -> None:
    resolved = harness.resolve_model_for_ticket(
        backend_id="hermes_agent",
        complexity="medium",
        priority="medium",
        board_policy={},
    )

    assert resolved.model == "auto"
    assert resolved.reason == "backend default model"


def test_elapsed_time_formats_to_ticket_time_spent() -> None:
    assert harness.format_elapsed_time_spent(1) == "1m"
    assert harness.format_elapsed_time_spent(61) == "2m"
    assert harness.format_elapsed_time_spent(60 * 60 + 1) == "1h 1m"


def test_disposable_project_path_is_exactly_guarded(tmp_path: Path) -> None:
    project_dir = harness.disposable_spotify_project_dir(
        "codex",
        "20260614-120000",
        development_root=tmp_path,
    )

    assert project_dir == tmp_path / "spotify-remake-e2e-codex-20260614-120000"
    assert harness.assert_safe_disposable_spotify_project_dir(
        project_dir,
        development_root=tmp_path,
    ) == project_dir


def test_disposable_project_path_rejects_non_matching_directory(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="Refusing"):
        harness.assert_safe_disposable_spotify_project_dir(
            tmp_path / "spotify-prod",
            development_root=tmp_path,
        )


def test_live_spotify_build_parser_defaults_to_all_ready() -> None:
    parser = harness.build_arg_parser()

    args = parser.parse_args(["live-spotify-build", "--dry-run"])

    assert args.command == "live-spotify-build"
    assert args.backend == "all-ready"
    assert args.dry_run is True
    assert args.live is False
