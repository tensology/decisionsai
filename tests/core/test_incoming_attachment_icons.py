"""2026-11-11: shared file-type icons for the Gmail/Mailshot attachment bar and preview title."""
from pathlib import Path
import subprocess


def test_attachment_file_kind_and_icon_svg():
    source = Path('distr/gui/web/static/development/incoming/index.js').resolve().as_uri()
    loader = Path('tests/ui/strip-query-loader.mjs').resolve().as_uri()
    script = '''
import assert from 'node:assert/strict';
const {attachmentFileKind: kind, fileTypeIconSvg: icon} = await import(SOURCE);
const cases = [
  [{filename:'a.pdf'}, 'pdf'], [{mime_type:'application/pdf'}, 'pdf'],
  [{filename:'a.DOCX'}, 'word'], [{mime_type:'application/vnd.openxmlformats-officedocument.wordprocessingml.document'}, 'word'],
  [{filename:'a.xlsx'}, 'sheet'], [{filename:'a.csv', mime_type:'text/csv'}, 'sheet'],
  [{filename:'a.pptx'}, 'slides'], [{mime_type:'image/png'}, 'image'], [{filename:'v.m4a'}, 'audio'],
  [{filename:'v.mp4'}, 'video'], [{filename:'b.zip'}, 'archive'], [{mime_type:'application/zip'}, 'archive'],
  [{filename:'s.py'}, 'code'], [{filename:'r.txt'}, 'text'], [{filename:'x.bin'}, 'file'], [{}, 'file'],
  [{name:'Report.PDF', mime_type:'application/octet-stream'}, 'pdf'],
];
for (const [item, expected] of cases) assert.equal(kind(item), expected, JSON.stringify(item));
const svg = icon('pdf');
assert.match(svg, /^<svg class="file-type-icon file-type-pdf" data-file-kind="pdf"/);
assert.match(svg, />PDF<\\/text><\\/svg>$/);
assert.match(svg, /aria-hidden="true"/);
assert.match(icon('nope'), /data-file-kind="file"/);
assert.match(icon('pdf', 24), /width="21" height="24"/);
assert.doesNotMatch(svg, /https?:/);
'''.replace('SOURCE', repr(source))
    result = subprocess.run(['node', '--experimental-loader', loader, '--input-type=module', '-e', script], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
