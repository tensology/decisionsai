"""Scanning may refresh owned projections, never replace human-authored scope."""
import contextlib
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from distr.core.db import Base
from distr.core.db.projects import Project
from distr.core.db.workflow import PlanItem, PlanItemRevision
from distr.core.planning import service


def _item(item_id):
    with service.get_session() as db:
        workspace_id = db.get(PlanItem, item_id).workspace_id
    return next(item for item in service.get_workspace(workspace_id)['items'] if item['id'] == item_id)


@pytest.fixture
def store(tmp_path, monkeypatch):
    engine = create_engine(f'sqlite:///{tmp_path / "isolated.db"}')
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    @contextlib.contextmanager
    def session():
        with factory() as db:
            yield db
    monkeypatch.setattr(service, 'get_session', session)
    monkeypatch.setattr(service, '_workspace_sitemap', lambda *args: None)
    monkeypatch.setattr(service, '_file_tree_content', lambda *args: '# Current file tree')
    monkeypatch.setattr('distr.core.planning.project_scan.rebuild_sitemap_cache', lambda *args: {})
    root = tmp_path / 'project'
    root.mkdir()
    (root / 'source.py').write_text('# Human source, untouched\n')
    with factory() as db:
        db.add(Project(id=1, name='Project', folder_location=str(root)))
        db.commit()
    workspace = service.ensure_workspace(board_key='decisions:1', board_provider='decisions', board_name='Project', project_id=1)
    def scan(artifacts, force=True):
        monkeypatch.setattr('distr.core.planning.project_scan.scan_project', lambda *args, **kwargs: {'artifacts': artifacts, 'summary': {'route_count': 1, 'model_count': 0}})
        return service.materialize_project_scan(workspace['id'], force=force)
    return factory, workspace, root, scan


@pytest.mark.parametrize('kind', ['brief', 'prd', 'frac', 'architecture', 'erd', 'file_structure'])
@pytest.mark.parametrize('force', [False, True])
def test_scan_preserves_first_revision_human_content_and_file(store, kind, force):
    factory, workspace, root, scan = store
    human = '# My requirement\nKeep it.'
    item = service.create_item(workspace_id=workspace['id'], item_type=kind, content=human, source='user')
    result = scan({kind: '# Generated replacement'}, force=force)
    assert kind not in result['updated']
    saved = _item(item['id'])
    assert saved['content'] == human and saved['revision_count'] == 1
    assert Path(item['file_path']).read_text() == human
    assert (root / 'source.py').read_text() == '# Human source, untouched\n'


def test_force_refreshes_proven_generated_content_and_scaffold_but_not_human_lineage(store):
    factory, workspace, root, scan = store
    owned = service.create_item(workspace_id=workspace['id'], item_type='prd', content='# Scan output', source='discovery')
    result = scan({'prd': '# Fresh source requirements'})
    assert 'prd' in result['updated']
    assert _item(owned['id'])['content'] == '# Fresh source requirements'
    edited = service.update_item(owned['id'], content='# Edited requirement', expected_revision=2, source='user')
    result = scan({'prd': '# Another scan'})
    assert 'prd' in result['skipped'] and 'prd' not in result['updated']
    assert _item(owned['id'])['content'] == edited['content']
    scaffold = service.create_item(workspace_id=workspace['id'], item_type='brief', source='scaffold')
    result = scan({'brief': '# Discovered outcome'}, force=False)
    assert 'brief' in result['updated']
    assert _item(scaffold['id'])['content'] == '# Discovered outcome'


@pytest.mark.parametrize('mode', ['unknown_source', 'missing_history', 'direct_db_edit', 'revision_gap', 'human_history'])
def test_missing_or_inconsistent_ownership_is_preserved(store, mode):
    factory, workspace, root, scan = store
    item = service.create_item(workspace_id=workspace['id'], item_type='prd', content='# Existing intent', source='legacy' if mode == 'unknown_source' else 'discovery')
    with factory() as db:
        revision = db.query(PlanItemRevision).filter_by(item_id=item['id']).one()
        if mode == 'missing_history':
            db.delete(revision)
        elif mode == 'direct_db_edit':
            db.get(PlanItem, item['id']).content = '# Edited directly'
        elif mode == 'revision_gap':
            revision.revision = 3
        elif mode == 'human_history':
            revision.source = 'user'
            row = db.get(PlanItem, item['id'])
            db.add(PlanItemRevision(item_id=item['id'], revision=2, title=row.title, content=row.content, status=row.status, source='discovery'))
        db.commit()
    before = _item(item['id'])['content']
    result = scan({'prd': '# Generated overwrite'})
    assert 'prd' in result['skipped'] and _item(item['id'])['content'] == before


def test_external_disk_edit_blocks_generated_refresh_without_db_or_file_loss(store):
    factory, workspace, root, scan = store
    item = service.create_item(workspace_id=workspace['id'], item_type='prd', content='# Generated original', source='discovery')
    path = Path(item['file_path'])
    path.write_text('# Human external edit')
    with pytest.raises(ValueError, match='changed outside Decisions'):
        scan({'prd': '# New generated overwrite'})
    assert _item(item['id'])['content'] == '# Generated original'
    assert _item(item['id'])['revision_count'] == 1
    assert path.read_text() == '# Human external edit'


def test_unmarked_first_revision_interfaces_are_handmade_not_replaceable_by_age(store):
    factory, workspace, root, scan = store
    previous = 'screen "Hand made" id=hand route="/hand"\n#| My saved page description.\n  heading "Human layout"\n# decisions-flow: {"v":1,"nodes":{"hand":{"note":"My saved note"}}}\n'
    item = service.create_item(workspace_id=workspace['id'], item_type='flows', content=previous, source='user')
    result = scan({'flows': 'screen "Generated" route="/generated"\n  heading "Generated layout"\n'})
    saved = _item(item['id'])['content']
    assert 'Human layout' in saved and 'route="/hand"' in saved
    assert 'Generated layout' in saved
    assert '#| My saved page description.' in saved and 'My saved note' in saved and 'id=hand' in saved


def test_concurrent_human_edit_after_scan_snapshot_is_not_overwritten(store, monkeypatch):
    factory, workspace, root, scan = store
    item = service.create_item(workspace_id=workspace['id'], item_type='prd', content='# Scan original', source='discovery')
    original = service._scan_owned_item_ids
    def edit_after_ownership_read(workspace_id):
        owned = original(workspace_id)
        service.update_item(item['id'], content='# Concurrent human edit', source='user', expected_revision=1)
        return owned
    monkeypatch.setattr(service, '_scan_owned_item_ids', edit_after_ownership_read)
    with pytest.raises(service.PlanRevisionConflict):
        scan({'prd': '# Stale generated replacement'})
    assert _item(item['id'])['content'] == '# Concurrent human edit'
    assert Path(item['file_path']).read_text() == '# Concurrent human edit'
