"""Grounded launch/career drafts, bounded evidence, and safe deterministic diagrams."""

import copy
import hashlib
import json
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from test_departments import result

from backend.app.core.llm_router import LLMRouter, LLMRouterError
from backend.app.core.memory_vault import MemoryKind, MemoryVault
from backend.app.core.workspace_sandbox import UnsafePathError, WorkspaceSandbox
from backend.app.departments.growth_career import GrowthCareer
from backend.app.departments.growth_contracts import ArchitectureDiagram
from backend.app.models.events import AgentThoughtEvent, parse_event
from backend.app.models.mission import Artifact, Mission, MissionStatus, TaskNode, TaskStatus


def launch_output():
    return {
        "mode": "launch",
        "summary": "Prepared calculator launch drafts grounded in QA evidence.",
        "readme": "# Calculator\n\nA local integer calculator.\n\n## Run\n\n`uvicorn app:app`\n",
        "diagram": {
            "nodes": [
                {"key": "api", "label": "FastAPI homepage"},
                {"key": "addition", "label": "Integer addition"},
            ],
            "edges": [{"source": "api", "target": "addition", "label": "calls"}],
        },
        "assets": [
            {
                "key": "show_hn",
                "kind": "show_hn",
                "title": "Show HN draft",
                "content": "Show HN: A small local calculator with recorded regression checks.",
            },
            {
                "key": "reddit",
                "kind": "reddit",
                "title": "Reddit draft",
                "content": "I built an integer calculator and would welcome feedback on the code.",
            },
            {
                "key": "x",
                "kind": "x",
                "title": "X draft",
                "content": "Built a verified local calculator. Draft announcement.",
            },
        ],
    }


def career_output():
    return {
        "mode": "career",
        "summary": "Prepared a tailored backend engineer outreach draft.",
        "readme": "# Career package\n\nBackend materials based on the supplied profile.",
        "diagram": None,
        "assets": [
            {
                "key": "backend_outreach",
                "kind": "outreach",
                "title": "Backend role outreach",
                "content": "I work with Python and SQLite. I would like to discuss your role.",
            }
        ],
    }


async def seed_growth(settings, database, sandbox):
    mission_id, upstream_id = sandbox.mission_id, uuid4()
    qa_path = f"qa/{upstream_id}/report.json"
    report = json.dumps(
        {"passed": True, "repairs": 1, "history": [{"passed": False}, {"passed": True}]}
    )
    await sandbox.write_file(
        "SPEC.md", "# Calculator\nDeliver integer addition and a Ready homepage."
    )
    await sandbox.write_file("README.md", "# Existing project instructions\n")
    await sandbox.write_file("app.py", "def add(a: int, b: int) -> int:\n    return a + b\n")
    await sandbox.write_file(qa_path, report)
    async with database.session(write=True) as session:
        session.add(
            Mission(
                id=mission_id,
                title="Calculator",
                goal="Launch the calculator",
                status=MissionStatus.RUNNING,
            )
        )
        await session.flush()
        session.add(
            TaskNode(
                id=upstream_id,
                mission_id=mission_id,
                department="qa_self_healer",
                title="Verify calculator",
                description="Run acceptance checks",
                status=TaskStatus.COMPLETED,
                output_summary="Verification passed after 1 repair",
            )
        )
        session.add_all(
            [
                Artifact(
                    mission_id=mission_id,
                    file_path="app.py",
                    artifact_type="source",
                    content_preview="stale preview",
                ),
                Artifact(mission_id=mission_id, file_path=qa_path, artifact_type="qa_report"),
            ]
        )
    task = TaskNode(
        id=uuid4(),
        mission_id=mission_id,
        department="growth_career",
        title="Calculator launch",
        description="Prepare README, architecture and launch copy using completed QA evidence",
        status=TaskStatus.RUNNING,
        dependencies_json=json.dumps([str(upstream_id)]),
    )
    memory = MemoryVault(mission_id, storage_root=settings.memory_root)
    await memory.remember(
        MemoryKind.RESEARCH_FINDING,
        "Calculator research",
        "Integer addition is the implemented scope.",
        department="researcher",
        source="https://example.com/addition",
    )
    router = LLMRouter(settings, database)
    router.complete = AsyncMock(return_value=result(mission_id, launch_output()))
    return task, memory, router, qa_path


