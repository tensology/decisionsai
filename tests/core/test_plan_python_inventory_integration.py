"""A real Python web app must produce source-backed planning inventory."""
import json
from pathlib import Path

from distr.core.planning import project_scan as scan


def write(root, name, content):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    return path


def app_fixture(root):
    write(root, 'app.py', '''from fastapi import FastAPI
from starlette.templating import Jinja2Templates
from sqlalchemy import Column, Integer, String
from sqlalchemy.orm import declarative_base
import never_import_this_project
app = FastAPI()
templates = Jinja2Templates(directory="templates")
Base = declarative_base()
class Project(Base):
    __tablename__ = "projects"
    id = Column(Integer, primary_key=True)
    title = Column(String)
@app.get("/development/")
async def development(request):
    return templates.TemplateResponse(request, "development/index.html", {})
@app.get("/api/projects")
async def projects():
    return {"items": []}
''')
    write(root, 'templates/development/index.html', '<h1>Development</h1><label>Project title<input name="title"></label><button>Save project</button>')


def test_fastapi_pages_and_sqlalchemy_models_flow_into_saved_plan_candidates(tmp_path):
    app_fixture(tmp_path)
    result = scan.build_plan(tmp_path, project_name='Python app')
    assert {row['url'] for row in result['routes']} == {'/development'}
    assert [(row['name'], row['source']) for row in result['models']] == [('Project', 'app.py')]
    assert {'id', 'title'} <= {row['name'] for row in result['models'][0]['fields']}
    assert 'Project {' in result['artifacts']['architecture']
    assert 'route="/development"' in result['artifacts']['flows']
    assert '/api/projects' not in result['artifacts']['flows']
    assert result['summary']['coverage'] == 'partial'
    assert result['summary']['python_http_route_count'] == 2
    assert result['summary']['python_page_count'] == 1
    assert result['summary']['sqlalchemy_model_count'] == 1


def test_fastapi_sitemap_links_template_and_handler_with_source_evidence(tmp_path):
    app_fixture(tmp_path)
    model = scan.build_html_sitemap(tmp_path)
    page = next(row for row in model['nodes'] if row['kind'] == 'page')
    assert page['route'] == '/development'
    assert page['declared_route'] == '/development/'
    assert page['template'] == 'development/index.html'
    assert page['status'] == 'verified'
    assert page['source'] == 'app.py'
    assert page['line'] > 0
    assert not any(row.get('route') == '/api/projects' for row in model['nodes'])


def test_symlinked_sources_and_templates_are_never_read(tmp_path):
    root = tmp_path / 'app'; root.mkdir()
    outside = tmp_path / 'outside'; outside.mkdir()
    app_fixture(outside)
    (root / 'app.py').symlink_to(outside / 'app.py')
    (root / 'templates').mkdir()
    (root / 'templates' / 'stolen.html').symlink_to(outside / 'templates/development/index.html')
    assert scan.iter_sitemap_sources(root) == []
    assert scan.build_html_sitemap(root)['nodes'] == []


def test_old_cache_version_is_rebuilt_even_when_source_fingerprint_matches(tmp_path):
    app_fixture(tmp_path)
    cache = tmp_path / 'cache.json'
    cache.write_text(json.dumps({'version': 1, 'project_root': str(tmp_path),
        'fingerprint': scan._sitemap_fingerprint(tmp_path), 'model': {'nodes': [], 'coverage': 'verified'}}))
    model = scan.ensure_sitemap_cache(tmp_path, cache_path=cache)
    assert any(row.get('route') == '/development' for row in model['nodes'])
    assert json.loads(cache.read_text())['version'] == scan._SITEMAP_VERSION


def test_route_module_changes_invalidate_sitemap_fingerprint(tmp_path):
    app_fixture(tmp_path)
    before = scan._sitemap_fingerprint(tmp_path)
    assert 'app.py' in before
    source = tmp_path / 'app.py'
    source.write_text(source.read_text().replace('/development/', '/development-v2/'))
    assert scan._sitemap_fingerprint(tmp_path) != before


