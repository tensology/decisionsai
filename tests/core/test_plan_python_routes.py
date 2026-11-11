"""Source inventory proves declarations, never imports or runs the scanned app."""
from pathlib import Path

from distr.core.planning.python_routes import inventory_fastapi_routes


def scan(tmp_path, sources, **limits):
    files = []
    for name, body in sources.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body)
        files.append(path)
    return inventory_fastapi_routes(tmp_path, files, **limits)


def test_nested_app_aliases_templates_json_and_redirects(tmp_path):
    result = scan(tmp_path, {"app.py": '''
from fastapi import FastAPI as API
from fastapi.responses import HTMLResponse as HTML, RedirectResponse as Redirect
def create_app():
    app = API()
    @app.get('/page', response_class=HTML)
    async def page(request):
        return templates.TemplateResponse(request, 'page.html', {})
    @app.get('/old', response_class=HTML)
    def old():
        return Redirect('/page')
    @app.post('/api/items')
    def items():
        return {'ok': True}
    return app
'''})
    assert [(row['route'], row['template']) for row in result['pages']] == [('/page', 'page.html')]
    by_path = {row['route']: row for row in result['routes']}
    assert by_path['/old']['kind'] == 'redirect'
    assert by_path['/api/items']['kind'] == 'api'
    assert by_path['/api/items']['methods'] == ['POST']
    assert by_path['/page']['handler'] == 'create_app.page'
    assert by_path['/page']['source'] == 'app.py' and by_path['/page']['line'] > 0


def test_router_prefix_factory_and_relative_imports(tmp_path):
    result = scan(tmp_path, {
        'site/__init__.py': '',
        'site/pages.py': '''
from fastapi import APIRouter as Routes
def create_router(prefix='/pages'):
    router = Routes(prefix=prefix)
    @router.api_route('/hello', methods=['GET', 'POST'])
    def hello(request):
        return templates.TemplateResponse('hello.html', {'request': request})
    return router
''',
        'site/app.py': '''
import fastapi as web
from .pages import create_router as pages
def create_app():
    app = web.FastAPI()
    app.include_router(pages('/custom'), prefix='/v1')
    return app
''',
    })
    assert [(row['route'], row['methods'], row['template']) for row in result['pages']] == [('/v1/custom/hello', ['GET', 'POST'], 'hello.html')]
    assert len(result['pages'][0]['registration_evidence']) == 2


def test_registration_functions_and_static_module_loop(tmp_path):
    result = scan(tmp_path, {
        'feature.py': '''
def register_routes(router, templates):
    @router.get('/feature')
    def feature(request):
        return templates.TemplateResponse(name='feature.html', request=request)
''',
        'routes.py': '''
from fastapi import APIRouter
import feature as part
def create_router():
    router = APIRouter(prefix='/ui')
    for module in (part,):
        module.register_routes(router, None)
    return router
''',
        'app.py': '''
from fastapi import FastAPI
from routes import create_router
app = FastAPI()
app.include_router(create_router(), prefix='/site')
''',
    })
    assert [row['route'] for row in result['pages']] == ['/site/ui/feature']


def test_orphan_router_is_evidence_without_a_page(tmp_path):
    result = scan(tmp_path, {'orphan.py': '''
from fastapi import APIRouter
router = APIRouter(prefix='/orphan')
@router.get('/page')
def page(request):
    return templates.TemplateResponse(request, 'orphan.html', {})
'''})
    assert not result['pages']
    assert result['routes'][0]['registration'] == 'unregistered'
    assert result['routes'][0]['route'] is None
    assert result['routes'][0]['declared_path'] == '/page'


def test_dynamic_prefix_path_and_template_are_explicit(tmp_path):
    result = scan(tmp_path, {'app.py': '''
from fastapi import FastAPI, APIRouter
app = FastAPI()
router = APIRouter(prefix=environment_prefix())
@router.get('/page')
def page(request):
    return templates.TemplateResponse(request, 'page.html', {})
app.include_router(router)
@app.get(dynamic_path())
def unknown(request):
    return templates.TemplateResponse(request, 'other.html', {})
@app.get('/dynamic')
def dynamic(request):
    template = 'one.html' if request.query_params.get('mode') else 'two.html'
    return templates.TemplateResponse(request, template, {})
app.mount('/other', other_app)
'''})
    assert [row['route'] for row in result['pages']] == ['/dynamic']
    assert result['pages'][0]['dynamic_template'] is True
    reasons = ' '.join(row['reason'] for row in result['limitations'])
    assert 'prefix' in reasons and 'path' in reasons and 'template' in reasons and 'Mounted' in reasons


