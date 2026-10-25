"""Conservative structural checks; these do not prove test semantics or isolation."""

from __future__ import annotations

import ast

from backend.app.core.llm_router import OutputValidationError

_DISABLED = {"skip", "skipif", "xfail", "importorskip"}


def _reachable_body(function: ast.FunctionDef | ast.AsyncFunctionDef) -> list[ast.stmt]:
    body: list[ast.stmt] = []
    for statement in function.body:
        if isinstance(statement, (ast.Return, ast.Raise)):
            break
        if not isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            body.append(statement)
    return body


def validate_test_structure(tree: ast.Module, *, production_modules: set[str]) -> None:
    """Reject disabled, mocked, uncollected, or observably vacuous acceptance tests.

    Assertions may live in a called helper or fixture. Reachability here is a
    static minimum-quality check, not proof that all runtime paths assert behavior.
    Fixed external acceptance tests remain necessary for reliability evaluation.
    """
    production_names: set[str] = set()
    pytest_names = {"pytest"}
    raises_names: set[str] = set()
    bindings: dict[str, ast.AST] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module.startswith(("unittest.mock", "pytest_mock")) or (
                module == "unittest" and any(a.name == "mock" for a in node.names)
            ):
                raise OutputValidationError("Independent acceptance tests cannot mock the delivery")
            if module.startswith(("pytest", "unittest")) and any(
                alias.name in _DISABLED for alias in node.names
            ):
                raise OutputValidationError("Acceptance tests cannot skip or xfail behavior")
            if module == "pytest":
                raises_names.update(a.asname or a.name for a in node.names if a.name == "raises")
            if module in production_modules:
                production_names.update(a.asname or a.name for a in node.names)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith(("unittest.mock", "pytest_mock")):
                    raise OutputValidationError("Acceptance tests cannot mock the delivery")
                if alias.name == "pytest":
                    pytest_names.add(alias.asname or alias.name)
                if alias.name in production_modules:
                    production_names.add(alias.asname or alias.name.split(".")[0])
        elif isinstance(node, ast.Attribute) and node.attr in _DISABLED:
            raise OutputValidationError("Acceptance tests cannot skip or xfail behavior")
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if any(a.arg in {"monkeypatch", "mocker"} for a in node.args.args):
                raise OutputValidationError("Acceptance tests cannot mock the delivery")

    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            bindings[node.name] = ast.Module(body=_reachable_body(node), type_ignores=[])
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if isinstance(target, ast.Name):
                    bindings[target.id] = node

    tests: list[ast.FunctionDef | ast.AsyncFunctionDef] = []
    scopes: list[ast.Module | ast.ClassDef] = [tree]
    scopes.extend(n for n in tree.body if isinstance(n, ast.ClassDef))
    for scope in scopes:
        names: set[str] = set()
        for node in scope.body:
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue
            if node.name in names:
                raise OutputValidationError("Use unique test/helper names in each scope")
            names.add(node.name)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith(
                "test_"
            ):
                if isinstance(scope, ast.ClassDef) and (
                    not scope.name.startswith("Test")
                    or any(
                        n.name == "__init__" for n in scope.body if isinstance(n, ast.FunctionDef)
                    )
                ):
                    raise OutputValidationError("Test classes must be collected by pytest")
                tests.append(node)
    if not tests:
        raise OutputValidationError("Provide executable pytest functions named test_...")

    for test in tests:
        nodes = list(ast.walk(ast.Module(body=_reachable_body(test), type_ignores=[])))
        pending = [n.id for n in nodes if isinstance(n, ast.Name)]
        pending.extend(a.arg for a in test.args.args)
        visited: set[str] = set()
        while pending:
            name = pending.pop()
            if name in visited:
                continue
            visited.add(name)
            if name in bindings:
                children = list(ast.walk(bindings[name]))
                nodes.extend(children)
                pending.extend(n.id for n in children if isinstance(n, ast.Name))
        assertions = [n for n in nodes if isinstance(n, ast.Assert)]
        raises = [
            n
            for n in nodes
            if isinstance(n, ast.Call)
            and (
                isinstance(n.func, ast.Name)
                and n.func.id in raises_names
                or isinstance(n.func, ast.Attribute)
                and n.func.attr == "raises"
                and isinstance(n.func.value, ast.Name)
                and n.func.value.id in pytest_names
            )
        ]
        meaningful = any(
            any(
                isinstance(n, (ast.Name, ast.Call, ast.Attribute, ast.Subscript))
                for n in ast.walk(assertion.test)
            )
            for assertion in assertions
        )
        if not (meaningful or raises) or not visited.intersection(production_names):
            raise OutputValidationError(
                "Every acceptance test must exercise a delivered public interface and assert "
                "observable results or expected exceptions; empty or constant tests are invalid"
            )
