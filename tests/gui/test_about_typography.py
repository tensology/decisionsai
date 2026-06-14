from distr.gui.dialogs import about


def test_about_markdown_html_uses_shared_tab_typography() -> None:
    html = about.AboutWindow._markdown_to_html(
        object(),
        "# DecisionsAI Changelog\n"
        "## [2.8.0] - 2026-06-14\n"
        "### Fixed\n"
        "#### About Window\n"
        "- Tightened changelog typography\n"
        "Plain paragraph body text.\n",
    )

    assert f"line-height: {about._tab_line_height_css()};" in html
    assert f"font-size: {about._TAB_FONT_SIZE_PX}px;" in html
    assert f"font-size: {about._TAB_FONT_SIZE_PX + 1}px;" not in html
    assert f"font-size: {about._TAB_FONT_SIZE_PX + 2}px;" not in html
    assert f"font-size: {about._TAB_FONT_SIZE_PX + 4}px;" not in html
    assert "<br>" not in html
    assert "Plain paragraph body text." in html


def test_about_helper_html_uses_shared_tab_typography() -> None:
    paragraph = about._html_paragraph("Credit")
    section_title = about._html_section_title("Credits")
    wrapper = about._wrap_tab_html(paragraph + section_title)

    for html in (paragraph, section_title, wrapper):
        assert f"font-size: {about._TAB_FONT_SIZE_PX}px;" in html
        assert f"line-height: {about._tab_line_height_css()};" in html
