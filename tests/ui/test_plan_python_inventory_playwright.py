"""Render actual AST-discovered artifacts through Development, with isolated APIs."""
import re
from pathlib import Path

import pytest
from playwright.sync_api import expect

from distr.core.planning.project_scan import build_plan
from distr.core.planning.interface_rebuild import mark_flows, overlay
from tests.core.test_plan_python_inventory_integration import app_fixture, write
from tests.ui.test_development_harness_playwright import _install_api
from tests.ui.test_plan_interfaces_nodes_playwright import NodeBackend
from tests.ui.test_plan_inline_edit_playwright import PLAN_URL

pytestmark=pytest.mark.e2e_playwright
OUT=Path('/tmp/decisions-edge-audit/python-inventory')


def capture(page, width, name):
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    OUT.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(OUT/f'{name}-{width}.png'))


@pytest.mark.parametrize('width,height',[(390,844),(768,1024),(1280,900),(1440,900)])
def test_discovered_python_pages_models_and_documents_are_usable(page,tmp_path,width,height):
    if OUT.is_dir():
        for previous in OUT.glob(f'*-{width}.png'):
            previous.unlink()
    app_fixture(tmp_path)
    path=tmp_path/'app.py'
    path.write_text(path.read_text()+'''\n@app.get('/projects/')
async def project_page(request):
    return templates.TemplateResponse(request, 'projects/index.html', {})
''')
    write(tmp_path,'templates/projects/index.html','<h1>Projects</h1><a href="/development/">Development</a>')
    write(tmp_path,'frontend/src/routes.jsx','export const routes=[{url:"/development",name:"development",view:"development"}];')
    write(tmp_path,'frontend/src/pages/development.jsx','export function Development(){return <><TextField label="Filter" /><button>Search</button></>;}')
    result=build_plan(tmp_path,project_name='Python planning fixture')
    backend=NodeBackend(content=result['artifacts']['flows'])
    backend.items=[{'id':n+10,'item_type':kind,'title':kind.title(), 'content':content,
        'content_format':'wire' if kind=='flows' else 'mermaid' if kind=='architecture' else 'markdown',
        'status':'draft','revision_count':1} for n,(kind,content) in enumerate(result['artifacts'].items())]
    _install_api(page,[])
    page.route('**/api/**',backend.route)
    page.set_viewport_size({'width':width,'height':height})
    errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
    page.goto(PLAN_URL)
    page.get_by_role('button',name='Interfaces',exact=True).click()
    expect(page.locator('.iface-node').filter(has_text='Development').first).to_be_visible()
    expect(page.locator('.iface-node').filter(has_text='Projects').first).to_be_visible()
    guidance=page.locator('.iface-node[data-lane="guidance"]')
    expect(guidance).to_contain_text('Overview')
    expect(guidance.locator('.iface-route')).to_have_text('guidance')
    expect(page.locator('.iface-map-status')).to_have_attribute('title', re.compile('2 pages · 1 guidance item'))
    capture(page,width,'interfaces')
    page.locator('.iface-node').filter(has_text='Development').first.focus()
    page.keyboard.press('Enter')
    page.get_by_role('tablist', name='Page perspective').get_by_role('button', name='Planning', exact=True).click()
    planned=page.locator('.plan-page-hub li').filter(has_text='Planned data bindings:')
    expect(planned).to_contain_text('Project.title')
    planned.evaluate('n=>n.scrollIntoView({block:"center"})')
    capture(page,width,'planned-bindings')
    page.locator('#plan-preview').evaluate('n=>n.scrollTop=n.scrollHeight')
    save=page.locator('#plan-preview .plan-inline-save')
    expect(save).to_be_visible()
    assert save.evaluate('''n=>{
      const b=n.getBoundingClientRect(), dock=document.querySelector('.plan-pinned-dock').getBoundingClientRect();
      return b.bottom <= dock.top - 4;
    }'''), 'The page description actions must scroll completely clear of the floating dock'
    capture(page,width,'planned-end')
    page.get_by_role('button',name='Development',exact=True).click()
    hint=page.locator('.plan-page-hub p').filter(has_text='These bindings are plan annotations.')
    expect(hint).to_be_visible()
    hint.evaluate('n=>n.scrollIntoView({block:"center"})')
    capture(page,width,'binding-confirmation')
    page.get_by_role('button',name='Data',exact=True).click()
    expect(page.locator('.erd-table[data-table="Project"]')).to_be_visible()
    expect(page.locator('.erd-table[data-table="Project"]')).to_contain_text('title')
    capture(page,width,'data')
    page.get_by_role('button',name='Documents',exact=True).click()
    for tab in ('Brief','PRD','FRAC'):
        page.get_by_role('tab',name=tab,exact=True).click()
        editor=page.locator('.plan-inline-body')
        expect(editor).to_contain_text('Draft from source discovery.')
        if tab=='PRD':
            expect(editor).to_contain_text('Actors and permissions are not established')
        if tab=='Brief':
            expect(editor).to_contain_text('2 route pages resolved to source with observed headings, controls or links')
        if tab=='FRAC':
            expect(editor).to_contain_text('Observed controls:')
            expect(editor).to_contain_text('provides a valid Title')
            expect(editor).to_contain_text('Save project')
            expect(editor).to_contain_text('provides a valid Filter')
            expect(editor).to_contain_text('Search')
            expect(editor).to_contain_text('frontend/src/pages/development.jsx')
            expect(editor).to_contain_text('templates/development/index.html')
        # Capture every readable scroll view, including the final line.
        total=editor.evaluate('n => Math.max(0,n.scrollHeight-n.clientHeight)')
        step=max(100,editor.evaluate('n=>n.clientHeight')-40)
        offsets=list(range(0,total+1,step))
        if offsets[-1]!=total: offsets.append(total)
        for i,offset in enumerate(offsets):
            editor.evaluate('(n,y)=>n.scrollTop=y',offset)
            capture(page,width,f'{tab.lower()}-{i:02d}')
    assert not backend.saves, 'Read-only review must not mutate generated documents'
    assert not errors,errors


