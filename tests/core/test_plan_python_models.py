"""SQLAlchemy inventory proves source evidence without importing project code."""
from pathlib import Path

import pytest

from distr.core.planning import python_models as scanner


def scan(tmp_path, sources, **options):
    paths = []
    for name, source in sources.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source)
        paths.append(path)
    return scanner.scan_sqlalchemy_models(tmp_path, paths, **options)


CLASSIC = '''from sqlalchemy import Column, Integer, String, ForeignKey
from sqlalchemy.orm import declarative_base
Base = declarative_base()
class User(Base):
    __tablename__ = 'users'
    id = Column(Integer, primary_key=True)
    label = Column('display_name', String(120), nullable=False)
class Item(Base):
    __tablename__ = 'items'
    id = Column(Integer, primary_key=True)
    owner_id = Column(ForeignKey('users.id'))
'''


def test_classic_fields_fk_inference_and_source_evidence(tmp_path):
    result = scan(tmp_path, {'models.py': CLASSIC})
    user, item = result['models']
    assert user['qualified_name'] == 'models.User'
    assert user['source'] == 'models.py' and user['line'] == 4
    assert user['fields'][0]['primary_key'] is True
    assert user['fields'][0]['line'] == 6
    assert user['fields'][1]['column'] == 'display_name'
    assert user['fields'][1]['nullable'] is False
    assert item['fields'][1]['mermaid'] == 'int'
    assert item['fields'][1]['type_source'] == 'foreign_key'
    assert item['relations'][0]['target_qualified_name'] == 'models.User'
    assert result['limitations'] == []


def test_sqlalchemy2_aliases_annotations_and_implicit_column(tmp_path):
    result = scan(tmp_path, {'models.py': '''import sqlalchemy as sa
from sqlalchemy.orm import DeclarativeBase as DB, mapped_column as mc, Mapped as M
from typing import Optional
from datetime import datetime
class Base(DB): pass
class Event(Base):
    __tablename__ = 'events'
    id: M[int] = mc(primary_key=True)
    title: M[Optional[str]] = mc()
    counter: M[int | None]
    created: M[datetime] = mc()
    active = sa.Column(sa.Boolean, default=False)
'''})
    model, = result['models']
    assert [(f['name'], f['mermaid']) for f in model['fields']] == [
        ('id','int'), ('title','string'), ('counter','int'), ('created','date'), ('active','bool')]
    assert not result['limitations']


def test_relative_reexports_and_declarative_base_alias(tmp_path):
    result = scan(tmp_path, {'app/__init__.py': 'from .base import Base\n',
        'app/base.py': 'from sqlalchemy.orm import declarative_base as factory\nBase = factory()\n',
        'app/models.py': '''from . import Base
import sqlalchemy.schema as schema
from sqlalchemy import Integer as Number
class Entry(Base):
    __tablename__ = 'entries'
    id = schema.Column(Number, primary_key=True)
'''})
    assert [m['qualified_name'] for m in result['models']] == ['app.models.Entry']
    assert result['models'][0]['fields'][0]['kind'] == 'Integer'


def test_abstract_mixins_supply_columns_but_are_not_models(tmp_path):
    result = scan(tmp_path, {'models.py': '''from sqlalchemy import Column, Integer
from sqlalchemy.orm import declarative_base
Base = declarative_base()
class Mixin:
    id = Column(Integer, primary_key=True)
class Abstract(Base):
    __abstract__ = True
    extra = Column(Integer)
class Real(Mixin, Abstract):
    __tablename__ = 'real'
class Inherited(Real): pass
'''})
    assert [m['name'] for m in result['models']] == ['Real']
    assert [f['name'] for f in result['models'][0]['fields']] == ['id', 'extra']
    assert 'dynamic_or_inherited_table' in {d['code'] for d in result['limitations']}