async def test_launch_reads_registered_artifacts_spec_and_memory_and_saves_grounded_assets(
    settings, database
):
    async with WorkspaceSandbox(uuid4(), root=settings.workspace_root) as sandbox:
        task, memory, router, qa_path = await seed_growth(settings, database, sandbox)
        await sandbox.write_file("unregistered-secret.txt", "not completed or registered")
        source_code = await sandbox.read_file("app.py")
        emitter = AsyncMock()
        output = await GrowthCareer(router).execute_task(task, sandbox, memory, emitter)
        context = json.loads(router.complete.call_args.args[1][1].content)
        assert "integer addition" in context["specification"]
        assert context["completed_tasks"][0]["department"] == "qa_self_healer"
        assert context["memory"] and "Calculator research" in json.dumps(context["memory"])
        assert "unregistered-secret" not in json.dumps(context)
        source = next(value for value in context["artifacts"] if value["path"] == "app.py")
        assert source["content"] == source_code and "stale preview" not in source["content"]
        verification = next(value for value in context["artifacts"] if value["path"] == qa_path)
        assert verification["verification"] == {"passed": True, "repairs": 1}
        assert (
            verification["sha256"] == hashlib.sha256(await sandbox.read_bytes(qa_path)).hexdigest()
        )
        prefix = f"growth/{task.id}"
        assert "```mermaid" in await sandbox.read_file("README.md")
        assert "n_api" in await sandbox.read_file(prefix + "/architecture.mmd")
        manifest = json.loads(await sandbox.read_file(prefix + "/manifest.json"))
        assert manifest["published"] is False
        assert len(output["artifacts"]) == 8
        for reference in output["artifacts"]:
            assert await sandbox.read_bytes(reference["file_path"])
        assert await sandbox.read_file("app.py") == source_code
        assert all(
            isinstance(call.args[0], AgentThoughtEvent)
            and parse_event(call.args[0].model_dump_json()) == call.args[0]
            for call in emitter.await_args_list
        )
        assert all(
            call.args[0].task_id == task.id and call.args[0].department == "growth_career"
            for call in emitter.await_args_list
        )
        assert await memory.search("calculator launch", department="growth_career")
        router.complete.assert_awaited_once()


async def test_career_package_uses_profile_memory_and_preserves_project_readme(settings, database):
    async with WorkspaceSandbox(uuid4(), root=settings.workspace_root) as sandbox:
        task, memory, router, _ = await seed_growth(settings, database, sandbox)
        task.title = "Backend career outreach"
        task.description = "Tailor outreach to a backend role using the Python SQLite profile"
        await memory.remember(
            MemoryKind.DOCUMENT_CHUNK,
            "Candidate profile",
            "Python and SQLite backend development.",
            department="researcher",
        )
        router.complete.return_value = result(sandbox.mission_id, career_output())
        old_readme = await sandbox.read_file("README.md")
        output = await GrowthCareer(router).execute_task(task, sandbox, memory, AsyncMock())
        assert await sandbox.read_file("README.md") == old_readme
        assert all(
            reference["file_path"].startswith(f"growth/{task.id}/")
            for reference in output["artifacts"]
        )
        assert "Python and SQLite" in await sandbox.read_file(
            f"growth/{task.id}/backend_outreach.md"
        )
        assert "Candidate profile" in router.complete.call_args.args[1][1].content
        assert "Never invent" in router.complete.call_args.args[1][0].content


@pytest.mark.parametrize(
    "mutation",
    [
        "duplicate_asset",
        "traversal",
        "missing_platform",
        "bad_edge",
        "duplicate_node",
        "reserved_asset",
        "raw_mermaid",
    ],
)
async def test_invalid_launch_package_never_overwrites_existing_files(settings, database, mutation):
    async with WorkspaceSandbox(uuid4(), root=settings.workspace_root) as sandbox:
        task, memory, router, _ = await seed_growth(settings, database, sandbox)
        invalid = copy.deepcopy(launch_output())
        if mutation == "duplicate_asset":
            invalid["assets"].append(invalid["assets"][0])
        elif mutation == "traversal":
            invalid["assets"][0]["key"] = "../outside"
        elif mutation == "missing_platform":
            invalid["assets"].pop()
        elif mutation == "bad_edge":
            invalid["diagram"]["edges"][0]["target"] = "missing"
        elif mutation == "duplicate_node":
            invalid["diagram"]["nodes"].append(invalid["diagram"]["nodes"][0])
        elif mutation == "reserved_asset":
            invalid["assets"][0]["key"] = "manifest"
        else:
            invalid["readme"] = "```mermaid\ninvalid graph\n```"
        router.complete.return_value = result(sandbox.mission_id, invalid)
        before = await sandbox.read_file("README.md")
        with pytest.raises(ValueError):
            await GrowthCareer(router).execute_task(task, sandbox, memory, AsyncMock())
        assert await sandbox.read_file("README.md") == before
        assert not (sandbox.root / "growth").exists()


