from distr.core.agent.tools.integrations.playwright_tool import PlaywrightTool


def test_read_only_browser_lookup_does_not_require_confirmation():
    code = "page.goto('https://stitch.money'); page.screenshot(path='result.png')"
    assert PlaywrightTool._requires_confirmation(code) is False


def test_browser_mutation_requires_confirmation():
    code = "page.goto('https://example.com'); page.get_by_label('Email').fill('x@example.com'); page.click('button')"
    assert PlaywrightTool._requires_confirmation(code) is True
