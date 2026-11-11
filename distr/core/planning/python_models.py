"""Bounded SQLAlchemy inventory from supplied Python sources, never imports them.

Only verified SQLAlchemy constructors and declarative ancestry establish a
model. Literal table/column/ForeignKey evidence is retained with source lines.
Dynamic mappings, reflection, composite constraints and relationship() hints
are reported instead of being interpreted as database relationships.
"""
from __future__ import annotations

import ast
from collections import defaultdict
from pathlib import Path
from typing import Iterable

MAX_FILE_BYTES = 512 * 1024
MAX_TOTAL_BYTES = 32 * 1024 * 1024
MAX_FILE_NODES = 100_000
MAX_TOTAL_NODES = 4_000_000
MAX_DIAGNOSTICS = 300
_FACTORIES = {'sqlalchemy.orm.declarative_base', 'sqlalchemy.ext.declarative.declarative_base'}
_DECLARATIVE = {'sqlalchemy.orm.DeclarativeBase', 'sqlalchemy.orm.DeclarativeBaseNoMeta'}
_COLUMNS = {'sqlalchemy.Column', 'sqlalchemy.schema.Column', 'sqlalchemy.orm.mapped_column'}
_FOREIGN_KEYS = {'sqlalchemy.ForeignKey', 'sqlalchemy.schema.ForeignKey'}
_TYPE_MODULES = {'sqlalchemy', 'sqlalchemy.types', 'sqlalchemy.sql.sqltypes',
                 'sqlalchemy.dialects.postgresql', 'sqlalchemy.dialects.mysql', 'sqlalchemy.dialects.sqlite'}
_KINDS = {
    **dict.fromkeys(('Integer', 'BigInteger', 'SmallInteger', 'INT', 'INTEGER', 'BIGINT', 'SMALLINT'), 'int'),
    **dict.fromkeys(('String', 'Unicode', 'Text', 'UnicodeText', 'CHAR', 'VARCHAR', 'NVARCHAR', 'Enum',
                     'UUID', 'Uuid', 'JSON', 'JSONB', 'LargeBinary', 'BINARY', 'VARBINARY'), 'string'),
    **dict.fromkeys(('Numeric', 'DECIMAL', 'Float', 'Double', 'REAL', 'DOUBLE_PRECISION'), 'float'),
    **dict.fromkeys(('DateTime', 'Date', 'Time', 'TIMESTAMP', 'DATETIME', 'DATE', 'TIME'), 'date'),
    'Boolean':'bool', 'BOOLEAN':'bool',
}
_ANNOTATIONS = {'builtins.int':('Integer','int'), 'builtins.str':('String','string'),
                'builtins.float':('Float','float'), 'builtins.bool':('Boolean','bool'),
                'builtins.bytes':('LargeBinary','string'), 'datetime.datetime':('DateTime','date'),
                'datetime.date':('Date','date'), 'datetime.time':('Time','date'),
                'decimal.Decimal':('Numeric','float'), 'uuid.UUID':('Uuid','string')}
_BUILTINS = {key.split('.')[-1]:key for key in _ANNOTATIONS if key.startswith('builtins.')}


def _literal(node):
    return node.value if isinstance(node, ast.Constant) else None


