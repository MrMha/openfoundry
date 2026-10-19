"""Run small, uncached live missions and independently check their delivered app.py.

Example: GEMINI_API_KEY=... .venv/bin/python -m scripts.run_live_evaluation --case slugify
Results stay in ignored .openfoundry/live-evaluations until reviewed for publication.
No mock completions, prepared fixes, optimization claims, or automatic paid fallback.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import subprocess
import time
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import select

from backend.app.config import Settings
from backend.app.core.workspace_sandbox import WorkspaceSandbox
from backend.app.main import create_app
from backend.app.models.mission import LLMAttempt
from scripts.live_evaluation_cases import CASES


def digest(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


async def independently_check(settings: Settings, mission_id: UUID, checks: str) -> dict:
    """Use a fresh workspace: generated pytest config/conftest cannot alter grading."""
    async with WorkspaceSandbox(mission_id, root=settings.workspace_root) as source:
        try:
            application = await source.read_file("app.py")
        except (OSError, ValueError):
            return {"status": "not_run", "reason": "No root app.py delivered"}
    check_id = uuid4()
    async with WorkspaceSandbox(check_id, root=settings.data_dir / "acceptance") as sandbox:
        await sandbox.write_file("app.py", application)
        await sandbox.write_file("test_independent.py", checks)
        result = await sandbox.run_test_command(["pytest", "-q", "test_independent.py"])
    return {
        "status": "passed" if result.succeeded else "failed",
        "application_sha256": digest(application),
        "checks_sha256": digest(checks),
        "workspace": str(settings.data_dir / "acceptance" / str(check_id)),
        **asdict(result),
    }


async def attempt_rows(client: TestClient, mission_id: UUID) -> list[dict]:
    async with client.app.state.database.session() as session:
        rows = list(
            await session.scalars(
                select(LLMAttempt)
                .where(LLMAttempt.mission_id == mission_id)
                .order_by(LLMAttempt.created_at)
            )
        )
    return [
        {
            "id": str(row.id),
            "provider": row.provider,
            "model": row.model,
            "status": str(row.status),
            "prompt_tokens": row.prompt_tokens,
            "completion_tokens": row.completion_tokens,
            "estimated_list_price_usd": str(row.cost_usd) if row.cost_usd is not None else None,
            "reserved_usd": str(row.reserved_cost_usd),
            "error_code": row.error_code,
        }
        for row in rows
    ]


def run_case(client: TestClient, settings: Settings, name: str, timeout: float) -> dict:
    case = CASES[name]
    started = time.monotonic()
    created = client.post(
        "/api/missions",
        json={
            "title": "Live evaluation: " + name,
            "goal": case["requirements"],
            "configuration": {"provider": "gemini", "model": settings.gemini_model},
        },
    )
    created.raise_for_status()
    mission_id = UUID(created.json()["id"])
    approved: set[str] = set()
    timed_out = False
    while True:
        detail = client.get(f"/api/missions/{mission_id}").json()
        if detail["status"] in {"completed", "failed", "cancelled"}:
            break
        if time.monotonic() - started > timeout:
            timed_out = True
            break
        for approval in detail["approvals"]:
            task_id = approval["task_id"]
            if approval["decision"] == "requested" and task_id not in approved:
                task = next(task for task in detail["tasks"] if task["id"] == task_id)
                # Synthetic implementation/QA/drafts only. Other side-effect gates stop here.
                if task["department"] not in {"engineer", "qa_self_healer", "growth_career"}:
                    raise RuntimeError("Unexpected approval requires operator review")
                response = client.post(
                    f"/api/missions/{mission_id}/tasks/{task_id}/approve",
                    json={"reason": "Preauthorized synthetic evaluation"},
                )
                response.raise_for_status()
                approved.add(task_id)
        time.sleep(0.5)
    generation_seconds = round(time.monotonic() - started, 3)
    if timed_out:
        independent = {"status": "not_run", "reason": "Mission deadline exceeded"}
    else:
        independent = client.portal.call(independently_check, settings, mission_id, case["checks"])
    attempts = client.portal.call(attempt_rows, client, mission_id)
    return {
        "case": name,
        "mission_id": str(mission_id),
        "requirements": case["requirements"],
        "mode": "live",
        "response_cache": False,
        "deadline_exceeded": timed_out,
        "generation_seconds": generation_seconds,
        "approval_policy": "runner auto-approval",
        "mission": detail,
        "attempts": attempts,
        "independent_acceptance": independent,
        "recorded_prompt_tokens": sum(row["prompt_tokens"] for row in attempts),
        "recorded_completion_tokens": sum(row["completion_tokens"] for row in attempts),
        "usage_complete": all(row["status"] in {"succeeded", "rejected"} for row in attempts),
        "end_to_end_pass": detail["status"] == "completed" and independent["status"] == "passed",
        "provider_invoice_charge_usd": None,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", choices=[*CASES, "all"], default="slugify")
    parser.add_argument("--model", default="gemini/gemini-3.5-flash-lite")
    parser.add_argument("--repeats", type=int, choices=range(1, 11), default=1)
    parser.add_argument("--timeout", type=float, default=900)
    parser.add_argument("--max-output-tokens", type=int, default=4096)
    args = parser.parse_args()
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        parser.error("Set GEMINI_API_KEY in the backend environment; never put it in an argument")
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:6]
    data_dir = Path(".openfoundry/live-evaluations", stamp).resolve()
    settings = Settings(
        data_dir=data_dir,
        gemini_api_key=SecretStr(key),
        gemini_model=args.model,
        provider_order=("gemini",),
        max_output_tokens=args.max_output_tokens,
        max_parallel_tasks=1,
    )
    data_dir.mkdir(parents=True, mode=0o700)
    report = {
        "started_at_utc": datetime.now(UTC).isoformat(),
        "model": args.model,
        "backend_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
        "source_sha256": {
            str(path): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(
                [
                    *Path("backend/app").rglob("*.py"),
                    Path("pyproject.toml"),
                    Path("uv.lock"),
                ]
            )
        },
        "python": platform.python_version(),
        "platform": platform.system(),
        "runner_sha256": digest(Path(__file__).read_text()),
        "cases_sha256": digest(Path(__file__).with_name("live_evaluation_cases.py").read_text()),
        "max_output_tokens": args.max_output_tokens,
        "request_timeout_seconds": settings.request_timeout_seconds,
        "provider_max_attempts": settings.provider_max_attempts,
        "runs": [],
        "limitations": [
            "Small synthetic pilot, not a population reliability estimate",
            "Generated code executes in measured sandbox mode; guarded is not OS isolation",
            "Token costs are catalog estimates, not provider invoice amounts",
            "Fresh missions; no cached responses; no model-generated grading",
            "Research analyzes the local requirements document; live web research is not tested",
        ],
    }
    names = list(CASES) if args.case == "all" else [args.case]
    with TestClient(create_app(settings)) as client:
        for repeat in range(args.repeats):
            for name in names:
                result = run_case(client, settings, name, args.timeout)
                result["repeat"] = repeat + 1
                report["runs"].append(result)
                payload = json.dumps(report, indent=2, default=str).replace(key, "[REDACTED]")
                (data_dir / "report.json").write_text(payload + "\n")
                print(
                    json.dumps(
                        {
                            "case": name,
                            "mission_status": result["mission"]["status"],
                            "acceptance": result["independent_acceptance"]["status"],
                            "report": str(data_dir / "report.json"),
                        }
                    ),
                    flush=True,
                )
                if result["deadline_exceeded"]:
                    return  # Close coordinator before trying another mission.


if __name__ == "__main__":
    main()