def test_nested_helpers_and_class_methods_do_not_supply_parent_template(tmp_path):
    result = scan(tmp_path, {'app.py': '''
from fastapi import FastAPI
app = FastAPI()
@app.get('/api')
def api(request):
    def helper():
        return templates.TemplateResponse(request, 'wrong.html', {})
    class Helper:
        def render(self):
            return templates.TemplateResponse(request, 'also-wrong.html', {})
    return {'ok': True}
class Views:
    @app.get('/class')
    def page(request):
        return templates.TemplateResponse(request, 'class.html', {})
'''})
    assert [(row['route'], row['template']) for row in result['pages']] == [('/class', 'class.html')]
    assert next(row for row in result['routes'] if row['route'] == '/api')['kind'] == 'api'


def test_scanned_imports_calls_and_writes_never_execute(tmp_path):
    marker = tmp_path / 'must-not-exist'
    result = scan(tmp_path, {'unsafe.py': f'''
from fastapi import FastAPI
import nonexistent_provider_sdk
from pathlib import Path
Path({str(marker)!r}).write_text('executed')
def never_called():
    raise RuntimeError('never run')
app = FastAPI()
@app.get('/safe')
def safe(request):
    return templates.TemplateResponse(request, 'safe.html', {{}})
'''})
    assert not marker.exists()
    assert [row['route'] for row in result['pages']] == ['/safe']


def test_reads_only_supplied_files_and_reports_budgets(tmp_path):
    (tmp_path / 'unlisted.py').write_text('this is not even Python')
    result = scan(tmp_path, {'app.py': 'from fastapi import FastAPI\napp = FastAPI()\n', 'large.py': '#' * 500}, max_file_bytes=100)
    assert result['stats']['files_scanned'] == 1
    assert result['stats']['files_omitted'] == 1
    assert len(result['limitations']) == 1
    assert 'large.py' in result['limitations'][0]['source']


def test_conditional_template_assignments_are_not_guessed(tmp_path):
    result = scan(tmp_path, {'app.py': '''
from fastapi import FastAPI
app = FastAPI()
@app.get('/page')
def page(request):
    if request.query_params.get('mode'):
        template = 'one.html'
    else:
        template = 'two.html'
    return templates.TemplateResponse(request, template, {})
'''})
    assert result['pages'][0]['template'] is None
    assert result['pages'][0]['templates'] == []
    assert result['pages'][0]['dynamic_template'] is True


def test_annotated_app_and_literal_html_response_class(tmp_path):
    result = scan(tmp_path, {'app.py': '''
from fastapi import FastAPI, responses
def create_app():
    app: FastAPI = FastAPI()
    @app.get('/page', response_class=responses.HTMLResponse)
    def page():
        return '<html><body>Real HTML</body></html>'
    return app
'''})
    assert [row['route'] for row in result['pages']] == ['/page']


def test_dynamic_include_and_invalid_prefix_are_unresolved(tmp_path):
    result = scan(tmp_path, {'app.py': '''
from fastapi import FastAPI, APIRouter
app = FastAPI()
router = APIRouter(prefix='/valid')
@router.get('/page')
def page(request):
    return templates.TemplateResponse(request, 'page.html', {})
app.include_router(router, prefix=unknown())
bad = APIRouter(prefix='not-absolute')
@bad.get('/other')
def other(request):
    return templates.TemplateResponse(request, 'other.html', {})
app.include_router(bad)
'''})
    assert not result['pages']
    assert {row['registration'] for row in result['routes']} == {'unresolved'}


def test_unreturned_app_and_shadowed_response_helper_are_not_pages(tmp_path):
    result = scan(tmp_path, {'app.py': '''
from fastapi import FastAPI
def unused():
    app = FastAPI()
    @app.get('/not-connected')
    def hidden(request):
        return templates.TemplateResponse(request, 'hidden.html', {})
    return 42
app = FastAPI()
def HTMLResponse(value):
    return {'value': value}
@app.get('/api')
def api():
    return HTMLResponse('not HTML')
'''})
    assert not result['pages']


def test_methods_path_constants_multiple_mounts_and_page_budget(tmp_path):
    result = scan(tmp_path, {'app.py': '''
from fastapi import FastAPI, APIRouter
PATH = '/page'
METHODS = ['GET', 'HEAD']
app = FastAPI()
router = APIRouter()
@router.api_route(PATH, methods=METHODS)
def page(request):
    return templates.TemplateResponse(request, name='page.html')
app.include_router(router, prefix='/one')
app.include_router(router, prefix='/two')
'''}, max_pages=1)
    assert len(result['pages']) == 1
    assert {row['route'] for row in result['routes']} == {'/one/page', '/two/page'}
    assert result['pages'][0]['methods'] == ['GET', 'HEAD']
    assert any('page budget' in row['reason'] for row in result['limitations'])


