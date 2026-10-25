"""Read bounded pytest JUnit receipts without treating collection or skips as passes."""

from __future__ import annotations

from xml.etree import ElementTree


def execution_counts(payload: bytes) -> dict[str, int]:
    if len(payload) > 2 * 1024 * 1024 or b"<!DOCTYPE" in payload or b"<!ENTITY" in payload:
        raise ValueError("Invalid independent pytest receipt")
    try:
        root = ElementTree.fromstring(payload)
    except ElementTree.ParseError as error:
        raise ValueError("Invalid independent pytest receipt") from error
    if root.tag not in {"testsuite", "testsuites"}:
        raise ValueError("Invalid independent pytest receipt")
    cases = root.findall(".//testcase")
    counts = {"tests": len(cases), "failed": 0, "errors": 0, "skipped": 0}
    for case in cases:
        counts["failed"] += int(case.find("failure") is not None)
        counts["errors"] += int(case.find("error") is not None)
        counts["skipped"] += int(case.find("skipped") is not None)
    return counts
