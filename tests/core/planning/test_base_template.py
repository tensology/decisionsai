"""Section B: the base template owns every decision; a page overrides by exception."""

import pytest

from distr.core.planning import base_template as bt


def test_defaults_are_complete_enough_to_render():
    base = bt.default_base()
    assert base.tokens and base.contracts and base.grid
    assert base.contracts["form.field"] in ("legend", "label")
    assert base.grid["columns"] == 12


def test_the_shipped_base_template_loads_from_the_library():
    base = bt.load()
    assert base.name == "default-base"
    # the shipped manifest is explicit about these
    assert base.contracts["table.striped"] is True
    assert base.contracts["form.field"] == "legend"


def test_a_missing_name_falls_back_to_a_usable_base():
    """A page must always have something to inherit from - never nothing."""
    base = bt.load("no-such-base-template")
    assert base.name == "default-base"
    assert base.tokens and base.contracts and base.grid


def test_a_page_that_overrides_nothing_is_the_base_unchanged():
    base = bt.default_base()
    assert bt.resolve(base, {}) == base
    assert bt.resolve(base, None) == base


def test_a_page_overrides_only_what_it_names():
    base = bt.default_base()
    page = bt.resolve(base, {"tokens": {"color.accent": "#ff0000"}})
    assert page.tokens["color.accent"] == "#ff0000"
    # everything else is still the base
    assert page.tokens["color.ink"] == base.tokens["color.ink"]
    assert page.contracts == base.contracts
    assert page.grid == base.grid


def test_changing_the_base_reaches_every_page():
    """The point of inherit-everything: one edit, all pages follow."""
    base = bt.default_base()
    page = bt.resolve(base, {})
    restyled = bt.resolve(base, {"tokens": {"font.size.3": "18px"}})
    assert page.tokens["font.size.3"] == base.tokens["font.size.3"]
    assert restyled.tokens["font.size.3"] == "18px"


def test_grid_overrides_merge_deeply():
    page = bt.resolve(bt.default_base(), {"grid": {"gap": "24px"}})
    assert page.grid["gap"] == "24px"
    assert page.grid["columns"] == 12          # untouched
    assert page.grid["breakpoints"]             # nested dict survives


# ------------------------------------------------------------------ validation

@pytest.mark.parametrize("bad", ["", "nonsense", 0, [], None])
def test_junk_config_falls_back_to_the_defaults(bad):
    base = bt.parse_base(bad)
    assert base.tokens["color.ink"] == bt.DEFAULT_TOKENS["color.ink"]


def test_an_unknown_contract_value_is_rejected_not_applied():
    base = bt.parse_base({"contracts": {"form.field": "nonsense"}})
    assert base.contracts["form.field"] == bt.DEFAULT_CONTRACTS["form.field"]


def test_a_valid_contract_value_is_applied():
    assert bt.parse_base({"contracts": {"form.field": "label"}}).contracts["form.field"] == "label"


@pytest.mark.parametrize("bad", [0, -1, 25, "junk"])
def test_an_out_of_range_column_count_is_rejected(bad):
    base = bt.parse_base({"grid": {"columns": bad}})
    assert base.grid["columns"] == bt.DEFAULT_GRID["columns"]


def test_a_valid_column_count_is_applied():
    assert bt.parse_base({"grid": {"columns": 6}}).grid["columns"] == 6


def test_unknown_contract_keys_pass_through():
    """A contract the code has never heard of is not silently dropped."""
    base = bt.parse_base({"contracts": {"future.thing": "on"}})
    assert base.contracts["future.thing"] == "on"


# ------------------------------------------------------------------ css

def test_css_exposes_tokens_as_custom_properties():
    css = bt.to_css(bt.default_base())
    assert "--wf-color-ink:" in css
    assert "--wf-font-size-3:" in css


def test_the_form_standard_changes_the_css():
    legend = bt.to_css(bt.parse_base({"contracts": {"form.field": "legend"}}))
    label = bt.to_css(bt.parse_base({"contracts": {"form.field": "label"}}))
    assert "legend {" in legend and "label {" not in legend
    assert "label {" in label and "legend {" not in label


def test_table_density_changes_the_css():
    compact = bt.to_css(bt.parse_base({"contracts": {"table.density": "compact"}}))
    comfy = bt.to_css(bt.parse_base({"contracts": {"table.density": "comfortable"}}))
    assert compact != comfy
    assert "6px 10px" in compact
    assert "10px 14px" in comfy


def test_grid_columns_reach_the_css():
    css = bt.to_css(bt.parse_base({"grid": {"columns": 6}}))
    assert "repeat(6, minmax(0, 1fr))" in css


def test_css_is_deterministic():
    assert bt.to_css(bt.default_base()) == bt.to_css(bt.default_base())


def test_from_entry_never_raises_on_junk():
    assert bt.from_entry(None) == bt.default_base()
    assert bt.from_entry(object()) == bt.default_base()


def test_for_page_loads_and_overrides_in_one_call():
    page = bt.for_page(None, {"tokens": {"color.accent": "#000000"}})
    assert page.tokens["color.accent"] == "#000000"
    assert page.tokens["color.ink"] == bt.DEFAULT_TOKENS["color.ink"]
