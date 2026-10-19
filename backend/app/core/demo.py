"""Offline provider/research fixtures driving the real five-department workflow.

No global monkeypatches, SDK calls or public network requests. The local route uses
normal schema validation and usage ledger settlement with zero API-token cost.
"""

from __future__ import annotations

import asyncio
import json
from functools import partial
from typing import Any

import httpx

from backend.app.config import Settings
from backend.app.core.llm_router import LLMRouter
from backend.app.core.planning import MissionPlan
from backend.app.database import Database
from backend.app.departments._research_web import PublicWeb
from backend.app.departments.base_department import BaseDepartment
from backend.app.departments.chief_of_staff import ChiefOfStaff
from backend.app.departments.engineer import Engineer
from backend.app.departments.engineering_contracts import (
    BugDiagnosis,
    EngineeringResult,
    RepairResult,
)
from backend.app.departments.growth_career import GrowthCareer
from backend.app.departments.growth_contracts import GrowthResult
from backend.app.departments.qa_self_healer import QASelfHealer
from backend.app.departments.researcher import Researcher

DEMO_GOAL = (
    "Build a local integer calculator: research addition, implement a FastAPI homepage, "
    "test arithmetic and readiness, repair the seeded bug, and prepare launch drafts. "
    "This is an offline demonstration with deterministic provider responses."
)
APP = """from fastapi import FastAPI
from fastapi.responses import HTMLResponse

app = FastAPI()


def add(a: int, b: int) -> int:
    return a - b


@app.get("/", response_class=HTMLResponse)
def home() -> str:
    return "<h1>Calculator</h1><p>Broken</p>"
"""
FIXED_APP = APP.replace("return a - b", "return a + b").replace("Broken", "Ready")
TEST = "from app import add\n\n\ndef test_add():\n    assert add(2, 3) == 5\n"


def demo_outputs() -> dict[str, dict[str, Any]]:
    tasks = []
    previous: list[str] = []
    for key, department, title, description in [
        (
            "research",
            "researcher",
            "Research integer addition",
            json.dumps(
                {
                    "query": "integer addition",
                    "urls": ["https://example.com/addition"],
                    "prefer_browser": False,
                }
            ),
        ),
        ("implement", "engineer", "Build the calculator", "Implement the calculator and test."),
        (
            "verify",
            "qa_self_healer",
            "Verify and self-heal",
            "Run tests and DOM acceptance checks.",
        ),
        (
            "launch",
            "growth_career",
            "Prepare launch package",
            "Draft launch documentation and copy.",
        ),
    ]:
        tasks.append(
            dict(
                key=key,
                department=department,
                title=title,
                description=description,
                dependencies=previous,
                requires_approval=False,
            )
        )
        previous = [key]
    return {
        "MissionPlan": {
            "specification": "# Calculator\n\n## Goal\nAdd integers and show a Ready homepage.\n\n"
            "## Architecture\nFastAPI app.py with add(a, b) and a GET / HTML response.\n\n"
            "## Acceptance\nadd(2, 3) == 5; homepage contains Ready, h1 and p elements.\n\n"
            "## Demo\nSeeded arithmetic/readiness defects must be repaired once. "
            "Offline research and provider fixtures; no external publication.\n",
            "tasks": tasks,
        },
        "EngineeringResult": {
            "summary": "Implemented calculator source and an arithmetic regression test.",
            "files": [
                dict(file_path="app.py", content=APP, artifact_type="source"),
                dict(file_path="tests/test_app.py", content=TEST, artifact_type="test"),
            ],
            "verification": {
                "asgi_app": "app:app",
                "prefer_browser": False,
                "max_repairs": 3,
                "probes": [{"contains": "Ready", "selectors": ["h1", "p"]}],
            },
        },
        "BugDiagnosis": {
            "root_cause": "add subtracts b and the homepage still displays Broken.",
            "affected_files": ["app.py"],
            "fix_strategy": "Add b and display Ready.",
            "regression_test": "Preserve the original arithmetic assertion and DOM checks.",
        },
        "RepairResult": {
            "summary": "Corrected arithmetic and homepage readiness.",
            "files": [dict(file_path="app.py", content=FIXED_APP, artifact_type="source")],
        },
        "GrowthResult": {
            "mode": "launch",
            "summary": "Prepared calculator launch drafts after one QA repair.",
            "readme": "# Calculator\n\nOffline OpenFoundry self-healing demonstration.\n\n"
            "## Run\n`uvicorn app:app`\n\n## Verify\n`pytest -q` and `ruff check .`.\n\n"
            "QA recorded passing arithmetic and ASGI/DOM checks after one repair. "
            "The captured frame is a diagnostic card, not a real browser screenshot.\n",
            "diagram": {
                "nodes": [
                    {"key": "api", "label": "FastAPI homepage"},
                    {"key": "addition", "label": "Integer addition"},
                ],
                "edges": [{"source": "api", "target": "addition", "label": "calls"}],
            },
            "assets": [
                dict(
                    key="show_hn",
                    kind="show_hn",
                    title="Show HN draft",
                    content="Show HN: A local calculator built and repaired by OpenFoundry. "
                    "This offline demo records tests, a repair, and passing verification.",
                ),
                dict(
                    key="reddit",
                    kind="reddit",
                    title="Reddit draft",
                    content="An OpenFoundry demo caught an arithmetic bug and repaired it "
                    "while preserving its regression test. Feedback on the workflow is welcome.",
                ),
                dict(
                    key="x",
                    kind="x",
                    title="X draft",
                    content="Build → fail → diagnose → repair → pass. "
                    "An offline OpenFoundry calculator demo with recorded QA evidence.",
                ),
            ],
        },
    }


async def demo_completion(*, delay_seconds: float = 0.15, **kwargs: Any) -> dict[str, Any]:
    schema = kwargs["response_format"]["json_schema"]["schema"]
    title = schema.get("title")
    models = {
        model.__name__: model
        for model in (MissionPlan, EngineeringResult, BugDiagnosis, RepairResult, GrowthResult)
    }
    if title not in models:
        raise ValueError("Unsupported offline demo output schema")
    output = models[title].model_validate(demo_outputs()[title]).model_dump(mode="json")
    # Yield visibly between stages without delaying real test subprocesses.
    await asyncio.sleep(delay_seconds)
    return {
        "choices": [{"message": {"content": json.dumps(output)}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 240, "completion_tokens": 500},
    }


def demo_departments(settings: Settings, database: Database) -> dict[str, BaseDepartment]:
    local = settings.model_copy(
        update={
            "provider_order": ("ollama",),
            "ollama_base_url": "http://127.0.0.1:11434",
            "ollama_model": "ollama_chat/openfoundry-offline-demo",
            "max_output_tokens": 8192,
        }
    )
    router = LLMRouter(
        local,
        database,
        completion=partial(demo_completion, delay_seconds=settings.demo_step_delay_seconds),
    )

    def research(request: httpx.Request) -> httpx.Response:
        if str(request.url) != "https://example.com/addition":
            return httpx.Response(404)
        return httpx.Response(
            200,
            headers={"content-type": "text/html"},
            text=(
                "<title>Offline addition reference</title><main><h1>Integer addition</h1>"
                "<p>Addition combines integers. This source is a local demo fixture.</p></main>"
            ),
        )

    return {
        "chief_of_staff": ChiefOfStaff(router),
        "researcher": Researcher(router, web=PublicWeb(httpx.MockTransport(research))),
        "engineer": Engineer(router),
        "qa_self_healer": QASelfHealer(router),
        "growth_career": GrowthCareer(router),
    }
