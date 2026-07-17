from __future__ import annotations

import sys

from scripts.run_isolated_workflow_e2e import (
    CANONICAL_TARGET,
    SPOTIFY_TARGET,
    _profile_contract,
    _pytest_command,
)


def test_default_profile_uses_canonical_browser_contract() -> None:
    assert _profile_contract("until-green") == ("e2e_playwright", CANONICAL_TARGET)
    assert _pytest_command("until-green", []) == [
        sys.executable,
        "-m",
        "pytest",
        "-m",
        "e2e_playwright",
        CANONICAL_TARGET,
        "-q",
    ]


def test_spotify_aliases_use_program_contract_and_preserve_pytest_arguments() -> None:
    for profile in ("spotify", "dogfood"):
        assert _profile_contract(profile) == ("e2e", SPOTIFY_TARGET)
        assert _pytest_command(profile, ["--junitxml=proof.xml", "-x"])[-2:] == [
            "--junitxml=proof.xml",
            "-x",
        ]
