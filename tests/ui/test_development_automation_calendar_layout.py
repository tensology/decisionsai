"""Dense calendar collision groups retain every occurrence and readable names."""
from pathlib import Path

import pytest
from playwright.sync_api import Page, expect

from tests.ui.test_development_automation_edit import install
from tests.ui.test_development_modules_playwright import BASE

pytestmark = pytest.mark.e2e_playwright
OUTPUT = Path('/tmp/decisions-calendar-layout')


def dense_calendar(page):
    rows, writes, _ = install(page)
    rows.clear()
    times = ['07:00', '07:03', '07:06', '07:09'] + ['09:00'] * 13 + ['09:45', '10:00', '11:00', '22:30']
    for index, time in enumerate(times):
        rows.append({
            'id': f'auto_{index}', 'name': f'Automation {index}: review scheduled project work and incoming messages',
            'status': 'active', 'automation_type': 'scheduled_instruction', 'instruction': 'Run the configured task.',
            'schedule': {'kind': 'daily', 'time': time, 'timezone': 'Africa/Johannesburg'},
        })
    for index in range(2):
        rows.append({
            'id': f'cron_{index}', 'name': f'Recurring availability and support monitor {index}',
            'status': 'active', 'automation_type': 'tool_action', 'instruction': 'Check for meaningful changes.',
            'schedule': {'kind': 'cron', 'time': '09:00', 'cron': '0,15,30,45 8-19 * * *', 'timezone': 'Africa/Johannesburg'},
        })
    return rows, writes


@pytest.mark.parametrize('width', [390, 768, 1280, 1920])
def test_dense_week_has_readable_nonoverlapping_groups_and_full_expansion(page: Page, width):
    rows, writes = dense_calendar(page)
    page.set_viewport_size({'width': width, 'height': 1000})
    page.goto(BASE + '/development/automations/')
    page.locator('[data-scheduled-view="calendar"]').click()
    expect(page.locator('.cal-timegrid.week')).to_be_visible()
    page.locator('[data-cal-scroll]').evaluate('(node) => node.scrollTop = 6 * 64')
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    columns = page.locator('.cal-tg-col')
    assert columns.count() == 7
    for column in columns.all():
        assert column.evaluate('''node => {
            const blocks = [...node.querySelectorAll('.cal-event-group, .cal-tg-event')]
                .map(el => el.getBoundingClientRect()).sort((a, b) => a.top - b.top);
            const bounds = node.getBoundingClientRect();
            return blocks.every((rect, i) => rect.width >= 100 && rect.left >= bounds.left &&
                rect.right <= bounds.right + 1 && (!i || rect.top >= blocks[i - 1].bottom + 3));
        }''')
    OUTPUT.mkdir(exist_ok=True)
    page.screenshot(path=str(OUTPUT / f'week-{width}.png'))
    first = columns.first.locator('.cal-event-group').first
    expect(first).to_contain_text('4 automations')
    first.click()
    pop = page.locator('#scheduled-day-popover')
    expect(pop).to_be_visible()
    expect(pop.locator('[data-scheduled-calendar-event]')).to_have_count(4)
    for index in range(4):
        expect(pop).to_contain_text(rows[index]['name'])
    assert pop.evaluate('(node) => node.scrollWidth <= node.clientWidth')
    assert pop.locator('.cal-title-inner').first.evaluate('(node) => parseFloat(getComputedStyle(node).fontSize) >= 14')
    page.screenshot(path=str(OUTPUT / f'group-{width}.png'))
    page.keyboard.press('Escape')
    expect(pop).to_have_count(0)
    expect(first).to_be_focused()
    recurring = page.locator('.cal-recurring-group').first
    expect(recurring).to_have_text('2 recurring')
    recurring.click()
    expect(pop.locator('[data-scheduled-calendar-event]')).to_have_count(2)
    expect(pop).to_contain_text('Cron: 0,15,30,45 8-19 * * *')
    assert '09:00' not in pop.locator('time').all_text_contents()
    page.screenshot(path=str(OUTPUT / f'recurring-{width}.png'))
    pop.get_by_role('button', name='Close', exact=True).click()
    page.locator('[data-scheduled-calendar-mode="day"]').click()
    expect(page.locator('.cal-timegrid.day')).to_be_visible()
    page.locator('[data-cal-scroll]').evaluate('(node) => node.scrollTop = 6 * 64')
    page.screenshot(path=str(OUTPUT / f'day-{width}.png'))
    assert not writes


def test_month_overflow_still_opens_complete_day_list(page: Page):
    rows, writes = dense_calendar(page)
    page.set_viewport_size({'width': 1280, 'height': 900})
    page.goto(BASE + '/development/automations/')
    page.locator('[data-scheduled-view="calendar"]').click()
    page.locator('[data-scheduled-calendar-mode="month"]').click()
    page.screenshot(path=str(OUTPUT / 'month-1280.png'))
    page.locator('.cal-more').first.click()
    expect(page.locator('#scheduled-day-popover [data-scheduled-calendar-event]')).to_have_count(len(rows))
    page.screenshot(path=str(OUTPUT / 'month-expanded-1280.png'))
    assert not writes
