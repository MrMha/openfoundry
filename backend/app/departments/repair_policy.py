"""Reject known test-discriminating repair workarounds; not a Python security sandbox."""

from __future__ import annotations

import ast

from backend.app.core.llm_router import OutputValidationError


def reject_test_discrimination(tree: ast.Module) -> None:
    nodes = list(ast.walk(tree))
    strings = [n.value for n in nodes if isinstance(n, ast.Constant) and isinstance(n.value, str)]
    test_marker = any(
        marker in value.casefold()
        for value in strings
        for marker in ("test_", "pytest", "unittest", ".openfoundry-qa")
    )
    caller_inspection = any(
        isinstance(n, ast.Attribute)
        and n.attr
        in {"stack", "currentframe", "_getframe", "f_back", "co_filename", "extract_stack"}
        or isinstance(n, ast.ImportFrom)
        and n.module in {"inspect", "sys", "traceback"}
        and any(a.name in {"stack", "currentframe", "_getframe", "extract_stack"} for a in n.names)
        for n in nodes
    )
    test_environment = any("PYTEST_CURRENT_TEST" in value for value in strings)
    if test_environment or test_marker and caller_inspection:
        raise OutputValidationError(
            "Repair cannot inspect test filenames, caller frames or PYTEST_CURRENT_TEST to "
            "change behavior. The same public input must have the same behavior in tests and "
            "normal use. If frozen tests contradict SPEC.md, report that conflict instead of "
            "implementing a test-specific workaround."
        )
