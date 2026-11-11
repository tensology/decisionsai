"""Observed UI evidence must stay distinct from model-derived suggestions."""
from distr.core.planning import project_scan as scan
from tests.core.test_plan_python_inventory_integration import app_fixture


def test_html_controls_reach_brief_requirements_and_reported_evidence(tmp_path):
    app_fixture(tmp_path)
    result=scan.build_plan(tmp_path)
    assert result['summary']['cue_count']==1
    assert set(result['cues'])=={'/development'}
    assert '## UI surfaces inspected' in result['artifacts']['brief']
    frac=result['artifacts']['frac']
    assert 'provides a valid `Title`' in frac
    assert 'activates `Save project`' in frac
    assert 'Observed controls: `templates/development/index.html`' in frac


def route():
    return {'url':'/projects','name':'projects','title':'Projects','group':'Commerce','source':'routes.js'}


def models():
    return [{'name':'Project','app':'app','fields':[{'name':'internal_payload','kind':'Text','mermaid':'string'}],
             'relations':[],'source':'models.py'}]


def test_model_only_cues_do_not_become_observed_page_requirements_or_inspection(tmp_path):
    synthetic={'/projects':{'synthetic':True,'fields':['internal_payload'],'buttons':['Invented save'],'path':'models.py'}}
    frac=scan.build_frac(routes=[route()],models=models(),cues=synthetic)
    assert 'internal_payload' not in frac
    assert 'Invented save' not in frac
    assert 'Given the Projects form' not in frac
    brief=scan.build_brief(project_name='App',project_root=tmp_path,routes=[route()],models=models(),cues=synthetic)
    assert '## UI surfaces inspected' not in brief


def test_model_name_coincidence_does_not_invent_a_displayed_field():
    frac=scan.build_frac(routes=[route()],models=models(),cues={})
    assert 'internal_payload' not in frac
    assert 'is represented on the page' not in frac


def test_synthetic_cues_cannot_shadow_real_template_evidence(tmp_path):
    context={'project_name':'App','project_root':str(tmp_path),'routes':[route()],'models':models(),
        'template_cues':{'/projects':{'fields':['actual_title'],'buttons':['Publish'],'path':'templates/projects.html'}},
        'cues':{'/projects':{'synthetic':True,'fields':['internal_payload'],'buttons':['Invented save']}}}
    result=scan.render_artifacts(context)
    assert 'Actual title' in result['frac'] and 'Publish' in result['frac']
    assert 'internal_payload' not in result['frac'] and 'Invented save' not in result['frac']
    assert 'input "actual_title"' in result['flows']


def test_name_matched_binding_is_explicitly_a_suggestion():
    result=scan.build_frac(routes=[route()],models=models(),cues={'/projects':{'fields':['internal_payload']}})
    assert 'suggested binding' in result
    assert 'confirm before approval' in result
    assert ' bound to ' not in result


def test_planning_overview_does_not_claim_an_application_home_route():
    flows=scan.build_flows([route()],project_name='App')
    overview=flows.split('\n\n')[0]
    assert 'layer=guidance' in overview.splitlines()[0]
    assert 'route=' not in overview.splitlines()[0]
    assert 'route="/"' not in flows
    assert 'route="/projects"' in flows


def test_real_home_keeps_its_route_alongside_route_free_overview():
    home={**route(),'url':'/','title':'Home','name':'home','group':'Core'}
    flows=scan.build_flows([home],project_name='App')
    screens=[line for line in flows.splitlines() if line.startswith('screen ')]
    assert len(screens)==2
    assert 'route=' not in screens[0]
    assert screens[1].endswith('route="/"')


def test_two_real_sources_keep_all_controls_and_paths_without_mutating_inputs(tmp_path):
    import copy
    context={'project_name':'App','project_root':str(tmp_path),'routes':[route()],'models':[],
        'template_cues':{'/projects':{'fields':['server_title'],'buttons':['Publish'],'path':'templates/projects.html'}},
        'cues':{'/projects':{'fields':['client_filter'],'buttons':['Search'],'path':'frontend/projects.jsx'}}}
    original=copy.deepcopy(context)
    result=scan.render_artifacts(context)
    for text in ('Server title','Client filter','Publish','Search','templates/projects.html','frontend/projects.jsx'):
        assert text in result['frac']
    assert 'input "server_title"' in result['flows'] and 'input "client_filter"' in result['flows']
    assert context==original


def test_conflicting_source_types_are_exposed_not_silently_selected(tmp_path):
    context={'project_name':'App','project_root':str(tmp_path),'routes':[route()],'models':[],
        'template_cues':{'/projects':{'fields':['Title'],'types':{'Title':'email'},'regions':{'header':'Server'}}},
        'cues':{'/projects':{'fields':['Title'],'types':{'Title':'number'},'regions':{'header':'Client'}}}}
    cues=scan._observed_cues(context)
    assert cues['/projects']['fields']==['Title']
    assert cues['/projects']['types']=={} and cues['/projects']['regions']=={}
    assert set(cues['/projects']['evidence_conflicts'])=={'types:Title','regions:header'}
    assert 'Source observations disagree on types:Title, regions:header' in scan.render_artifacts(context)['frac']


