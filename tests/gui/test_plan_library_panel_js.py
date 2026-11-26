"""Section E frontend: the side panel is screen-aware and never breaks the UI."""
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PANEL = ROOT / 'distr/gui/web/static/development/planning/library-panel.js'


def _run(body: str) -> str:
    script = f"import * as panel from {PANEL.as_uri()!r};\n" + body
    result = subprocess.run(
        ['node', '--input-type=module', '-e', script],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def test_the_screen_decides_the_library_kind():
    out = _run("""
    console.log(JSON.stringify([
      panel.screenKind('sitemap'), panel.screenKind('flow'), panel.screenKind('FLOWS'),
      panel.screenKind('page'), panel.screenKind('wireframe'), panel.screenKind('nonsense'),
      panel.screenKind(''),
    ]));
    """)
    assert json.loads(out) == [
        'page-template', 'page-template', 'page-template',
        'widget', 'widget', 'widget',
        'widget',
    ]


def test_an_unknown_screen_normalises_to_a_page():
    out = _run("console.log(JSON.stringify([panel.normalizeScreen('nonsense'), panel.normalizeScreen('sitemap'), panel.normalizeScreen('')]));")
    assert json.loads(out) == ['page', 'sitemap', 'page']


def test_the_panel_swaps_between_chat_and_library():
    out = _run("""
    console.log(JSON.stringify([
      panel.toggleMode('chat', 'library'), panel.toggleMode('library', 'chat'),
      panel.toggleMode('chat', 'nonsense'), panel.toggleMode('junk', 'nonsense'),
    ]));
    """)
    assert json.loads(out) == ['library', 'chat', 'chat', 'chat']


def test_the_library_url_carries_the_screen():
    out = _run("""
    console.log(JSON.stringify([
      panel.libraryUrl('/api', 7, 'sitemap'),
      panel.libraryUrl('/api/', 7, 'page', 'widget'),
    ]));
    """)
    urls = json.loads(out)
    assert urls[0] == '/api/workflows/studio/plan-workspaces/7/library?screen=sitemap'
    assert urls[1].endswith('screen=page&kind=widget')


def test_a_failed_load_is_empty_not_thrown():
    out = _run("""
    const bad = await panel.loadLibrary('/api', 1, 'page', { fetchImpl: async () => { throw new Error('boom'); } });
    const http = await panel.loadLibrary('/api', 1, 'page', { fetchImpl: async () => ({ ok: false, status: 500 }) });
    console.log(JSON.stringify([bad.entries.length, bad.error, http.error]));
    """)
    entries, err, http_err = json.loads(out)
    assert entries == 0
    assert 'boom' in err
    assert '500' in http_err


def test_a_malformed_body_is_treated_as_empty():
    out = _run("""
    const r = await panel.loadLibrary('/api', 1, 'page', { fetchImpl: async () => ({ ok: true, json: async () => ({}) }) });
    console.log(JSON.stringify([r.entries.length, r.error]));
    """)
    assert json.loads(out) == [0, '']


def test_entries_group_by_kind_then_tag_and_skip_junk():
    out = _run("""
    const groups = panel.groupEntries([
      { name: 'b', kind: 'widget', title: 'B', tags: ['marketing'] },
      { name: 'a', kind: 'widget', title: 'A', tags: ['dashboard'] },
      { name: 'c', kind: 'page-template', title: 'C', tags: [] },
      null, {}, { title: 'no name' },
    ]);
    console.log(JSON.stringify(groups.map(g => [g.kind, g.groups.map(x => [x.tag, x.items.map(i => i.name)])])));
    """)
    assert json.loads(out) == [
        ['page-template', [['general', ['c']]]],
        ['widget', [['dashboard', ['a']], ['marketing', ['b']]]],
    ]


def test_grouping_survives_a_non_array():
    out = _run("console.log(JSON.stringify(panel.groupEntries(null)));")
    assert json.loads(out) == []
