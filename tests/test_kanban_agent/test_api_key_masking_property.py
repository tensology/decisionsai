# Feature: kanban-cli-settings-restructure, Property 5: API key masking displays only last four characters
"""
Property 5: API key masking displays only last four characters

For any API key string of length >= 4, the masked display value should
consist of mask characters (e.g., ``•``) for all characters except the last
four, which should match the original string's last four characters.  For
strings shorter than 4 characters, the entire string should be masked.

**Validates: Requirements 5.4**
"""
from hypothesis import given, settings
from hypothesis import strategies as st

from distr.core.kanban.utils import MASK_CHAR, mask_api_key


# ── Strategies ──

# Non-empty strings of printable characters (no leading/trailing whitespace
# that would be stripped, to keep assertions straightforward).
api_key_long_st = st.text(
    alphabet=st.characters(whitelist_categories=("L", "N", "P", "S")),
    min_size=4,
    max_size=200,
)

api_key_short_st = st.text(
    alphabet=st.characters(whitelist_categories=("L", "N", "P", "S")),
    min_size=1,
    max_size=3,
)


class TestApiKeyMaskingProperty:
    """Property 5: API key masking displays only last four characters."""

    @given(key=api_key_long_st)
    @settings(max_examples=100, deadline=None)
    def test_long_key_shows_only_last_four(self, key: str):
        """
        **Validates: Requirements 5.4**

        For keys of length >= 4, the masked value must:
        1. Have the same length as the original key.
        2. End with the last four characters of the original key.
        3. Have all preceding characters replaced with the mask character.
        """
        masked = mask_api_key(key)

        assert len(masked) == len(key)
        assert masked[-4:] == key[-4:]
        assert masked[:-4] == MASK_CHAR * (len(key) - 4)

    @given(key=api_key_short_st)
    @settings(max_examples=100, deadline=None)
    def test_short_key_fully_masked(self, key: str):
        """
        **Validates: Requirements 5.4**

        For keys shorter than 4 characters, the entire string must be masked.
        """
        masked = mask_api_key(key)

        assert len(masked) == len(key)
        assert masked == MASK_CHAR * len(key)

    def test_empty_and_none_return_empty(self):
        """
        **Validates: Requirements 5.4**

        None and empty strings should return an empty string.
        """
        assert mask_api_key(None) == ""
        assert mask_api_key("") == ""
        assert mask_api_key("   ") == ""