def test_false_branches_returns_and_raises_never_create_pages(tmp_path):
    result = scan(tmp_path, {'app.py': '''
from fastapi import FastAPI
app = FastAPI()
if False:
    @app.get('/false')
    def false_page(request):
        return templates.TemplateResponse(request, 'false.html', {})
def create_app():
    app = FastAPI()
    if True:
        return app
    @app.get('/after-return')
    def after_return(request):
        return templates.TemplateResponse(request, 'false.html', {})
raise RuntimeError('stop module registration')
@app.get('/after-raise')
def after_raise(request):
    return templates.TemplateResponse(request, 'false.html', {})
'''})
    assert not result['pages']
    assert all(row['registration'] != 'registered' for row in result['routes'])


def test_conditional_registration_and_mount_disclose_static_limits(tmp_path):
    result = scan(tmp_path, {'app.py': '''
from fastapi import FastAPI, APIRouter
app = FastAPI()
router = APIRouter()
@router.get('/mounted')
def mounted(request):
    return templates.TemplateResponse(request, 'mounted.html', {})
if settings.enabled:
    app.include_router(router, prefix='/optional')
    @app.get('/conditional')
    def conditional(request):
        return templates.TemplateResponse(request, 'conditional.html', {})
'''})
    assert {row['route'] for row in result['pages']} == {'/conditional', '/optional/mounted'}
    assert all(row['conditional'] for row in result['pages'])
    assert any('condition' in row['reason'] for row in result['limitations'])


def test_supplied_symlink_and_total_byte_budget_are_explicit(tmp_path):
    target = tmp_path / 'target.py'
    target.write_text('from fastapi import FastAPI\napp = FastAPI()\n')
    alias = tmp_path / 'alias.py'
    alias.symlink_to(target)
    result = inventory_fastapi_routes(tmp_path, [alias])
    assert result['stats']['files_scanned'] == 0
    assert result['stats']['files_omitted'] == 1
    result = inventory_fastapi_routes(tmp_path, [target], max_total_bytes=8)
    assert result['stats']['files_scanned'] == 0
    assert result['stats']['files_omitted'] == 1
    assert result['limitations']


def test_handler_response_receiver_does_not_activate_registration_helper(tmp_path):
    result = scan(tmp_path, {'app.py': '''
from fastapi import FastAPI, APIRouter
def router_helper():
    router = APIRouter()
    @router.get('/hidden')
    def hidden(request):
        return templates.TemplateResponse(request, 'hidden.html', {})
    return router
app = FastAPI()
@app.get('/api')
def api():
    return router_helper().unrelated_call()
'''})
    assert not result['pages']
    assert all(row['registration'] != 'registered' for row in result['routes'] if row['declared_path'] == '/hidden')


def test_try_return_skips_else_declarations(tmp_path):
    result = scan(tmp_path, {'app.py': '''
from fastapi import FastAPI
def create_app():
    app = FastAPI()
    try:
        return app
    except Exception:
        pass
    else:
        @app.get('/unreachable')
        def unreachable(request):
            return templates.TemplateResponse(request, 'hidden.html', {})
    return app
'''})
    assert not result['pages']


def test_async_router_factory_call_is_not_a_registered_router(tmp_path):
    result = scan(tmp_path, {'app.py': '''
from fastapi import FastAPI, APIRouter
async def make_router():
    router = APIRouter()
    @router.get('/not-awaited')
    def page(request):
        return templates.TemplateResponse(request, 'page.html', {})
    return router
app = FastAPI()
app.include_router(make_router())
'''})
    assert not result['pages']
    assert any('Async registration' in row['reason'] for row in result['limitations'])


def test_adversarial_expression_depth_is_bounded(tmp_path):
    prefix = ' + '.join(["'/prefix'"] * 300)
    result = scan(tmp_path, {'app.py': f'''
from fastapi import FastAPI, APIRouter
app = FastAPI()
router = APIRouter(prefix={prefix})
@router.get('/page')
def page(request):
    return templates.TemplateResponse(request, 'page.html', {{}})
app.include_router(router)
'''})
    assert not result['pages']
    assert any('depth budget' in row['reason'] for row in result['limitations'])


def test_actual_decisions_server_nested_html_routes():
    root = Path(__file__).resolve().parents[2]
    result = inventory_fastapi_routes(root, [root / 'distr/gui/web/server.py'])
    pages = {row['route']: row for row in result['pages']}
    assert pages['/development/']['template'] == 'workflows/studio.html'
    assert pages['/development/{development_path:path}']['template'] == 'workflows/studio.html'
    assert pages['/settings']['template'] == 'settings/settings.html'
    assert pages['/workflows/']['dynamic_template'] is True
    assert '/projects/' not in pages and '/kanban/' not in pages and '/health' not in pages
    assert result['stats']['files_scanned'] == 1