@pytest.mark.parametrize('shadow', [
    'def Column(*args, **kwargs): pass',
    'class Column: pass',
    'Column = lambda *args, **kwargs: None',
    'if UNKNOWN:\n    from impostor import Column',
])
def test_constructor_shadowing_cannot_invent_fields(tmp_path, shadow):
    source = CLASSIC.split('class User')[0] + shadow + '''
class Real(Base):
    __tablename__ = 'real'
    id = Column(Integer, primary_key=True)
'''
    model, = scan(tmp_path, {'models.py': source})['models']
    assert model['fields'] == []


def test_unrelated_pydantic_dataclass_and_local_base_are_excluded(tmp_path):
    result = scan(tmp_path, {'models.py': '''from dataclasses import dataclass
from pydantic import BaseModel
from sqlalchemy import Column, Integer
class Base: pass
class Fake(Base):
    __tablename__ = 'fake'
    id = Column(Integer)
class Validation(BaseModel):
    id: int
@dataclass
class Data:
    id: int
'''})
    assert result['models'] == []
    assert result['limitations'][0]['code'] == 'unverified_base'


def test_scan_never_executes_supplied_imports_or_top_level_code(tmp_path):
    marker = tmp_path / 'executed'
    source = f"raise RuntimeError('must never import')\nopen({str(marker)!r}, 'w').write('bad')\n" + CLASSIC
    assert len(scan(tmp_path, {'models.py': source})['models']) == 2
    assert not marker.exists()


def test_duplicate_public_names_remain_distinct_and_fk_identity_is_exact(tmp_path):
    result = scan(tmp_path, {'a.py': CLASSIC,
        'b.py': CLASSIC.split('class Item')[0].replace("'users'", "'other_users'")})
    users = [m for m in result['models'] if m['name'] == 'User']
    assert {m['qualified_name'] for m in users} == {'a.User','b.User'}
    assert result['models'][1]['relations'][0]['target_qualified_name'] == 'a.User'
    assert 'duplicate_model_name' in {d['code'] for d in result['limitations']}


@pytest.mark.parametrize('target', ['users.missing', 'absent.id', 'dynamic'])
def test_unproved_target_does_not_create_relationship(tmp_path, target):
    source = CLASSIC.replace("'users.id'", target if target == 'dynamic' else repr(target))
    result = scan(tmp_path, {'models.py': source})
    assert result['models'][1]['relations'] == []
    assert 'unresolved_foreign_key' in {d['code'] for d in result['limitations']}


def test_ambiguous_tables_do_not_guess_target(tmp_path):
    result = scan(tmp_path, {'a.py': CLASSIC, 'b.py': CLASSIC.split('class Item')[0]})
    assert result['models'][1]['relations'] == []


def test_schema_and_column_object_foreign_keys(tmp_path):
    source = CLASSIC.replace("__tablename__ = 'users'", "__tablename__ = 'users'\n    __table_args__ = {'schema': 'accounts'}")
    result = scan(tmp_path, {'models.py': source.replace("'users.id'", "'accounts.users.id'")})
    assert result['models'][0]['schema'] == 'accounts'
    assert result['models'][1]['relations'][0]['target_column'] == 'id'
    result = scan(tmp_path, {'models.py': source.replace("'users.id'", 'User.id')})
    assert result['models'][1]['relations'][0]['target_qualified_name'] == 'models.User'


def test_literal_fk_uses_database_column_name_while_object_uses_attribute(tmp_path):
    source = CLASSIC.replace("id = Column(Integer, primary_key=True)", "id = Column('user_key', Integer, primary_key=True)", 1)
    result = scan(tmp_path, {'models.py': source})
    assert not result['models'][1]['relations']
    for target in ("'users.user_key'", 'User.id'):
        result = scan(tmp_path, {'models.py': source.replace("'users.id'", target)})
        assert result['models'][1]['relations'][0]['target_qualified_name'] == 'models.User'


def test_class_method_shadow_and_later_table_method_update(tmp_path):
    source = CLASSIC + '''
User.__table__.drop = replacement
class Fake(User):
    __tablename__ = 'fake'
    def Column(*args): pass
    other = Column(Integer)
    def id(self): pass
'''
    result = scan(tmp_path, {'models.py': source})
    assert result['models'][0]['name'] == 'User'
    fake = result['models'][2]
    assert [f['name'] for f in fake['fields']] == ['label']


