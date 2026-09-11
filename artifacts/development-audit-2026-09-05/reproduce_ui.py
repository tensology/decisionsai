"""Execute the real Plan JS in a browser with an in-memory API. No live writes."""
import json
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent
HOST = r'''() => {
window.audit = {failSave:true, messages:[], writes:[]};
const items = [1,2].map(id=>({id,workspace_id:1,title:'Item '+id,content:'Original '+id,content_format:'markdown',status:'draft',revision_count:1}));
const workspace = {id:1,board_name:'Audit board',item_count:2,items};
window.DecisionsPlanHost = {
 escapeHtml: s => String(s??'').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;'),
 toast: (...args)=>audit.messages.push(args), openHome:()=>{},openBoardPlan:()=>{},
 api:async(path, options={})=>{
   if(path.endsWith('plan-workspaces')) return options.method==='POST'?{id:1}:{items:[]};
   if(path.includes('plan-items/') && options.method==='PATCH') {
     audit.writes.push({path,body:options.body});
     if(audit.failSave)throw new Error('409: Planning file changed outside Decisions AI');
     return {...items[Number(path.split('/').pop())-1],...options.body};
   }
   return structuredClone(workspace);
 }
};
}'''
with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page(viewport={'width':1100,'height':820})
    page.set_content('<div id="plan-root" style="height:800px"></div>')
    page.add_style_tag(path=str(ROOT/'distr/gui/web/static/workflows/css/studio.css'))
    page.evaluate(HOST)
    page.add_script_tag(path=str(ROOT/'distr/gui/web/static/workflows/js/plan.js'))
    page.evaluate('async()=>await DecisionsPlan.openBoard({key:"decisions:1",provider:"decisions",name:"Audit board",project_id:1},[{id:1}])')
    page.get_by_label('Plan item content', exact=True).fill('Unsaved important text')
    page.locator('[data-plan-item="2"]').click()
    page.locator('[data-plan-item="1"]').click()
    actual = page.get_by_label('Plan item content', exact=True).input_value()
    assert actual == 'Original 1'
    results = [{'case':'Failed save still permits item navigation and discards editor text','observed':actual,'toasts':page.evaluate('audit.messages')}]
    page.set_viewport_size({'width':390,'height':844})
    assert not page.locator('.plan-outline').is_visible()
    assert page.locator('[data-plan-item="2"]:visible').count() == 0
    results.append({'case':'Mobile hides every item selection control','observed':'Second item cannot be selected from UI'})
    page.screenshot(path=str(OUT/'plan-mobile-fixture.png'),full_page=True)
    page.set_viewport_size({'width':1100,'height':820})
    page.evaluate('''() => {
      const oldApi = DecisionsPlanHost.api;
      DecisionsPlanHost.api = async (path,options={}) => {
        if(path.endsWith('plan-workspaces') && !options.method) {
          await new Promise(r=>setTimeout(r,200)); return {items:[]};
        }
        return oldApi(path,options);
      };
    }''')
    page.evaluate('''async()=>{
      const home=DecisionsPlan.openHome([{key:'decisions:1',name:'Audit board',project_id:1}],[{id:1}]);
      await DecisionsPlan.openBoard({key:'decisions:1',provider:'decisions',name:'Audit board',project_id:1},[{id:1}]);
      await home;
    }''')
    assert page.locator('.plan-home').count()==1
    results.append({'case':'Slower Plan-home request overwrites already opened board workspace','observed':'Home painted last'})
    browser.close()
rendered = json.dumps(results,indent=2)
(OUT/'ui-evidence.json').write_text(rendered+'\n')
print(rendered)
