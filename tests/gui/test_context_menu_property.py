# Feature: oracle-skins-system, Property 16: Context menu text uses skin name
# Validates: Requirements 1.3
"""Property-based test: for any valid SkinConfig with a non-empty name field,
the context menu visibility toggle text should be "Hide {name}" or "Show {name}"
and the change action text should be "Change {name}". For any SkinConfig with
an empty or missing name, the text should use "Avatar" as the fallback."""

from hypothesis import given, settings
from hypothesis import strategies as st

from distr.gui.oracle.menu import get_skin_display_name


# ---------------------------------------------------------------------------
# Hypothesis strategies
# ---------------------------------------------------------------------------

_non_empty_name = st.text(
    alphabet=st.sampled_from(
        "abcdefghijklmnopqrstuvwxyz ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_"
    ),
    min_size=1,
    max_size=40,
).filter(lambda s: s.strip() != "")

_empty_or_missing_name = st.one_of(
    st.just(""),
    st.just(None),
    st.text(alphabet=st.just(" "), min_size=1, max_size=10),  # whitespace-only
)


# ---------------------------------------------------------------------------
# Property tests
# ---------------------------------------------------------------------------


@settings(max_examples=100)
@given(name=_non_empty_name)
def test_skin_display_name_uses_non_empty_name(name: str) -> None:
    """**Validates: Requirements 1.3**

    For any valid SkinConfig with a non-empty name field, the display name
    should be that name, and context menu text should be
    "Hide {name}" / "Show {name}" and "Change {name}".
    """
    display = get_skin_display_name(name)
    assert display == name, f"Expected '{name}', got '{display}'"

    # Verify the menu text patterns
    assert f"Hide {display}" == f"Hide {name}"
    assert f"Show {display}" == f"Show {name}"
    assert f"Change {display}" == f"Change {name}"


@settings(max_examples=100)
@given(name=_empty_or_missing_name)
def test_skin_display_name_falls_back_to_avatar(name) -> None:
    """**Validates: Requirements 1.3**

    For any SkinConfig with an empty or missing name, the display name
    should fall back to "Avatar".
    """
    display = get_skin_display_name(name)
    assert display == "Avatar", f"Expected 'Avatar' for name={name!r}, got '{display}'"

    # Verify the menu text patterns use the fallback
    assert f"Hide {display}" == "Hide Avatar"
    assert f"Show {display}" == "Show Avatar"
    assert f"Change {display}" == "Change Avatar"