@pytest.mark.parametrize('width,height',[(390,844),(768,1024),(1280,900),(1440,900)])
def test_returning_page_clears_proven_stale_label_in_map(page,width,height):
    original='screen "Projects" route=/projects\n  heading "Projects"\n\nscreen "Development" route=/development\n  heading "Development"\n'
    absent='screen "Development" route=/development\n  heading "Development"\n'
    stale,_=overlay(mark_flows(original),absent,revision=2)
    restored,summary=overlay(stale,original,revision=3)
    assert summary['restored']==1
    backend=NodeBackend(content=stale)
    _install_api(page,[]);page.route('**/api/**',backend.route)
    page.set_viewport_size({'width':width,'height':height})
    page.goto(PLAN_URL)
    page.get_by_role('button',name='Interfaces',exact=True).click()
    expect(page.locator('.iface-node').filter(has_text='Projects (stale)')).to_be_visible()
    capture(page,width,'stale')
    next(item for item in backend.items if item['item_type']=='wireframe')['content']=restored
    page.reload()
    expect(page.locator('.iface-node').filter(has_text='Projects')).to_be_visible()
    expect(page.locator('.iface-node').filter(has_text='Projects (stale)')).to_have_count(0)
    capture(page,width,'returned')


@pytest.mark.parametrize('width,height',[(390,844),(768,1024),(1280,900),(1440,900)])
def test_model_only_discovery_does_not_count_an_application_page(page,width,height):
    from distr.core.planning.project_scan import build_flows
    backend=NodeBackend(content=build_flows([],project_name='Model-only'))
    _install_api(page,[]);page.route('**/api/**',backend.route)
    page.set_viewport_size({'width':width,'height':height})
    page.goto(PLAN_URL)
    page.get_by_role('button',name='Interfaces',exact=True).click()
    # Only the guidance card exists, so it opens directly (no map, no page count, no summary banner).
    expect(page.locator('#plan-preview iframe')).to_have_count(1)
    expect(page.locator('.iface-map-status')).to_have_count(0)
    expect(page.locator('#plan-preview .plan-canvas-callout')).to_have_count(0)
    expect(page.locator('#plan-rebuild-sitemap')).to_be_visible()
    frame=page.frame_locator('#plan-preview iframe')
    assert frame.locator('.canvas').evaluate('''canvas=>{
      const bounds=canvas.getBoundingClientRect();
      const walker=document.createTreeWalker(canvas,NodeFilter.SHOW_TEXT);
      while(walker.nextNode()){
        const range=document.createRange();range.selectNodeContents(walker.currentNode);
        for(const r of range.getClientRects()) if(r.left<bounds.left-1 || r.right>bounds.right+1) return false;
      }
      return true;
    }'''), 'The guidance text must fit its responsive preview card'
    capture(page,width,'no-pages')
