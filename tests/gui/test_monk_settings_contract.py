from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_preferences_exposes_monk_mode_tab_and_script() -> None:
    template = (ROOT / "distr/gui/web/templates/settings/settings.html").read_text(encoding="utf-8")
    assert 'href="#monk"' in template
    assert 'data-tab="monk"' in template
    assert 'id="tab-monk"' in template
    assert 'settings/sections/monk.html' in template
    assert '/settings/static/js/monk.js' in template


def test_monk_mode_ui_has_crud_toggle_and_schedule_controls() -> None:
    section = (ROOT / "distr/gui/web/templates/settings/sections/monk.html").read_text(encoding="utf-8")
    for control_id in (
        "monk-enabled",
        "monk-add-site",
        "monk-sites-list",
        "monk-schedule-enabled",
        "monk-schedule-editor",
        "monk-schedule-days",
        "monk-schedule-start",
        "monk-schedule-end",
        "monk-schedule-actions",
        "monk-save-schedule",
    ):
        assert f'id="{control_id}"' in section


def test_monk_mode_schedule_precedes_tabular_website_list_without_label_field() -> None:
    section = (ROOT / "distr/gui/web/templates/settings/sections/monk.html").read_text(encoding="utf-8")
    assert section.index('id="monk-schedule-heading"') < section.index('id="monk-websites-heading"')
    assert 'role="table"' in section
    assert 'role="columnheader">Website' in section
    assert "Label (optional)" not in section


def test_monk_mode_uses_one_weekly_window_instead_of_schedule_rows() -> None:
    section = (ROOT / "distr/gui/web/templates/settings/sections/monk.html").read_text(encoding="utf-8")
    script = (ROOT / "distr/gui/web/static/settings/js/monk.js").read_text(encoding="utf-8")
    assert "Switches on" in section
    assert ">From<" in section
    assert ">Until<" in section
    assert "Add schedule" not in section
    assert "monk-schedule-row" not in section + script
    assert "monk-window-enabled" not in section + script


def test_monk_mode_supports_multiple_editable_rows_and_multi_address_paste() -> None:
    script = (ROOT / "distr/gui/web/static/settings/js/monk.js").read_text(encoding="utf-8")
    assert "draftSites.push(createDraft" in script
    assert "splitAddresses(clipboard)" in script
    assert "addresses.slice(1)" in script
    assert "monk-site-input" in script


def test_oracle_preferences_menu_contains_monk_toggle_and_manager() -> None:
    menu = (ROOT / "distr/gui/oracle/menu.py").read_text(encoding="utf-8")
    assert 'QAction("Monk Mode", self.preferences_submenu)' in menu
    assert 'setCheckable(True)' in menu
    assert '"/settings#monk"' in menu
    assert '_reconcile_monk_schedule' in menu
