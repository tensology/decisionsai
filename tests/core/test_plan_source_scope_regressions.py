"""A source inventory must not turn embedded starter projects into this app."""
from pathlib import Path

import pytest

from distr.core.planning import project_scan as scan


@pytest.mark.parametrize('child',['seed-corpus-pack/domains/store','reference/shop','vendor/shop','tests/fixtures/shop','examples/shop','installer/release/App.app/Contents/Resources'])
def test_embedded_ecommerce_routes_are_not_project_pages(tmp_path,child):
    embedded=tmp_path/child
    embedded.mkdir(parents=True)
    (embedded/'urls.py').write_text('path("checkout/", views.checkout),\n')
    (tmp_path/'urls.py').write_text('path("development/", views.development),\n')
    rows=scan.scan_frontend_routes(tmp_path)
    assert [row['url'] for row in rows]==['/development/']
    assert all(str(path.relative_to(tmp_path))=='urls.py' for path in scan.iter_sitemap_sources(tmp_path))


def test_explicit_example_project_root_is_still_a_real_project(tmp_path):
    root=tmp_path/'examples'/'shop';root.mkdir(parents=True)
    (root/'urls.py').write_text('path("checkout/", views.checkout),\n')
    assert scan.scan_frontend_routes(root)[0]['url']=='/checkout/'


def test_unsupported_fastapi_project_reports_no_complete_inventory(tmp_path):
    (tmp_path/'app.py').write_text('from fastapi import FastAPI\napp=FastAPI()\n@app.get("/development")\ndef page(): pass\n')
    result=scan.build_plan(tmp_path,project_name='Actual app')
    assert result['routes']==[]
    assert result['adequate'] is False
    assert result['summary']['coverage']=='none'
    assert any(gap['code']=='unsupported_inventory' for gap in result['residual_gaps'])