class _Inventory:
    def __init__(self):
        self.bindings = {}
        self.exports = {}
        self.classes = []
        self.proof = {}
        self.diagnostics = []
        self._noted = set()
        self.truncated = False

    def note(self, code, source='', line=None, message=''):
        identity = (code,source,line,message)
        if identity in self._noted: return
        self._noted.add(identity)
        if len(self.diagnostics) < MAX_DIAGNOSTICS:
            self.diagnostics.append({'code':code, 'source':source, 'line':line, 'message':message})
        else:
            self.truncated = True

    def follow(self, token, trail=frozenset()):
        if token is None or token in trail or len(trail) > 40:
            return None
        if token in self.exports:
            return self.follow(self.exports[token], trail | {token})
        binding = self.bindings.get(token)
        if binding is None:
            return token
        if binding['kind'] == 'class':
            return '@class:' + token
        if binding['kind'] == 'alias':
            value = binding['value']
            if isinstance(value, ast.Call) and self.symbol(value.func, binding['env'], trail | {token}) in _FACTORIES:
                return '@base:' + token
            return self.symbol(value, binding['env'], trail | {token})
        return None

    def symbol(self, node, env, trail=frozenset()):
        if isinstance(node, ast.Name):
            token = env.get(node.id, _BUILTINS.get(node.id))
            return self.follow(token, trail)
        if isinstance(node, ast.Attribute):
            namespace = self.symbol(node.value, env, trail)
            if namespace and not namespace.startswith('@'):
                return self.follow(namespace + '.' + node.attr, trail)
        return None

    def module(self, module, tree, source):
        env = {}
        package = module if source.endswith('/__init__.py') or source == '__init__.py' else module.rpartition('.')[0]
        for node in tree.body:
            if isinstance(node, ast.Import):
                for alias in node.names:
                    env[alias.asname or alias.name.split('.')[0]] = alias.name if alias.asname else alias.name.split('.')[0]
            elif isinstance(node, ast.ImportFrom):
                prefix = node.module or ''
                if node.level:
                    parts = package.split('.') if package else []
                    if node.level > len(parts) + 1:
                        self.note('unresolved_import', source, node.lineno, 'Relative import escapes the supplied project module scope.')
                        continue
                    prefix = '.'.join([*parts[:len(parts)-node.level+1], *([prefix] if prefix else [])])
                for alias in node.names:
                    if alias.name == '*':
                        self.note('star_import', source, node.lineno, 'Star imports cannot prove constructor or Base identity.')
                    else:
                        env[alias.asname or alias.name] = '.'.join(filter(None, (prefix, alias.name)))
            elif isinstance(node, (ast.Assign, ast.AnnAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                for target in targets:
                    if isinstance(target, ast.Name):
                        key = f'{module}.{target.id}@{node.lineno}'
                        self.bindings[key] = {'kind':'alias', 'value':node.value, 'env':dict(env)}
                        env[target.id] = key
                    elif isinstance(target, ast.Attribute):
                        root = target
                        while isinstance(root, ast.Attribute): root = root.value
                        if isinstance(root, ast.Name):
                            namespace = self.symbol(root,env)
                            # Mutating a namespace constructor invalidates its
                            # provenance. A model's method/attribute mutation
                            # does not replace the model's class identity.
                            if namespace and namespace.startswith('sqlalchemy'):
                                env[root.id] = '@blocked'
            elif isinstance(node, ast.ClassDef):
                key = f'{module}.{node.name}@{node.lineno}'
                row = {'kind':'class', 'node':node, 'env':dict(env), 'module':module,
                       'source':source, 'qualified_name':'.'.join(filter(None, (module,node.name))), 'key':key}
                self.bindings[key] = row
                self.classes.append(row)
                env[node.name] = key
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                key = f'{module}.{node.name}@{node.lineno}'
                self.bindings[key] = {'kind':'blocked'}
                env[node.name] = key
            elif isinstance(node, (ast.If, ast.Try, ast.With, ast.For)):
                for child in ast.walk(node):
                    if isinstance(child, (ast.Import, ast.ImportFrom)):
                        mapping_symbols = {'Column','ForeignKey','declarative_base','DeclarativeBase','DeclarativeBaseNoMeta','Mapped','mapped_column'}
                        sql_import = (isinstance(child,ast.ImportFrom) and (child.module or '').startswith('sqlalchemy')
                                      and any(alias.name in mapping_symbols for alias in child.names)) or (
                            isinstance(child,ast.Import) and any(alias.name.startswith('sqlalchemy') for alias in child.names))
                        if sql_import:
                            self.note('conditional_import', source, child.lineno, 'Conditional SQLAlchemy imports are not assumed to execute.')
                        for alias in child.names:
                            env[alias.asname or alias.name.split('.')[0]] = '@blocked'
                # Conditional writes can shadow a proved constructor. Do not
                # retain the earlier binding when the selected branch is unknown.
                for child in ast.walk(node):
                    if isinstance(child, ast.Name) and isinstance(child.ctx, ast.Store): env[child.id] = '@blocked'
        for name, token in env.items():
            self.exports['.'.join(filter(None, (module,name)))] = token

    def orm(self, row, active=frozenset()):
        key = row['key']
        if key in self.proof: return self.proof[key]
        if key in active: return False
        if len(active) >= 40:
            self.note('inheritance_limit',row['source'],row['node'].lineno,'Declarative ancestry exceeds the bounded resolution depth.')
            self.truncated = True
            return False
        proved = False
        for base in row['node'].bases:
            symbol = self.symbol(base, row['env'])
            if symbol in _DECLARATIVE or (symbol or '').startswith('@base:'):
                proved = True
            elif (symbol or '').startswith('@class:'):
                proved = proved or self.orm(self.bindings[symbol[7:]], active | {key})
        self.proof[key] = proved
        return proved

    def mapped_annotation(self, node, env):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and len(node.value) <= 2000:
            try: node = ast.parse(node.value, mode='eval').body
            except (SyntaxError, ValueError, RecursionError): return None
        if not isinstance(node, ast.Subscript) or self.symbol(node.value, env) != 'sqlalchemy.orm.Mapped':
            return None
        return node.slice

    def annotation(self, node, env):
        value = self.mapped_annotation(node,env)
        if value is None: return None
        if isinstance(value, ast.Subscript) and self.symbol(value.value, env) in {'typing.Optional','typing.Union'}:
            value = value.slice
        if isinstance(value, ast.Tuple):
            values = [entry for entry in value.elts if not isinstance(entry, ast.Constant) or entry.value is not None]
            value = values[0] if len(values) == 1 else None
        if isinstance(value, ast.BinOp) and isinstance(value.op, ast.BitOr):
            options = [entry for entry in (value.left,value.right) if not isinstance(entry, ast.Constant) or entry.value is not None]
            value = options[0] if len(options) == 1 else None
        return _ANNOTATIONS.get(self.symbol(value,env))

    def members(self, row, visited=frozenset()):
        if row['key'] in visited: return {}
        if len(visited) >= 40:
            self.note('inheritance_limit',row['source'],row['node'].lineno,'Inherited fields exceed the bounded resolution depth.')
            self.truncated = True
            return {}
        result = {}
        for base in row['node'].bases:
            symbol = self.symbol(base,row['env'])
            if (symbol or '').startswith('@class:'):
                for name, member in self.members(self.bindings[symbol[7:]], visited | {row['key']}).items():
                    result.setdefault(name,member)
        env = dict(row['env'])
        for node in row['node'].body:
            if isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef,ast.ClassDef)):
                env[node.name] = '@blocked'
                result.pop(node.name,None)
            if not isinstance(node,(ast.Assign,ast.AnnAssign)): continue
            targets = node.targets if isinstance(node,ast.Assign) else [node.target]
            for target in targets:
                if not isinstance(target,ast.Name): continue
                result[target.id] = {'value':node.value,'annotation':getattr(node,'annotation',None),
                    'env':dict(env),'line':node.lineno,'source':row['source']}
                env[target.id] = '@blocked'
        return result

    def column(self, name, member):
        value,env = member['value'],member['env']
        constructor = self.symbol(value.func,env) if isinstance(value,ast.Call) else None
        inferred_annotation = self.annotation(member['annotation'],env)
        if constructor not in _COLUMNS and not (value is None and self.mapped_annotation(member['annotation'],env) is not None):
            return None
        # SQLAlchemy 2 permits an annotation-only Mapped scalar column.
        args = value.args if isinstance(value,ast.Call) else []
        keywords = value.keywords if isinstance(value,ast.Call) else []
        kind = None
        for arg in [*args, *[kw.value for kw in keywords if kw.arg == 'type_']]:
            symbol = self.symbol(arg.func if isinstance(arg,ast.Call) else arg,env)
            if symbol and symbol.rpartition('.')[0] in _TYPE_MODULES and symbol.rsplit('.',1)[-1] in _KINDS:
                kind = (symbol.rsplit('.',1)[-1],_KINDS[symbol.rsplit('.',1)[-1]])
                break
        kind = kind or inferred_annotation
        options = {kw.arg:kw.value for kw in keywords if kw.arg}
        foreign = []
        for arg in args:
            if isinstance(arg,ast.Call) and self.symbol(arg.func,env) in _FOREIGN_KEYS:
                target = arg.args[0] if arg.args else next((kw.value for kw in arg.keywords if kw.arg == 'column'),None)
                foreign.append(target)
        column_name = _literal(args[0]) if args and isinstance(_literal(args[0]),str) else name
        field = {'name':name,'column':column_name,'kind':kind[0] if kind else None,'mermaid':kind[1] if kind else None,
                 'primary_key':_literal(options['primary_key']) if 'primary_key' in options else False,
                 'source':member['source'],'line':member['line'],'_foreign':foreign,'_env':env}
        for option in ('nullable','unique'):
            if option in options and type(_literal(options[option])) is bool: field[option] = _literal(options[option])
        if type(field['primary_key']) is not bool:
            field['primary_key'] = None
            self.note('dynamic_primary_key',member['source'],member['line'],'Primary-key option is not a literal boolean.')
        return field