async def test_snapshot_excludes_private_files_and_handles_binary_missing_and_pending_sources(
    settings, database
):
    async with WorkspaceSandbox(uuid4(), root=settings.workspace_root) as sandbox:
        task, memory, router, _ = await seed_growth(settings, database, sandbox)
        await sandbox.write_file(".env", "OPENAI_API_KEY=must-not-reach-prompt")
        await sandbox.write_bytes("frame.png", b"\x89PNG\x00binary")
        async with database.session(write=True) as session:
            session.add_all(
                [
                    Artifact(mission_id=sandbox.mission_id, file_path=path, artifact_type="source")
                    for path in [".env", "frame.png", "missing.txt"]
                ]
            )
            session.add(
                TaskNode(
                    mission_id=sandbox.mission_id,
                    department="engineer",
                    title="Pending work",
                    description="Do not claim this is complete",
                    status=TaskStatus.PENDING,
                )
            )
        await GrowthCareer(router).execute_task(task, sandbox, memory, AsyncMock())
        context = json.loads(router.complete.call_args.args[1][1].content)
        assert "must-not-reach-prompt" not in json.dumps(context)
        assert "Pending work" not in json.dumps(context)
        sources = {source["path"]: source for source in context["artifacts"]}
        assert sources[".env"]["availability"] == "excluded_private_filename"
        assert sources["frame.png"]["availability"] == "binary_metadata_only"
        assert sources["missing.txt"]["availability"] == "unavailable_or_unsafe"


async def test_growth_requires_completed_dependencies_and_matching_resources(settings, database):
    async with WorkspaceSandbox(uuid4(), root=settings.workspace_root) as sandbox:
        task, memory, router, _ = await seed_growth(settings, database, sandbox)
        task.dependencies_json = json.dumps([str(uuid4())])
        with pytest.raises(ValueError, match="dependencies"):
            await GrowthCareer(router).execute_task(task, sandbox, memory, AsyncMock())
        with pytest.raises(ValueError, match="same mission"):
            await GrowthCareer(router).execute_task(
                task, sandbox, MemoryVault(uuid4(), storage_root=settings.memory_root), AsyncMock()
            )
        router.complete.assert_not_awaited()


async def test_output_symlink_does_not_replace_root_readme(settings, database, tmp_path):
    async with WorkspaceSandbox(uuid4(), root=settings.workspace_root) as sandbox:
        task, memory, router, _ = await seed_growth(settings, database, sandbox)
        before = await sandbox.read_file("README.md")
        (sandbox.root / "growth").symlink_to(tmp_path, target_is_directory=True)
        with pytest.raises(UnsafePathError):
            await GrowthCareer(router).execute_task(task, sandbox, memory, AsyncMock())
        assert await sandbox.read_file("README.md") == before
        assert not (tmp_path / str(task.id)).exists()


async def test_router_failure_does_not_create_fake_assets(settings, database):
    async with WorkspaceSandbox(uuid4(), root=settings.workspace_root) as sandbox:
        task, memory, router, _ = await seed_growth(settings, database, sandbox)
        router.complete.side_effect = LLMRouterError("Budget exhausted")
        with pytest.raises(LLMRouterError):
            await GrowthCareer(router).execute_task(task, sandbox, memory, AsyncMock())
        assert not (sandbox.root / "growth").exists()


def test_mermaid_escapes_reserved_identifiers_quotes_html_and_edge_labels():
    diagram = ArchitectureDiagram.model_validate(
        {
            "nodes": [{"key": "end", "label": 'API "name" <script>\n[service]'}],
            "edges": [{"source": "end", "target": "end", "label": "text|label"}],
        }
    ).mermaid()
    assert diagram.startswith("flowchart TD\n") and "n_end" in diagram
    assert "<script>" not in diagram and "&lt;script&gt;" in diagram
    assert "&quot;name&quot;" in diagram and "&#124;" in diagram
