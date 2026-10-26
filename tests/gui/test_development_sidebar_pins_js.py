from pathlib import Path
import subprocess


def test_web_sidebar_migrates_both_pin_sets_then_follows_shared_state():
    source = Path('distr/gui/web/static/development/sidebar/index.js').resolve().as_uri()
    script = '''
import assert from 'node:assert/strict';
const { createSidebar } = await import(SOURCE);
const saved = new Map([['decisions-development-pinned-boards', '["jira:ABC"]']]);
globalThis.window = { location: { search: '' }, localStorage: {
  getItem: key => saved.get(key) || null, setItem: (key, value) => saved.set(key, value)
}};
const writes = [];
const sidebar = createSidebar({
  context: { boards: [{key:'decisions:13', project_id:9}] }, el: () => null,
  actions: { api: async (path, options) => {
    writes.push({path, ...options}); return options.body;
  }}
});
await sidebar.applyPinnedBoards({board_keys:null, legacy_project_ids:[9]});
assert.deepEqual(writes[0].body.board_keys, ['jira:ABC','decisions:13']);
assert.equal(writes[0].method, 'PUT');
await sidebar.applyPinnedBoards({board_keys:['trello:SHARED']});
assert.deepEqual(JSON.parse(saved.get('decisions-development-pinned-boards')), ['trello:SHARED']);
await sidebar.applyPinnedBoards({});
assert.deepEqual(JSON.parse(saved.get('decisions-development-pinned-boards')), ['trello:SHARED']);
assert.equal(writes.length,1);
'''.replace('SOURCE', repr(source))
    result = subprocess.run(['node', '--input-type=module', '-e', script], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_desktop_menu_dispatch_reuses_only_available_canonical_web_actions():
    source = Path('distr/gui/web/static/development/sidebar/index.js').resolve().as_uri()
    script = '''
import assert from 'node:assert/strict';
const {createSidebar} = await import(SOURCE);
globalThis.window = {location:{search:''},localStorage:{getItem:()=>null}};
const nodes = new Map(); const clicked=[]; const messages=[];
function el(id) {
 if (!nodes.has(id)) {
  const classes=new Set();
  nodes.set(id,{disabled:false,dataset:{},offsetWidth:100,offsetHeight:100,style:{},
   classList:{add:x=>classes.add(x),remove:x=>classes.delete(x),contains:x=>classes.has(x),toggle:(x,on)=>on?classes.add(x):classes.delete(x)},
   querySelector:()=>({textContent:'',innerHTML:''}),focus(){},click(){clicked.push(id);}});
 }
 return nodes.get(id);
}
const board={key:'decisions:18',provider:'decisions',project_id:9};
const sidebar=createSidebar({context:{boards:[board]},el,actions:{
 boardByKey:key=>key===board.key?board:null,projectById:()=>({folder_location:'/tmp'}),
 providerName:x=>x,systemFileManagerLabel:()=> 'Finder',getTerminalSessions:()=>[],
 loadProjectTerminalSessions:async()=>[],toast:x=>messages.push(x)
}});
await sidebar.runDesktopBoardAction({board_key:board.key,action:'edit'});
assert.deepEqual(clicked,['board-context-edit']);
await sidebar.runDesktopBoardAction({board_key:board.key,action:'arbitrary-script'});
assert.equal(clicked.length,1);
el('board-context-delete').disabled=true;
await sidebar.runDesktopBoardAction({board_key:board.key,action:'delete'});
assert.equal(clicked.length,1);
assert.equal(messages.length,1);
board.provider='jira';
await sidebar.runDesktopBoardAction({board_key:board.key,action:'archive'});
assert.equal(clicked.length,1);
await sidebar.runDesktopBoardAction({board_key:board.key,action:'terminal-toggle'});
assert.equal(clicked.at(-1),'board-context-terminal-toggle');
'''.replace('SOURCE', repr(source))
    result = subprocess.run(['node', '--input-type=module', '-e', script], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
