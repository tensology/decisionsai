"""Bounded, source-only FastAPI inventory. No project modules are imported or run.

``registered`` means a literal declaration is statically connected to a FastAPI
object (including an app-returning factory), not that the runtime was exercised.
Only the caller's filtered file list is read; unresolved evidence stays explicit.
"""
from __future__ import annotations

import ast
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


_UNKNOWN = object()
_VERBS = {"get", "post", "put", "patch", "delete", "head", "options", "trace"}
_FUNCTIONS = (ast.FunctionDef, ast.AsyncFunctionDef)


@dataclass(eq=False)
class _Env:
    values: dict = field(default_factory=dict)
    parent: Any = None

    def get(self, name):
        return self.values.get(name, self.parent.get(name) if self.parent else _UNKNOWN)


@dataclass(eq=False)
class _Function:
    node: Any
    module: str
    env: _Env
    qualname: str


@dataclass(eq=False)
class _Router:
    source: str
    line: int
    prefix: Any
    app: bool = False
    conditional: bool = False


@dataclass
class _Return:
    value: Any


class _Inventory:
    def __init__(self, root, files, *, max_files, max_routes, max_pages, max_file_bytes, max_ast_nodes, max_total_bytes):
        self.root = Path(root).resolve()
        self.files = files
        self.max_files, self.max_routes, self.max_pages = max_files, max_routes, max_pages
        self.max_file_bytes, self.max_ast_nodes = max_file_bytes, max_ast_nodes
        self.max_total_bytes, self.bytes_read = max_total_bytes, 0
        self.modules, self.envs, self.loading = {}, {}, set()
        self.routes, self.links, self.routers, self.limitations = [], [], [], []
        self.candidates, self.seen, self.stack = {}, set(), []
        self.operations, self.parsed, self.omitted, self.parse_errors = 0, 0, 0, 0
        self.ast_nodes = 0
        self.limit_keys = set()
        self.factory_checks = set()
        self.conditions = []
        self.expression_depth = 0

    def source(self, module):
        return self.modules[module][0]

    def note(self, module, node, reason):
        row = {"source": self.source(module) if module in self.modules else str(module),
               "line": node if isinstance(node, int) else getattr(node, "lineno", 0), "reason": reason}
        key = tuple(row.values())
        if key not in self.limit_keys and len(self.limitations) < 2000:
            self.limit_keys.add(key)
            self.limitations.append(row)

    def read(self):
        paths = []
        for supplied in self.files:
            path = Path(supplied)
            if not path.is_absolute():
                path = self.root / path
            if path.is_symlink():
                self.note(str(supplied), None, "Supplied symlink source file was omitted")
                self.omitted += 1
                continue
            try:
                resolved = path.resolve()
                relative = resolved.relative_to(self.root)
            except (OSError, ValueError):
                self.note(str(supplied), None, "File is outside the supplied project root")
                self.omitted += 1
                continue
            if relative.suffix == ".py":
                paths.append((relative.as_posix(), resolved))
        paths = sorted(dict(paths).items())
        if len(paths) > self.max_files:
            self.omitted += len(paths) - self.max_files
            self.note("", None, f"File budget omitted {len(paths) - self.max_files} supplied Python files")
        for index, (relative, path) in enumerate(paths[:self.max_files]):
            remaining = self.max_total_bytes - self.bytes_read
            if remaining <= 0:
                self.omitted += len(paths[:self.max_files]) - index
                self.note("", None, "Total Python source byte budget exceeded")
                break
            try:
                with path.open("rb") as stream:
                    data = stream.read(min(self.max_file_bytes + 1, remaining + 1))
                self.bytes_read += len(data)
                if len(data) > remaining:
                    raise ValueError("Total source byte budget exceeded")
                if len(data) > self.max_file_bytes:
                    raise ValueError("File byte budget exceeded")
                tree = ast.parse(data, filename=relative)
                count = sum(1 for _ in ast.walk(tree))
                if count > self.max_ast_nodes:
                    raise ValueError("File AST node budget exceeded")
                if self.ast_nodes + count > 4_000_000:
                    raise ValueError("Total AST node budget exceeded")
                self.ast_nodes += count
            except (OSError, SyntaxError, ValueError, RecursionError, MemoryError) as error:
                detail = str(error) if isinstance(error, ValueError) else type(error).__name__
                self.note(relative, None, f"Python source was not parsed: {detail}")
                self.omitted += 1
                self.parse_errors += int(isinstance(error, (SyntaxError, RecursionError, MemoryError)))
                continue
            module = relative[:-3].replace("/", ".")
            package = module.endswith(".__init__") or module == "__init__"
            if package:
                module = module.removesuffix(".__init__") if module != "__init__" else ""
            self.modules[module] = (relative, tree, package)
            self.parsed += 1
            self.collect(module, tree.body)

    def collect(self, module, statements, scope=""):
        for node in statements:
            if isinstance(node, _FUNCTIONS):
                name = f"{scope}.{node.name}".strip(".")
                for deco in node.decorator_list:
                    if isinstance(deco, ast.Call) and isinstance(deco.func, ast.Attribute) and deco.func.attr in _VERBS | {"api_route", "route"}:
                        self.candidates[(module, deco.lineno)] = (node, deco, name)
                self.collect(module, node.body, name)
            elif isinstance(node, ast.ClassDef):
                self.collect(module, node.body, f"{scope}.{node.name}".strip("."))
            else:
                for field_name in ("body", "orelse", "finalbody"):
                    self.collect(module, getattr(node, field_name, []), scope)
                for handler in getattr(node, "handlers", []):
                    self.collect(module, handler.body, scope)

    def ensure(self, module):
        if module in self.envs:
            return self.envs[module]
        if module not in self.modules:
            return _Env()
        if len(self.loading) >= 32:
            self.note(module, None, "Module linkage depth budget exceeded")
            return _Env()
        env = self.envs[module] = _Env()
        self.loading.add(module)
        self.run(self.modules[module][1].body, env, module)
        self.loading.discard(module)
        return env

    def resolve(self, value):
        for _ in range(12):
            if not (isinstance(value, tuple) and value and value[0] == "symbol"):
                return value
            _, module, name = value
            if module not in self.modules:
                return ("external", f"{module}.{name}")
            value = self.ensure(module).get(name)
        return _UNKNOWN

    def expr(self, node, env, module):
        self.expression_depth += 1
        try:
            if self.expression_depth > 64:
                self.note(module, node, "Static expression depth budget exceeded")
                return _UNKNOWN
            return self.expression(node, env, module)
        finally:
            self.expression_depth -= 1

    def expression(self, node, env, module):
        if node is None:
            return _UNKNOWN
        if isinstance(node, ast.Constant):
            return node.value
        if isinstance(node, ast.Name):
            return self.resolve(env.get(node.id))
        if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
            return [self.expr(value, env, module) for value in node.elts]
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            left, right = self.expr(node.left, env, module), self.expr(node.right, env, module)
            return left + right if isinstance(left, str) and isinstance(right, str) else _UNKNOWN
        if isinstance(node, ast.JoinedStr) and all(isinstance(value, ast.Constant) and isinstance(value.value, str) for value in node.values):
            return "".join(value.value for value in node.values)
        if isinstance(node, ast.Attribute):
            owner = self.expr(node.value, env, module)
            if isinstance(owner, tuple) and owner[0] == "module":
                target = f"{owner[1]}.{node.attr}".strip(".")
                return ("module", target) if target in self.modules else self.resolve(("symbol", owner[1], node.attr))
            if isinstance(owner, tuple) and owner[0] == "external":
                return ("external", f"{owner[1]}.{node.attr}")
            return _UNKNOWN
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Attribute) and node.func.attr == "include_router":
                parent = self.expr(node.func.value, env, module)
                child = self.expr(node.args[0], env, module) if node.args else self.expr(next((k.value for k in node.keywords if k.arg == "router"), None), env, module)
                prefix = self.keyword(node, "prefix", env, module, "")
                if isinstance(prefix, str) and prefix and (not prefix.startswith("/") or prefix.endswith("/")):
                    prefix = _UNKNOWN
                if isinstance(parent, _Router) and isinstance(child, _Router):
                    self.links.append((parent, child, prefix, self.source(module), node.lineno, bool(self.conditions)))
                if not isinstance(parent, _Router) or not isinstance(child, _Router) or not isinstance(prefix, str):
                    self.note(module, node, "Router inclusion or prefix cannot be resolved statically")
                return None
            if isinstance(node.func, ast.Attribute) and node.func.attr == "mount":
                self.note(module, node, "Mounted application or static surface is outside HTTP decorator inventory")
                return _UNKNOWN
            target = self.expr(node.func, env, module)
            if isinstance(target, tuple) and target[0] == "external" and target[1] in {"fastapi.FastAPI", "fastapi.APIRouter", "fastapi.routing.APIRouter"}:
                prefix = self.keyword(node, "prefix", env, module, "")
                app = target[1] == "fastapi.FastAPI"
                if app:
                    prefix = ""
                elif isinstance(prefix, str) and prefix and (not prefix.startswith("/") or prefix.endswith("/")):
                    prefix = _UNKNOWN
                router = _Router(self.source(module), node.lineno, prefix, app, bool(self.conditions))
                self.routers.append(router)
                if not isinstance(prefix, str):
                    self.note(module, node, "Router prefix is dynamic")
                return router
            if isinstance(target, _Function):
                args = [self.expr(arg, env, module) for arg in node.args]
                kwargs = {keyword.arg: self.expr(keyword.value, env, module) for keyword in node.keywords if keyword.arg}
                if any(isinstance(value, _Router) for value in args + list(kwargs.values())) or self.is_factory(target):
                    if isinstance(target.node, ast.AsyncFunctionDef):
                        self.note(module, node, "Async registration factory is not a synchronous router value")
                        return _UNKNOWN
                    return self.invoke(target, args, kwargs)
            return _UNKNOWN
        return _UNKNOWN

    def keyword(self, node, name, env, module, default=_UNKNOWN):
        value = next((keyword.value for keyword in node.keywords if keyword.arg == name), None)
        return self.expr(value, env, module) if value is not None else default

    def reference(self, node, env, module, depth=0):
        """Resolve names/attributes without analyzing any calls in a handler."""
        if depth >= 64:
            self.note(module, node, "Static reference depth budget exceeded")
            return _UNKNOWN
        if isinstance(node, ast.Name):
            return self.resolve(env.get(node.id))
        if isinstance(node, ast.Attribute):
            owner = self.reference(node.value, env, module, depth + 1)
            if isinstance(owner, tuple) and owner[0] == "module":
                return self.resolve(("symbol", owner[1], node.attr))
            if isinstance(owner, tuple) and owner[0] == "external":
                return ("external", f"{owner[1]}.{node.attr}")
        return _UNKNOWN

    def is_factory(self, function, *, app_only=False):
        key = (function.module, function.node.lineno, app_only)
        if key in self.factory_checks:
            return False
        self.factory_checks.add(key)
        constructors, returned = set(), set()
        found = False
        for node in self.direct_nodes(function.node.body):
            if isinstance(node, (ast.Assign, ast.AnnAssign)) and isinstance(node.value, ast.Call):
                target = self.reference(node.value.func, function.env, function.module)
                if target == ("external", "fastapi.FastAPI"):
                    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                    constructors.update(target.id for target in targets if isinstance(target, ast.Name))
            if isinstance(node, ast.Return) and isinstance(node.value, ast.Name):
                returned.add(node.value.id)
            if not isinstance(node, ast.Call):
                continue
            target = self.reference(node.func, function.env, function.module)
            allowed = {"fastapi.FastAPI"} if app_only else {"fastapi.FastAPI", "fastapi.APIRouter", "fastapi.routing.APIRouter"}
            if isinstance(target, tuple) and target[0] == "external" and target[1] in allowed:
                found = True
        self.factory_checks.discard(key)
        return found and (not app_only or bool(constructors & returned))

    @staticmethod
    def direct_nodes(statements):
        stack = list(reversed(statements))
        while stack:
            node = stack.pop()
            yield node
            if not isinstance(node, (*_FUNCTIONS, ast.ClassDef, ast.Lambda)):
                stack.extend(reversed(list(ast.iter_child_nodes(node))))

    def invoke(self, function, args, kwargs):
        key = (function.module, function.node.lineno)
        if key in self.stack or len(self.stack) >= 24:
            self.note(function.module, function.node, "Recursive or deeply nested registration is unresolved")
            return _UNKNOWN
        local = _Env(parent=function.env)
        params = function.node.args.posonlyargs + function.node.args.args
        defaults = [None] * (len(params) - len(function.node.args.defaults)) + list(function.node.args.defaults)
        for index, (param, default) in enumerate(zip(params, defaults)):
            local.values[param.arg] = args[index] if index < len(args) else kwargs.get(param.arg, self.expr(default, function.env, function.module))
        for param, default in zip(function.node.args.kwonlyargs, function.node.args.kw_defaults):
            local.values[param.arg] = kwargs.get(param.arg, self.expr(default, function.env, function.module))
        self.stack.append(key)
        result = self.run(function.node.body, local, function.module, function.qualname)
        self.stack.pop()
        return result.value if isinstance(result, _Return) else result

    def run(self, statements, env, module, scope=""):
        depth = len(self.conditions)
        try:
            return self.run_block(statements, env, module, scope)
        finally:
            del self.conditions[depth:]

    def run_block(self, statements, env, module, scope=""):
        for node in statements:
            self.operations += 1
            if self.operations > 200_000:
                self.note(module, node, "Registration analysis operation budget exceeded")
                return _UNKNOWN
            if isinstance(node, ast.Import):
                for alias in node.names:
                    env.values[alias.asname or alias.name.split(".")[0]] = ("module", alias.name if alias.asname else alias.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom):
                base = node.module or ""
                if node.level:
                    package = module if self.modules[module][2] else module.rpartition(".")[0]
                    pieces = package.split(".") if package else []
                    if node.level > max(1, len(pieces)):
                        self.note(module, node, "Relative import escapes the supplied package boundary")
                        continue
                    base = ".".join(pieces[:len(pieces) - node.level + 1] + ([base] if base else []))
                for alias in node.names:
                    target = f"{base}.{alias.name}".strip(".")
                    if alias.name == "*":
                        self.note(module, node, "Wildcard imports are not resolved")
                    else:
                        env.values[alias.asname or alias.name] = ("module", target) if target in self.modules else ("symbol", base, alias.name)
            elif isinstance(node, (ast.Assign, ast.AnnAssign)):
                value = self.expr(node.value, env, module)
                for target in node.targets if isinstance(node, ast.Assign) else [node.target]:
                    if isinstance(target, ast.Name):
                        env.values[target.id] = value
            elif isinstance(node, _FUNCTIONS):
                function = _Function(node, module, env, f"{scope}.{node.name}".strip("."))
                env.values[node.name] = function
                for deco in node.decorator_list:
                    if (module, deco.lineno) not in self.candidates:
                        continue
                    receiver = self.expr(deco.func.value, env, module)
                    self.record(function, deco, receiver, env)
            elif isinstance(node, ast.ClassDef):
                self.run(node.body, _Env(parent=env), module, f"{scope}.{node.name}".strip("."))
            elif isinstance(node, ast.Expr):
                self.expr(node.value, env, module)
            elif isinstance(node, ast.Return):
                return _Return(self.expr(node.value, env, module))
            elif isinstance(node, ast.Raise):
                return _Return(_UNKNOWN)
            elif isinstance(node, ast.If):
                test = self.expr(node.test, env, module)
                if isinstance(test, bool):
                    result = self.run(node.body if test else node.orelse, env, module, scope)
                    if isinstance(result, _Return):
                        return result
                else:
                    self.conditions.append((module, node.lineno))
                    first = self.run(node.body, _Env(dict(env.values), env.parent), module, scope)
                    result = self.run(node.orelse, _Env(dict(env.values), env.parent), module, scope)
                    self.conditions.pop()
                    if isinstance(first, _Return) and isinstance(result, _Return):
                        return _Return(first.value if first.value is result.value else _UNKNOWN)
                    if isinstance(first, _Return) or isinstance(result, _Return):
                        self.conditions.append((module, node.lineno))
            elif isinstance(node, ast.Try):
                self.conditions.append((module, node.lineno))
                result = self.run(node.body, env, module, scope)
                if not isinstance(result, _Return):
                    result = self.run(node.orelse, env, module, scope)
                final_result = self.run(node.finalbody, env, module, scope)
                if isinstance(final_result, _Return):
                    result = final_result
                self.conditions.pop()
                # Error handlers are alternative registration paths, not unconditional declarations.
                if any((module, getattr(child, "lineno", 0)) in self.candidates for handler in node.handlers for child in self.direct_nodes(handler.body)):
                    self.note(module, node, "Exception-only route registrations require runtime resolution")
                if isinstance(result, _Return):
                    return result
            elif isinstance(node, (ast.For, ast.AsyncFor)):
                values = self.expr(node.iter, env, module)
                if isinstance(values, list) and len(values) <= 64 and isinstance(node.target, ast.Name):
                    for value in values:
                        local = _Env(dict(env.values), env.parent)
                        local.values[node.target.id] = value
                        result = self.run(node.body, local, module, scope)
                        if isinstance(result, _Return):
                            return result
                else:
                    self.note(module, node, "Dynamic registration loop is not expanded")
            elif isinstance(node, (ast.With, ast.AsyncWith)):
                result = self.run(node.body, env, module, scope)
                if isinstance(result, _Return):
                    return result
        return _UNKNOWN

    def handler_info(self, function, response_html=False):
        env = _Env(parent=function.env)
        templates, dynamic, kinds = set(), False, set()
        conditional_assignments = set()
        for parent in self.direct_nodes(function.node.body):
            if isinstance(parent, (ast.If, ast.For, ast.While, ast.Try, ast.Match)):
                for child in self.direct_nodes([parent]):
                    if isinstance(child, (ast.Assign, ast.AnnAssign)):
                        conditional_assignments.add(id(child))
        for node in self.direct_nodes(function.node.body):
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                # Handler analysis only resolves literal expressions, never calls helpers.
                value = _UNKNOWN if id(node) in conditional_assignments else self.literal(node.value, env)
                for target in node.targets if isinstance(node, ast.Assign) else [node.target]:
                    if isinstance(target, ast.Name):
                        env.values[target.id] = value
            if not isinstance(node, ast.Return):
                continue
            value = node.value
            if isinstance(value, ast.Await):
                value = value.value
            if isinstance(value, ast.Call):
                name = value.func.attr if isinstance(value.func, ast.Attribute) else value.func.id if isinstance(value.func, ast.Name) else ""
                target = self.reference(value.func, env, function.module)
                if isinstance(target, tuple) and target[0] == "external":
                    name = target[1].split(".")[-1]
                elif isinstance(target, _Function):
                    name = ""  # A project helper named HTMLResponse is not framework evidence.
                if name == "TemplateResponse":
                    kinds.add("html")
                    template_node = next((k.value for k in value.keywords if k.arg == "name"), None)
                    if template_node is None and value.args:
                        template_node = value.args[0] if isinstance(self.literal(value.args[0], env), str) else value.args[1] if len(value.args) > 1 else None
                    template = self.literal(template_node, env)
                    if isinstance(template, str):
                        templates.add(template)
                    else:
                        dynamic = True
                elif name in {"RedirectResponse", "Redirect"}:
                    kinds.add("redirect")
                elif name == "HTMLResponse":
                    kinds.add("html")
                elif name == "JSONResponse" or isinstance(value, (ast.Dict, ast.List)):
                    kinds.add("api")
            elif isinstance(value, (ast.Dict, ast.List, ast.Tuple)):
                kinds.add("api")
            elif response_html and isinstance(self.literal(value, env), str):
                kinds.add("html")
        return templates, dynamic, kinds

    def literal(self, node, env, depth=0):
        if depth >= 64:
            return _UNKNOWN
        if isinstance(node, ast.Constant):
            return node.value
        if isinstance(node, ast.Name):
            value = env.get(node.id)
            return value if isinstance(value, (str, bool, int, float)) else _UNKNOWN
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            a, b = self.literal(node.left, env, depth + 1), self.literal(node.right, env, depth + 1)
            return a + b if isinstance(a, str) and isinstance(b, str) else _UNKNOWN
        return _UNKNOWN

    def record(self, function, deco, receiver, env):
        self.seen.add((function.module, deco.lineno))
        if len(self.routes) >= self.max_routes:
            self.note(function.module, deco, "HTTP route budget exceeded")
            return
        path = self.expr(deco.args[0], env, function.module) if deco.args else self.keyword(deco, "path", env, function.module)
        methods = [deco.func.attr.upper()] if deco.func.attr in _VERBS else self.keyword(deco, "methods", env, function.module, ["GET"])
        if not isinstance(methods, list) or any(not isinstance(method, str) or method.lower() not in _VERBS for method in methods):
            methods = []
            self.note(function.module, deco, "HTTP methods are dynamic")
        response_class = self.keyword(deco, "response_class", env, function.module)
        response_html = isinstance(response_class, tuple) and response_class[0] == "external" and response_class[1] in {"fastapi.responses.HTMLResponse", "starlette.responses.HTMLResponse"}
        templates, dynamic, kinds = self.handler_info(function, response_html)
        if dynamic:
            self.note(function.module, function.node, "Handler template is dynamic")
        if not isinstance(path, str) or not path.startswith("/"):
            path = None
            self.note(function.module, deco, "HTTP path is dynamic or not an absolute literal")
        if not isinstance(receiver, _Router):
            self.note(function.module, deco, "HTTP decorator receiver or declaration scope is unresolved")
        for conditional_module, line in self.conditions:
            self.note(conditional_module, line, "HTTP registration depends on a condition or successful try block")
        kind = "redirect" if kinds == {"redirect"} else "html" if kinds == {"html"} else "api" if kinds == {"api"} else "unknown"
        self.routes.append({"declared_path": path, "methods": sorted(set(method.upper() for method in methods)),
                            "template": next(iter(templates)) if len(templates) == 1 else None,
                            "templates": sorted(templates), "dynamic_template": dynamic, "kind": kind,
                            "source": self.source(function.module), "line": deco.lineno,
                            "handler": function.qualname, "conditional": bool(self.conditions), "_router": receiver})

    def finish(self):
        for module in sorted(self.modules):
            self.ensure(module)
        # A source-defined function that constructs and returns its own FastAPI app is an app root.
        for module, env in list(self.envs.items()):
            for value in list(env.values.values()):
                if isinstance(value, _Function) and self.is_factory(value, app_only=True):
                    if isinstance(value.node, ast.AsyncFunctionDef):
                        self.note(module, value.node, "Async app factory is not assumed to be an instantiated app root")
                    else:
                        self.invoke(value, [], {})
        for (module, line), (node, deco, name) in sorted(self.candidates.items()):
            if (module, line) not in self.seen:
                self.record(_Function(node, module, self.ensure(module), name), deco, _UNKNOWN, self.ensure(module))
        mounted = {}
        connected = {router for router in self.routers if router.app}
        for _ in range(24):
            children = {child for parent, child, *_ in self.links if parent in connected}
            if children <= connected:
                break
            connected.update(children)
        def visit(router, prefix, evidence, visited):
            if len(mounted) >= self.max_routes:
                self.note(router.source, None, "Router mounting budget exceeded")
                return
            if router in visited or len(visited) >= 24:
                return
            if not isinstance(router.prefix, str):
                return
            full = prefix + router.prefix
            key = (router, full)
            if key in mounted:
                return
            mounted[key] = evidence
            for parent, child, include_prefix, source, line, conditional in self.links:
                if parent is router:
                    if isinstance(include_prefix, str):
                        visit(child, full + include_prefix, evidence + [{"source": source, "line": line, "conditional": conditional or child.conditional}], visited | {router})
        for router in self.routers:
            if router.app:
                visit(router, "", [{"source": router.source, "line": router.line, "conditional": router.conditional}], set())
        output = []
        seen = set()
        for original in self.routes:
            if len(output) >= self.max_routes:
                self.note("", None, "Expanded HTTP route budget exceeded")
                break
            row = dict(original)
            router = row.pop("_router")
            mounts = [(prefix, evidence) for (candidate, prefix), evidence in mounted.items() if candidate is router]
            if not mounts:
                row.update(route=None, registration="unregistered" if isinstance(router, _Router) and router not in connected else "unresolved", registration_evidence=[])
                output.append(row)
                continue
            for prefix, evidence in mounts:
                result = {**row, "route": prefix + row["declared_path"] if row["declared_path"] is not None else None,
                          "registration": "registered" if row["declared_path"] is not None and row["methods"] else "unresolved", "registration_evidence": evidence,
                          "conditional": row["conditional"] or any(entry["conditional"] for entry in evidence)}
                if result["conditional"]:
                    for entry in evidence:
                        if entry["conditional"]:
                            self.note(entry["source"], entry["line"], "HTTP registration has conditional app or router linkage")
                key = (result["source"], result["line"], result["route"])
                if key not in seen:
                    seen.add(key)
                    output.append(result)
                    if len(output) >= self.max_routes:
                        break
        output.sort(key=lambda row: (row["source"], row["line"], row["route"] or ""))
        pages = [row for row in output if row["registration"] == "registered" and row["kind"] == "html" and row["methods"]]
        if len(pages) > self.max_pages:
            self.note("", None, f"HTML page budget omitted {len(pages) - self.max_pages} rows")
        return {"pages": pages[:self.max_pages], "routes": output[:self.max_routes], "limitations": self.limitations,
                "stats": {"files_scanned": self.parsed, "files_omitted": self.omitted, "parse_errors": self.parse_errors, "bytes_read": self.bytes_read, "ast_nodes": self.ast_nodes, "http_routes": len(output),
                          "html_pages": min(len(pages), self.max_pages), "unregistered_routes": sum(row["registration"] == "unregistered" for row in output),
                          "unresolved_routes": sum(row["registration"] == "unresolved" for row in output)}}


def inventory_fastapi_routes(root: Path, python_files: list[Path], *, max_files: int = 2500,
                             max_routes: int = 2000, max_pages: int = 200,
                             max_file_bytes: int = 2_000_000, max_ast_nodes: int = 200_000,
                             max_total_bytes: int = 64_000_000) -> dict:
    """Read only supplied first-party paths and return static pages, routes and coverage limits.

    Callers own first-party filtering. Files outside ``root`` are rejected, as are
    oversized/unparseable files. Total AST nodes are capped at four million.
    No filesystem walk, import, eval or cache write.
    Dynamic expressions, arbitrary helper calls and mounted sub-apps are not guessed.
    """
    scan = _Inventory(root, python_files, max_files=max_files, max_routes=max_routes,
                      max_pages=max_pages, max_file_bytes=max_file_bytes, max_ast_nodes=max_ast_nodes, max_total_bytes=max_total_bytes)
    scan.read()
    return scan.finish()