def scan_sqlalchemy_models(project_root: Path | str, files: Iterable[Path], *, with_fields=True,
                           max_files=2000, max_models=200, max_fields=80) -> dict:
    """Return models + explicit limitations from bounded, supplied first-party files.

    Output keeps the existing name/app/fields/relations/source contract. Additional
    qualified_name, table, line and target_qualified_name fields preserve identity
    and evidence. Duplicate public class names remain separate, never merged.
    """
    root = Path(project_root).expanduser().resolve()
    inventory = _Inventory()
    max_files,max_models,max_fields = (max(1,min(int(value),cap)) for value,cap in
                                      ((max_files,4000),(max_models,1000),(max_fields,500)))
    paths = set()
    for index, supplied in enumerate(files):
        if index >= 4000:
            inventory.note('input_limit',message='Supplied path enumeration exceeded 4000 entries.'); inventory.truncated=True; break
        raw = Path(supplied)
        raw = raw if raw.is_absolute() else root/raw
        resolved = raw.resolve()
        relative = raw.relative_to(root) if raw.is_relative_to(root) else None
        symlink = relative is not None and any((root/Path(*relative.parts[:index])).is_symlink() for index in range(1,len(relative.parts)+1))
        if not resolved.is_relative_to(root) or symlink:
            inventory.note('outside_scope',message='A symlink or source outside the selected root was excluded.'); continue
        if resolved.suffix == '.py': paths.add(resolved)
    if len(paths) > max_files:
        inventory.note('file_limit',message=f'Only {max_files} supplied Python files were inspected.'); inventory.truncated=True
    total_bytes,total_nodes,scanned = 0,0,0
    for path in sorted(paths)[:max_files]:
        source = path.relative_to(root).as_posix()
        try:
            with path.open('rb') as handle: data=handle.read(MAX_FILE_BYTES+1)
        except OSError:
            inventory.note('unreadable_source',source,message='The supplied source could not be read.'); continue
        if len(data) > MAX_FILE_BYTES:
            inventory.note('file_bytes_limit',source,message='Source exceeds the bounded parse size.'); inventory.truncated=True; continue
        total_bytes += len(data)
        if total_bytes > MAX_TOTAL_BYTES:
            inventory.note('total_bytes_limit',source,message='Total source byte budget reached.'); inventory.truncated=True; break
        try:
            tree=ast.parse(data,filename=source)
        except (SyntaxError,ValueError,RecursionError,MemoryError):
            inventory.note('unparsed_source',source,message='Source is invalid or too complex for a bounded Python AST.'); continue
        nodes=0
        for _ in ast.walk(tree):
            nodes+=1
            if nodes > MAX_FILE_NODES: break
        if nodes > MAX_FILE_NODES:
            inventory.note('file_ast_limit',source,message='Source exceeds the AST complexity limit.');inventory.truncated=True;continue
        total_nodes+=nodes
        if total_nodes > MAX_TOTAL_NODES:
            inventory.note('total_ast_limit',source,message='Total AST complexity budget reached.');inventory.truncated=True;break
        parts=list(path.relative_to(root).with_suffix('').parts)
        if parts[-1]=='__init__': parts.pop()
        module='.'.join(parts)
        inventory.module(module,tree,source);scanned+=1
    models=[]
    by_key={}
    for row in inventory.classes:
        members=inventory.members(row)
        own_names = {target.id for node in row['node'].body if isinstance(node,(ast.Assign,ast.AnnAssign))
                     for target in (node.targets if isinstance(node,ast.Assign) else [node.target]) if isinstance(target,ast.Name)}
        table=_literal(members.get('__tablename__',{}).get('value')) if '__tablename__' in own_names else None
        if not inventory.orm(row):
            if isinstance(table,str) and any(inventory.column(name,member) for name,member in members.items() if not name.startswith('_')):
                inventory.note('unverified_base',row['source'],row['node'].lineno,'Mapped-looking fields have no proved SQLAlchemy declarative ancestry.')
            continue
        # __abstract__ is a directive on this class, not inherited state.
        own_abstract=any(isinstance(node,ast.Assign) and any(isinstance(target,ast.Name) and target.id=='__abstract__' for target in node.targets)
                         and _literal(node.value) is True for node in row['node'].body)
        if own_abstract: continue
        if not isinstance(table,str) or not table.strip():
            if row['node'].name not in {'Base','Model'}:
                inventory.note('dynamic_or_inherited_table',row['source'],row['node'].lineno,'No literal mapped table was established; dynamic/single-table mappings are omitted.')
            continue
        if inventory.exports.get(row['qualified_name']) != row['key']:
            inventory.note('shadowed_model',row['source'],row['node'].lineno,'A later declaration replaced this public model name.');continue
        fields=[]
        for name,member in members.items():
            if name.startswith('_'):continue
            field=inventory.column(name,member)
            if field:fields.append(field)
            elif isinstance(member['value'],ast.Call) and inventory.symbol(member['value'].func,member['env']) in {'sqlalchemy.orm.relationship','sqlalchemy.orm.relation'}:
                inventory.note('relationship_not_inferred',member['source'],member['line'],'relationship() properties do not establish additional database foreign keys.')
        if len(models)>=max_models:
            inventory.note('model_limit',row['source'],row['node'].lineno,f'Model count exceeds {max_models}.');inventory.truncated=True;break
        model={'name':row['node'].name,'qualified_name':row['qualified_name'],'app':row['module'].rpartition('.')[0].split('.')[-1] or row['module'] or root.name,
               'table':table,'fields':fields,'relations':[],'source':row['source'],'line':row['node'].lineno,'_key':row['key']}
        table_args=members.get('__table_args__',{}).get('value')
        if isinstance(table_args,ast.Dict):
            schema=next((_literal(value) for key,value in zip(table_args.keys,table_args.values) if _literal(key)=='schema'),None)
            if isinstance(schema,str):model['schema']=schema
        for member in members.values():
            value=member['value']
            if value and any(isinstance(node,ast.Call) and (inventory.symbol(node.func,member['env']) or '').endswith('.ForeignKeyConstraint') for node in ast.walk(value)):
                inventory.note('composite_constraint',member['source'],member['line'],'Composite ForeignKeyConstraint relationships are not inferred.')
        models.append(model);by_key[row['key']]=model
    tables=defaultdict(list)
    names=defaultdict(list)
    for model in models:
        tables['.'.join(filter(None,(model.get('schema'),model['table'])))].append(model)
        names[model['name']].append(model)
    for name,duplicates in names.items():
        if len(duplicates)>1:inventory.note('duplicate_model_name',message=f'{name} has distinct qualified model identities; keep them separate when rendering.')
    for model in models:
        for field in model['fields']:
            for target in field['_foreign']:
                target_model,target_column=None,None
                object_target = False
                if isinstance(_literal(target),str) and '.' in _literal(target):
                    table_name,target_column=_literal(target).rsplit('.',1)
                    options=tables.get(table_name,[])
                    if len(options)==1:target_model=options[0]
                elif isinstance(target,ast.Attribute):
                    object_target = True
                    symbol=inventory.symbol(target.value,field['_env'])
                    if (symbol or '').startswith('@class:'):
                        target_model=by_key.get(symbol[7:]);target_column=target.attr
                if target_model is None:
                    inventory.note('unresolved_foreign_key',field['source'],field['line'],'Foreign-key target is dynamic, absent or ambiguous in the supplied inventory.');continue
                referenced=next((candidate for candidate in target_model['fields']
                                 if (candidate['name'] if object_target else candidate['column'])==target_column),None)
                if referenced is None:
                    inventory.note('unresolved_foreign_key',field['source'],field['line'],'Referenced column was not established in the supplied target model.');continue
                model['relations'].append({'field':field['name'],'kind':'ForeignKey','target':target_model['name'],
                    'target_qualified_name':target_model['qualified_name'],'target_column':target_column,
                    'source':field['source'],'line':field['line']})
                if not field['mermaid']:
                    inferred=referenced
                    if inferred and inferred['mermaid']:
                        field.update(kind=inferred['kind'],mermaid=inferred['mermaid'],type_source='foreign_key')
            if not field['mermaid']:inventory.note('unknown_scalar_type',field['source'],field['line'],'Column scalar type could not be established without executing project code.')
    # Resolve the complete inventory before applying display field limits.
    for model in models:
        fields=[{key:value for key,value in field.items() if not key.startswith('_')} for field in model['fields'] if field['mermaid']]
        if len(fields)>max_fields:
            inventory.note('field_limit',model['source'],model['line'],f'Only {max_fields} of {len(fields)} mapped scalar fields are returned.');inventory.truncated=True
        model['fields']=fields[:max_fields] if with_fields else []
        model.pop('_key',None)
    return {'models':models,'limitations':inventory.diagnostics,'files_scanned':scanned,'truncated':inventory.truncated}
