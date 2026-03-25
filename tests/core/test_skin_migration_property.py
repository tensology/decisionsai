# Feature: oracle-skins-system, Property 12: Settings migration maps legacy values correctly
# Validates: Requirements 13.1, 13.2, 13.3
"""Property-based test: for any legacy selected_oracle value, the migration
function maps GIF filenames to ``"oracle"``, passes through valid folder names
unchanged, and defaults empty/None to ``"oracle"``."""

from hypothesis import given, settings
from hypothesis import strategies as st

from distr.core.skin_migration import migrate_selected_oracle


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------


@st.composite
def gif_filename_strategy(draw):
    """Generate strings like "0.gif", "1.gif", "12.gif", "999.gif"
    (one or more digits followed by ".gif")."""
    digits = draw(st.integers(min_value=0, max_value=9999))
    return f"{digits}.gif"


@st.composite
def folder_name_strategy(draw):
    """Generate valid avatar folder names that do NOT match the GIF pattern.
    Lowercase letters, digits, and hyphens — e.g. "clippy", "cupidon",
    "my-avatar-123".  Must contain at least one letter so it cannot be
    purely digits (which would collide with the GIF pattern minus the
    extension)."""
    name = draw(
        st.from_regex(r"[a-z][a-z0-9\-]{0,19}", fullmatch=True)
    )
    return name


@st.composite
def empty_or_none_strategy(draw):
    """Generate either an empty string or None."""
    return draw(st.sampled_from(["", None]))


# ---------------------------------------------------------------------------
# Property tests
# ---------------------------------------------------------------------------


@settings(max_examples=100)
@given(gif_name=gif_filename_strategy())
def test_gif_filenames_migrate_to_oracle(gif_name: str) -> None:
    """**Validates: Requirements 13.1**

    For any string matching the pattern of a GIF filename (digits followed by
    ``.gif``), the migration function should return ``"oracle"``."""
    result = migrate_selected_oracle(gif_name)
    assert result == "oracle", (
        f"Expected 'oracle' for GIF filename {gif_name!r}, got {result!r}"
    )


@settings(max_examples=100)
@given(folder=folder_name_strategy())
def test_folder_names_pass_through_unchanged(folder: str) -> None:
    """**Validates: Requirements 13.2**

    For any string matching a valid avatar folder name (lowercase letters,
    digits, hyphens — NOT matching the GIF pattern), the migration function
    should return that folder name unchanged."""
    result = migrate_selected_oracle(folder)
    assert result == folder, (
        f"Expected folder name {folder!r} to pass through unchanged, got {result!r}"
    )


@settings(max_examples=100)
@given(value=empty_or_none_strategy())
def test_empty_or_none_defaults_to_oracle(value) -> None:
    """**Validates: Requirements 13.3**

    For empty string or None, the migration function should return
    ``"oracle"``."""
    result = migrate_selected_oracle(value)
    assert result == "oracle", (
        f"Expected 'oracle' for {value!r}, got {result!r}"
    )