def test_empty_discovery_is_guidance_not_a_discovered_page():
    first=scan.build_flows([],project_name='Model-only').splitlines()[0]
    assert 'layer=guidance' in first and 'route=' not in first


def test_imported_component_controls_keep_their_real_source_paths(tmp_path):
    from tests.core.test_plan_python_inventory_integration import write
    write(tmp_path,'frontend/src/routes.jsx','export const routes = [{url:"/projects",name:"projects",view:"projects"}];')
    write(tmp_path,'frontend/src/pages/projects.jsx','import Editor from "./editor"; export function Projects(){return <Editor />;}')
    write(tmp_path,'frontend/src/pages/editor.jsx','export default function Editor(){return <TextField label="Actual title" />;}')
    result=scan.build_plan(tmp_path)
    assert result['cues']['/projects']['paths']==['frontend/src/pages/projects.jsx','frontend/src/pages/editor.jsx']
    assert 'Observed controls: `frontend/src/pages/projects.jsx`, `frontend/src/pages/editor.jsx`' in result['artifacts']['frac']


def test_per_route_requirement_limits_are_explicit():
    fields=[f'Field {n}' for n in range(6)]
    result=scan.build_frac(routes=[route()],cues={'/projects':{'fields':fields,'buttons':['A','B','C']}})
    assert 'shows 4 of 6 recorded fields and 2 of 3 recorded actions' in result
    assert 'Review the remaining controls' in result


def test_html_form_cannot_end_discovery_before_react_source_is_inspected(tmp_path):
    from tests.core.test_plan_python_inventory_integration import write
    app_fixture(tmp_path)
    write(tmp_path,'frontend/src/routes.jsx','export const routes=[{url:"/development",name:"development",view:"development"}];')
    write(tmp_path,'frontend/src/pages/development.jsx','export function Page(){return <><TextField label="Filter" /><button>Search</button></>;}')
    result=scan.build_plan(tmp_path)
    assert result['cues']['/development']['fields']==['Title','Filter']
    assert result['cues']['/development']['buttons']==['Save project','Search']
    frac=result['artifacts']['frac']
    for value in ('Title','Filter','Save project','Search','frontend/src/pages/development.jsx','templates/development/index.html'):
        assert value in frac


def test_packaged_or_symlinked_page_roots_cannot_supply_component_evidence(tmp_path):
    from tests.core.test_plan_python_inventory_integration import write
    write(tmp_path,'installer/App.app/Contents/pages/projects.jsx','<TextField label="Contaminated" />')
    outside=tmp_path.parent/(tmp_path.name+'-external');outside.mkdir()
    write(outside,'projects.jsx','<TextField label="External" />')
    (tmp_path/'frontend/src').mkdir(parents=True)
    (tmp_path/'frontend/src/pages').symlink_to(outside,target_is_directory=True)
    assert scan.scan_page_cues(tmp_path,[route()])=={}


def test_unregistered_same_named_react_file_does_not_poison_python_template_evidence(tmp_path):
    from tests.core.test_plan_python_inventory_integration import write
    app_fixture(tmp_path)
    write(tmp_path,'frontend/src/pages/development.jsx','export function Page(){return <TextField label="Unused field" />;}')
    result=scan.build_plan(tmp_path)
    assert result['cues']['/development']['fields']==['Title']
    assert result['cues']['/development']['paths']==['templates/development/index.html']
    assert 'Unused field' not in result['artifacts']['frac']


import pytest


@pytest.mark.parametrize('target',['editor.jsx','editor/index.jsx','editor'])
def test_extension_or_index_resolution_cannot_follow_external_component_symlink(tmp_path,target):
    from tests.core.test_plan_python_inventory_integration import write
    page=write(tmp_path,'frontend/src/pages/projects.jsx','import Editor from "./editor"; export function Projects(){return <Editor />;}')
    outside=tmp_path.parent/(tmp_path.name+'-outside');outside.mkdir()
    source=write(outside,'index.jsx','<TextField label="Outside first party" />')
    link=page.parent/target
    link.parent.mkdir(parents=True,exist_ok=True)
    link.symlink_to(outside if target=='editor' else source,target_is_directory=target=='editor')
    assert scan._component_file(page,'./editor',tmp_path) is None
    assert scan.scan_page_cues(tmp_path,[{**route(),'view':'projects'}])=={}


def test_packaged_component_import_is_not_first_party_evidence(tmp_path):
    from tests.core.test_plan_python_inventory_integration import write
    page=write(tmp_path,'frontend/src/pages/projects.jsx','import Editor from "../../../release/App.app/editor"; export function Projects(){return <Editor />;}')
    write(tmp_path,'release/App.app/editor.jsx','<TextField label="Packaged editor" />')
    assert scan._component_file(page,'../../../release/App.app/editor',tmp_path) is None
