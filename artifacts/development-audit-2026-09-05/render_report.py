from pathlib import Path
import hashlib
import json
import re
import shutil
import markdown
from playwright.sync_api import sync_playwright

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
source = (HERE/'audit.md').read_text()
assert '\u2014' not in source and '\u2013' not in source
for name, original in {
    'marketing-workflows.png':'/tmp/decisions-marketing-workflows.png',
    'live-workflow-editor.png':'/tmp/decisions-live-workflow-editor.png',
    'live-workflows.png':'/tmp/decisions-live-workflows.png',
    'live-plan.png':'/tmp/decisions-live-plan.png',
    'live-workflow-mobile.png':'/tmp/decisions-live-workflow-mobile.png',
}.items():
    shutil.copyfile(original,HERE/name)

sections = source.split('\n## ')[1:]
pages = []
for i,section in enumerate(sections,1):
    title, body = section.split('\n',1)
    content = markdown.markdown(body,extensions=['tables','fenced_code'])
    pages.append(f'<section class="page" id="page-{i}"><header><b>DECISIONS<span>AI</span></b><small>DEVELOPMENT AUDIT / 05 SEP 2026</small></header><h1>{title}</h1><div class="content">{content}</div><footer><span>Audit and proposed remediation. Application source unchanged.</span><b>{i:02d} / {len(sections):02d}</b></footer></section>')
css = '''
*{box-sizing:border-box}html{scroll-behavior:smooth}body{margin:0;background:#e9edf3;color:#202d41;font:15px/1.48 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}
.page{width:1056px;min-height:800px;margin:24px auto;background:white;padding:36px 48px 28px;box-shadow:0 4px 24px #20304712;position:relative}
header{display:flex;justify-content:space-between;align-items:center;border-bottom:2px solid #16234c;padding-bottom:14px;color:#16234c}header b{font-size:20px;letter-spacing:-.6px}header b span{color:#d45b0a}header small{font-size:11px;letter-spacing:1px}
h1{font-size:29px;line-height:1.2;letter-spacing:-.65px;margin:26px 0 20px;color:#13234a}p{margin:12px 0}strong{color:#14234b}a{color:#a4480a}code{font:12px/1.5 ui-monospace,SFMono-Regular,monospace;overflow-wrap:anywhere}pre{background:#f2f5fa;border-left:3px solid #e97924;padding:16px 18px;white-space:pre-wrap;break-inside:avoid}pre code{font-size:13px}
table{border-collapse:collapse;width:100%;table-layout:fixed;margin:18px 0;font-size:13px}td,th{padding:10px 12px;text-align:left;border-bottom:1px solid #d8deea;vertical-align:top;overflow-wrap:anywhere}th{background:#16234c;color:white;font-weight:600}tr:nth-child(even) td{background:#f5f7fa}tr{break-inside:avoid}
footer{display:flex;justify-content:space-between;margin-top:26px;padding-top:14px;border-top:1px solid #d8deea;font-size:11px;color:#647087}footer b{color:#16234c}.comparison{display:grid;grid-template-columns:1fr 1fr;gap:18px;margin:18px 0}figure{margin:0}figure img{width:100%;display:block;border:1px solid #ccd3e0}figcaption{font-size:12px;line-height:1.45;padding-top:8px;color:#556278}
@media print{body{background:white}.page{margin:0;box-shadow:none;break-after:page;width:100%;min-height:0}header,footer{break-inside:avoid}}'''
html='<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>DecisionsAI Development Audit - 5 September 2026</title><style>'+css+'</style><body>'+''.join(pages)+'</body></html>'
(HERE/'audit.html').write_text(html)
with sync_playwright() as p:
    browser=p.chromium.launch(headless=True)
    page=browser.new_page(viewport={'width':1152,'height':1000},device_scale_factor=1)
    page.goto((HERE/'audit.html').as_uri())
    page.locator('img').evaluate_all('(els)=>Promise.all(els.map(e=>e.decode()))')
    views=[]
    for i in range(1,len(sections)+1):
        target=page.locator(f'#page-{i}')
        target.screenshot(path=str(HERE/f'render-{i:02d}.png'))
        views.append(target.evaluate('(e)=>({page:e.id,width:e.offsetWidth,height:e.offsetHeight,overflow:e.scrollWidth>e.clientWidth})'))
    browser.close()
manifest={'views':views,'source_sha256':hashlib.sha256(source.encode()).hexdigest(),'audited_files':{}}
for path in ['distr/gui/web/static/workflows/js/studio.js','distr/gui/web/static/workflows/js/plan.js','distr/gui/web/static/workflows/css/studio.css','distr/gui/web/routes/settings/workflows.py','distr/core/workflow/planning_workspace.py','distr/core/workflow/development_control.py','distr/core/automation/imports.py','distr/core/automation/scheduler.py']:
    manifest['audited_files'][path]=hashlib.sha256((ROOT/path).read_bytes()).hexdigest()
(HERE/'render-manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
print(json.dumps(views,indent=2))
