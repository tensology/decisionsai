# Feature: oracle-skins-system, Property 7: Skin listing returns only valid skins
# Validates: Requirements 11.1, 11.7, 8.3, 12.9
"""Property-based test: for any set of avatar folders where some contain valid
skin.json files and some contain invalid or missing configs, discover_skins()
returns exactly the skins with valid configurations, excludes all others, and
oracle-type skins always appear first in the list."""

import json
import tempfile
from pathlib import Path

from hypothesis import given, settings
from hypothesis import strategies as st

from distr.core.skin_discovery import discover_skins


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

_safe_name = st.text(
    alphabet=st.sampled_from("abcdefghijklmnopqrstuvwxyz0123456789"),
    min_size=1,
    max_size=12,
)

_animation_name = st.text(
    alphabet=st.sampled_from("abcdefghijklmnopqrstuvwxyz0123456789-_."),
    min_size=1,
    max_size=20,
).filter(lambda s: s.strip() != "")


def _valid_oracle_config(name: str) -> dict:
    """Return a valid oracle skin config dict."""
    return {
        "type": "oracle",
        "name": name,
        "rendering": {"shape": "round", "border": True, "shadow": True, "glow_on_hold": True},
        "events": {
            "idle": {
                "animation": "0.gif",
                "show_player": False,
                "show_chat_bubble": False,
                "glow": False,
                "glow_color": [0, 0, 0],
                "glow_speed": 1000,
                "glow_style": "breathing",
                "tray_icon": "default",
            }
        },
    }


def _valid_avatar_config(name: str) -> dict:
    """Return a valid avatar skin config dict."""
    return {
        "type": "avatar",
        "name": name,
        "rendering": {"shape": "square", "border": False, "shadow": False, "glow_on_hold": False},
        "events": {
            "idle": {
                "animation": "idle.webm",
                "show_player": False,
                "show_chat_bubble": False,
                "glow": False,
                "glow_color": [0, 0, 0],
                "glow_speed": 1000,
                "glow_style": "breathing",
                "tray_icon": "default",
            }
        },
    }


@st.composite
def invalid_skin_json_strategy(draw):
    """Generate an invalid skin.json content string (parseable JSON but fails
    validation or parse)."""
    kind = draw(st.sampled_from([
        "not_json",
        "missing_type",
        "missing_name",
        "missing_rendering",
        "missing_events",
        "missing_idle",
        "wrong_rendering_for_type",
        "empty_name",
    ]))

    if kind == "not_json":
        return "NOT VALID JSON {{{{"

    base = _valid_avatar_config("Bad")

    if kind == "missing_type":
        del base["type"]
    elif kind == "missing_name":
        del base["name"]
    elif kind == "missing_rendering":
        del base["rendering"]
    elif kind == "missing_events":
        del base["events"]
    elif kind == "missing_idle":
        base["events"] = {"thinking": base["events"]["idle"]}
    elif kind == "wrong_rendering_for_type":
        # oracle type with avatar rendering → validation failure
        base["type"] = "oracle"
        # rendering stays as square/false/false/false → invalid for oracle
    elif kind == "empty_name":
        base["name"] = ""

    return json.dumps(base)


# Each folder entry: (folder_name, category)
# category is one of: "valid_oracle", "valid_avatar", "invalid", "missing"

@st.composite
def folder_set_strategy(draw):
    """Generate a list of folder descriptors for the test.

    Each descriptor is a tuple: (folder_name, category, config_or_none)
    where category is 'valid_oracle', 'valid_avatar', 'invalid', or 'missing'.
    """
    # Generate between 1 and 8 folders with unique names
    num_folders = draw(st.integers(min_value=1, max_value=8))
    folder_names = draw(
        st.lists(
            _safe_name,
            min_size=num_folders,
            max_size=num_folders,
            unique=True,
        )
    )

    folders = []
    for fname in folder_names:
        category = draw(st.sampled_from([
            "valid_oracle", "valid_avatar", "invalid", "missing"
        ]))

        if category == "valid_oracle":
            display_name = draw(_safe_name.filter(lambda s: len(s) > 0))
            config_str = json.dumps(_valid_oracle_config(display_name))
            folders.append((fname, category, config_str, display_name))
        elif category == "valid_avatar":
            display_name = draw(_safe_name.filter(lambda s: len(s) > 0))
            config_str = json.dumps(_valid_avatar_config(display_name))
            folders.append((fname, category, config_str, display_name))
        elif category == "invalid":
            config_str = draw(invalid_skin_json_strategy())
            folders.append((fname, category, config_str, None))
        else:  # missing
            folders.append((fname, category, None, None))

    return folders


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_folder(base: Path, folder_name: str, category: str, config_str: str | None):
    """Create a folder in base, optionally writing a skin.json."""
    d = base / folder_name
    d.mkdir(parents=True, exist_ok=True)
    if category in ("valid_oracle", "valid_avatar", "invalid"):
        (d / "skin.json").write_text(config_str, encoding="utf-8")
    # "missing" → no skin.json written


# ---------------------------------------------------------------------------
# Property test
# ---------------------------------------------------------------------------


@settings(max_examples=100)
@given(folders=folder_set_strategy())
def test_skin_listing_returns_only_valid_skins(folders) -> None:
    """**Validates: Requirements 11.1, 11.7, 8.3, 12.9**

    For any set of avatar folders where some contain valid skin.json files and
    some contain invalid or missing configs, discover_skins() returns exactly
    the skins with valid configurations, excludes all others, and oracle-type
    skins appear first in the list.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)

        # --- Arrange: write folders to tmp_path ---
        for folder_name, category, config_str, _display_name in folders:
            _write_folder(tmp_path, folder_name, category, config_str)

        # --- Act ---
        result = discover_skins(tmp_path)

        # --- Compute expected valid folder names ---
        expected_valid = {
            fname for fname, cat, _, _ in folders
            if cat in ("valid_oracle", "valid_avatar")
        }

        returned_folder_names = {fname for fname, _cfg in result}

        # Property 1: Only valid skins are returned
        assert returned_folder_names == expected_valid, (
            f"Expected valid folders {expected_valid}, got {returned_folder_names}"
        )

        # Property 2: No invalid or missing configs are included
        invalid_or_missing = {
            fname for fname, cat, _, _ in folders
            if cat in ("invalid", "missing")
        }
        assert returned_folder_names.isdisjoint(invalid_or_missing), (
            f"Invalid/missing folders leaked into results: "
            f"{returned_folder_names & invalid_or_missing}"
        )

        # Property 3: Oracle-type skins appear first in the list
        if len(result) > 1:
            oracle_indices = [i for i, (_, cfg) in enumerate(result) if cfg.type == "oracle"]
            avatar_indices = [i for i, (_, cfg) in enumerate(result) if cfg.type == "avatar"]
            if oracle_indices and avatar_indices:
                assert max(oracle_indices) < min(avatar_indices), (
                    "Oracle skins must appear before all avatar skins"
                )

        # Property 4: Each returned config has the correct type
        for fname, cfg in result:
            assert cfg.type in ("oracle", "avatar")
            # Cross-check with our input data
            matching = [
                (cat, dn) for fn, cat, _, dn in folders if fn == fname
            ]
            assert len(matching) == 1
            cat, _dn = matching[0]
            if cat == "valid_oracle":
                assert cfg.type == "oracle"
            elif cat == "valid_avatar":
                assert cfg.type == "avatar"
