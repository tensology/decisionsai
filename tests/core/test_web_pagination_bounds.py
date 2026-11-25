"""Client-supplied pagination must be bounded regardless of what is requested."""

from distr.gui.web.pagination import (
    DEFAULT_PAGE_SIZE,
    MAX_PAGE_SIZE,
    clamp_limit,
    clamp_offset,
)


def test_clamp_limit_caps_a_huge_request():
    assert clamp_limit(999_999_999) == MAX_PAGE_SIZE


def test_clamp_limit_falls_back_for_junk_and_non_positive():
    assert clamp_limit("not-a-number") == DEFAULT_PAGE_SIZE
    assert clamp_limit(None) == DEFAULT_PAGE_SIZE
    assert clamp_limit(0) == DEFAULT_PAGE_SIZE
    assert clamp_limit(-5) == DEFAULT_PAGE_SIZE


def test_clamp_limit_keeps_a_sane_value():
    assert clamp_limit(5) == 5
    assert clamp_limit(5, default=50) == 5
    assert clamp_limit("7") == 7


def test_clamp_limit_honours_a_custom_ceiling():
    assert clamp_limit(5000, default=50, maximum=200) == 200


def test_clamp_offset_never_goes_negative():
    assert clamp_offset(-3) == 0
    assert clamp_offset("junk") == 0
    assert clamp_offset(None) == 0
    assert clamp_offset(7) == 7


def test_clamp_offset_can_be_capped():
    assert clamp_offset(10_000, maximum=500) == 500
