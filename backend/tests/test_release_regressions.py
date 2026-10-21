"""Regressions discovered by fresh live and platform validation."""

import asyncio
import os
import sys

import pytest
from process_helpers import process_running

from backend.app.core.planning import WorkResult
from backend.app.departments._research_documents import parse_document
from backend.app.departments.engineering_contracts import EngineeringResult, editable_path


def test_process_liveness_probe_detects_this_process_without_signalling_it():
    assert process_running(os.getpid())
    assert not process_running(2**31 - 1)


@pytest.mark.parametrize("text", ["Café déjà vu", "中文 and emoji 🚀"])
async def test_document_worker_protocol_is_utf8_even_with_legacy_stdout(monkeypatch, text):
    original = asyncio.create_subprocess_exec

    async def legacy_stdout(*args, **kwargs):
        assert args[:3] == (sys.executable, "-I", "-m")
        wrapper = (
            "import sys; sys.stdout.reconfigure(encoding='cp1252'); "
            "from backend.app.departments._research_documents import _worker; _worker()"
        )
        return await original(sys.executable, "-I", "-c", wrapper, **kwargs)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", legacy_stdout)
    result = await parse_document(text.encode("utf-8"), "SPEC.md", prefer_docling=False)
    assert result.text == text


@pytest.mark.parametrize("path", ["spec.md", "Spec.md", "OPENFOUNDRY.QA.JSON", "QA/forged.json"])
def test_case_variants_cannot_overwrite_specification_or_evidence(path):
    with pytest.raises(ValueError):
        editable_path(path)


def test_case_colliding_generated_paths_are_rejected():
    with pytest.raises(ValueError):
        WorkResult.model_validate(
            {
                "summary": "ambiguous portable output",
                "files": [
                    {"file_path": path, "content": "", "artifact_type": "code"}
                    for path in ["app.py", "App.py"]
                ],
            }
        )


@pytest.mark.parametrize(
    "reference,factory",
    [("app:app", True), ("app:create_app", False)],
)
def test_asgi_reference_mismatch_is_rejected_before_plan_protection(reference, factory):
    with pytest.raises(ValueError, match="factory|instance"):
        EngineeringResult.model_validate(
            {
                "summary": "notes API",
                "files": [
                    {
                        "file_path": "app.py",
                        "artifact_type": "code",
                        "content": "from fastapi import FastAPI\n"
                        "def create_app():\n    return FastAPI()\napp = create_app()\n",
                    }
                ],
                "verification": {"asgi_app": reference, "app_factory": factory},
            }
        )


@pytest.mark.parametrize(
    "content,target",
    [
        ("def create_app():\n    return object()\n", "create_app"),
        ("from other_module import factory\n", "factory"),
        ("from functools import partial\nfactory = partial(object)\n", "factory"),
        ("factory = lambda: object()\n", "factory"),
        ("def make_factory():\n    return lambda: object()\nfactory = make_factory()\n", "factory"),
    ],
)
def test_factory_declarations_and_dynamic_exports_remain_supported(content, target):
    EngineeringResult.model_validate(
        {
            "summary": "factory",
            "files": [{"file_path": "app.py", "content": content, "artifact_type": "code"}],
            "verification": {"asgi_app": "app:" + target, "app_factory": True},
        }
    )