def test_home_page_is_not_lost_during_sitemap_widening(tmp_path):
    app_fixture(tmp_path)
    path=tmp_path/'app.py'
    path.write_text(path.read_text().replace('/development/', '/'))
    assert '/' in {row['url'] for row in scan.build_plan(tmp_path)['routes']}


def test_erd_limit_does_not_create_implicit_ghost_entities():
    models=[{'name':f'Model{n:03d}', 'app':'app', 'fields':[], 'relations':[]} for n in range(61)]
    models[0]['relations']=[{'field':'outside_id','kind':'ForeignKey','target':'Model060'}]
    models[60]['relations']=[{'field':'inside_id','kind':'ForeignKey','target':'Model000'}]
    diagram=scan.build_erd(models)
    assert 'Showing 60 of 61' in diagram
    assert 'Model060' not in diagram
    assert 'outside_id' not in diagram and 'inside_id' not in diagram


def test_source_inventory_drafts_do_not_invent_actors_or_approved_requirements(tmp_path):
    app_fixture(tmp_path)
    result=scan.build_plan(tmp_path)
    for kind in ('brief','prd','frac'):
        assert 'Draft from source discovery.' in result['artifacts'][kind]
    prd=result['artifacts']['prd']
    assert 'End customer / visitor' not in prd
    assert 'Actors and permissions are not established' in prd
    assert 'Confirm their intended constraints before approval' in prd
    assert '## Discovery coverage' in result['artifacts']['brief']
    assert 'not approved product requirements' in result['artifacts']['brief']
    assert 'Given a visitor' not in result['artifacts']['frac']
    assert 'intended actor and access rules are confirmed' in result['artifacts']['frac']


def test_real_pages_take_priority_over_unwired_templates_at_node_limit(tmp_path):
    app_fixture(tmp_path)
    for n in range(220):
        write(tmp_path, f'templates/unwired-{n}.html', f'<h1>Unwired template {n}</h1>')
    model=scan.build_html_sitemap(tmp_path)
    assert any(row.get('route')=='/development' for row in model['nodes'])
    assert len(model['nodes']) <= scan._MAX_SITEMAP_NODES
    assert model['omitted_node_count'] > 0
    assert model['coverage']=='incomplete'


def test_malformed_current_cache_is_rebuilt_instead_of_returned_to_ui(tmp_path):
    app_fixture(tmp_path)
    cache=tmp_path/'cache.json'
    cache.write_text(json.dumps({'version':scan._SITEMAP_VERSION, 'project_root':str(tmp_path),
        'fingerprint':scan._sitemap_fingerprint(tmp_path),
        'model':{'version':scan._SITEMAP_VERSION,'coverage':'verified','nodes':['bad'],'edges':[],'scaffolding':[]}}))
    model=scan.ensure_sitemap_cache(tmp_path, cache_path=cache)
    assert any(row.get('route')=='/development' for row in model['nodes'])


def test_duplicate_model_names_keep_readable_namespaces_and_exact_relations(tmp_path):
    write(tmp_path,'base.py','from sqlalchemy.orm import declarative_base\nBase=declarative_base()\n')
    write(tmp_path,'customer.py','''from sqlalchemy import Column, Integer
from base import Base
class Record(Base):
    __tablename__='customers'
    id=Column(Integer, primary_key=True)
''')
    write(tmp_path,'project.py','''from sqlalchemy import Column, Integer, ForeignKey
from base import Base
class Record(Base):
    __tablename__='projects'
    id=Column(Integer, primary_key=True)
    customer_id=Column(Integer, ForeignKey('customers.id'))
''')
    result=scan.build_plan(tmp_path)
    models={row['table']:row for row in result['models']}
    assert models['customers']['name']=='customer_Record'
    assert models['projects']['name']=='project_Record'
    assert models['projects']['relations'][0]['target']=='customer_Record'
    assert 'project_Record }o--|| customer_Record : customer_id' in result['artifacts']['architecture']
