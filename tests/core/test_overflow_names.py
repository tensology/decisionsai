"""2026-11-11: project names never truncate; long thread/breadcrumb names use a shared marquee."""
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[2]
CSS = ROOT / 'distr/gui/web/static/development/shared/overflow-names.css'
JS = ROOT / 'distr/gui/web/static/development/shared/overflow-names.js'


def test_overflow_name_css_never_truncates_project_names():
    text = CSS.read_text(encoding='utf-8')
    for selector in ('#header-project', '.project-select span', '.plan-project',
                     '.terminal-project-titlebar strong', '.proposal-project', '.reports-chip'):
        assert selector in text
    assert 'text-overflow: clip' in text
    assert 'flex-shrink: 0' in text
    assert 'name-marquee-seamless' in text
    assert 'prefers-reduced-motion: reduce' in text
    # Must not un-hide the terminal-details breadcrumb the navigation worker removed.
    assert '.terminal-workspace .terminal-project-header' not in text or 'display: none' in text


def test_overflow_name_helper_defaults_and_distance():
    loader = (ROOT / 'tests/ui/strip-query-loader.mjs').resolve().as_uri()
    test_file = str(ROOT / 'tests/ui/test_overflow_names.mjs')
    result = subprocess.run(
        ['node', '--experimental-loader', loader, '--test', test_file],
        capture_output=True, text=True, cwd=ROOT,
    )
    assert result.returncode == 0, result.stdout + '\n' + result.stderr