def test_deep_inheritance_is_bounded_and_reported(tmp_path):
    source = CLASSIC.split('class User')[0]
    source += '\n'.join(f'class Level{i}(Level{i-1}): pass' if i else 'class Level0(Base): pass' for i in range(1200))
    source += "\nclass Leaf(Level1199):\n    __tablename__ = 'leaves'\n    id = Column(Integer)\n"
    result = scan(tmp_path, {'models.py': source})
    assert isinstance(result['models'],list)
    # Resolution is iterative across cached proofs, but member traversal is
    # independently bounded even when thousands of ancestors were supplied.
    assert len(result['models']) <= 1
    assert result['truncated']
    assert 'inheritance_limit' in {d['code'] for d in result['limitations']}


def test_only_supplied_paths_and_no_symlink_or_escape(tmp_path):
    outside = tmp_path.parent / (tmp_path.name + '_outside.py')
    outside.write_text(CLASSIC)
    (tmp_path/'hidden.py').write_text(CLASSIC)
    link = tmp_path/'linked.py'
    link.symlink_to(outside)
    try:
        result = scanner.scan_sqlalchemy_models(tmp_path, [link, outside])
        assert result['models'] == [] and result['files_scanned'] == 0
        assert len(result['limitations']) == 1
        assert all(d['code'] == 'outside_scope' for d in result['limitations'])
    finally:
        outside.unlink()


def test_model_and_field_limits_explicit_and_do_not_break_fk_lookup(tmp_path):
    result = scan(tmp_path, {'models.py': CLASSIC}, max_fields=1)
    assert result['truncated']
    assert len(result['models'][0]['fields']) == 1
    assert result['models'][1]['relations'][0]['target_qualified_name'] == 'models.User'
    assert 'field_limit' in {d['code'] for d in result['limitations']}
    result = scan(tmp_path, {'models.py': CLASSIC}, max_models=1, with_fields=False)
    assert len(result['models']) == 1 and result['models'][0]['fields'] == []
    assert result['truncated']


@pytest.mark.parametrize('budget, value, source, code', [
    ('MAX_FILE_BYTES', 10, CLASSIC, 'file_bytes_limit'),
    ('MAX_FILE_NODES', 10, CLASSIC, 'file_ast_limit'),
    ('MAX_TOTAL_BYTES', 10, CLASSIC, 'total_bytes_limit'),
    ('MAX_TOTAL_NODES', 10, CLASSIC, 'total_ast_limit'),
])
def test_parse_resource_limits_are_explicit(tmp_path, monkeypatch, budget, value, source, code):
    monkeypatch.setattr(scanner, budget, value)
    result = scan(tmp_path, {'models.py': source})
    assert result['models'] == [] and result['truncated']
    assert result['limitations'][0]['code'] == code


def test_syntax_failure_and_file_limit_reported(tmp_path):
    result = scan(tmp_path, {'bad.py': 'class X(:', 'models.py': CLASSIC})
    assert len(result['models']) == 2
    assert result['limitations'][0]['code'] == 'unparsed_source'
    result = scan(tmp_path, {'a.py': CLASSIC, 'b.py': CLASSIC}, max_files=1)
    assert result['files_scanned'] == 1 and result['truncated']
    assert result['limitations'][0]['code'] == 'file_limit'


def test_unknown_implicit_mapped_scalar_and_composite_relationship_reported(tmp_path):
    source = '''from sqlalchemy import Column, Integer, ForeignKeyConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, relationship
class Base(DeclarativeBase): pass
class Record(Base):
    __tablename__ = 'records'
    id = Column(Integer, primary_key=True)
    custom: Mapped[Custom]
    children = relationship('Record')
    __table_args__ = (ForeignKeyConstraint(['id'], ['records.id']),)
'''
    result = scan(tmp_path, {'models.py': source})
    assert result['models'][0]['relations'] == []
    assert [f['name'] for f in result['models'][0]['fields']] == ['id']
    assert {d['code'] for d in result['limitations']} == {
        'unknown_scalar_type','relationship_not_inferred','composite_constraint'}
