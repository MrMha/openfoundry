# Contributing to OpenFoundry

Start with a [scoped contributor ticket](docs/GOOD_FIRST_ISSUES.md), a [department proposal](.github/ISSUE_TEMPLATE/new_department_plugin.md), or a reproducible [bug report](.github/ISSUE_TEMPLATE/bug_report.md). Small changes with inspectable evidence are welcome. Comment on the relevant GitHub issue before starting overlapping work; the ticket briefs in this checkout have not been created or assigned automatically.

## Development setup

Use the existing checkout when one is already provided. You do not need a second checkout or worktree to develop or test. Required tools: Git, Python 3.11+, uv, Node 22.12+ and npm. CI uses Python 3.12, Node 22 and uv 0.12.23. The default test suites use fixtures and require no provider credentials, Ollama, Docling models or browser-use installation.

From the repository root:

```bash
uv sync --frozen --extra dev
(cd frontend && npm ci)
```

Start the backend in one terminal:

```bash
uv run --frozen openfoundry
```

Start Vite in another:

```bash
cd frontend
npm run dev
```

Open http://127.0.0.1:5173. Vite proxies `/api` and `/ws` to port 8000. `OPENFOUNDRY_BACKEND_URL` overrides that backend origin for Vite; configure `OPENFOUNDRY_CORS_ORIGINS` on the backend when the browser origin changes.

For the built, single-process UI:

```bash
(cd frontend && npm run build)
uv run --frozen openfoundry
```

Open http://127.0.0.1:8000. Build before starting: `create_app()` detects `frontend/dist/index.html` at application creation. `GET /health` checks the database; an open port alone is not a readiness check. Test the UI with the key-free Self-Healing Demo, review its Engineering approval, and verify the final files and QA report.

The application reads exported process variables and does not load `.env` automatically. Local loopback clients can connect without a token when `OPENFOUNDRY_API_TOKEN` is unset. If set, use **Connect backend** and enter the operator token. Saving it in localStorage is opt-in. Provider keys belong only in the backend process. Non-loopback CLI binding requires an operator token. See [the backend guide](docs/BACKEND_GUIDE.md) and [frontend guide](frontend/README.md).

## Validation

Run all commands from the stated directory. A successful change preserves both source contracts and real execution behavior; do not weaken tests, disable schema validation or replace subprocess verification with a fake pass.

### Backend: repository root

```bash
uv run --frozen pytest
uv run --frozen mypy
uv run --frozen ruff check backend scripts frontend/scripts
uv run --frozen ruff format --check backend scripts frontend/scripts
uv run --frozen flake8 backend scripts frontend/scripts --max-line-length=100 --extend-ignore=E203,W503
```

Mypy is strict for `backend.app` (Python 3.11 target). Flake8/mypy require the `dev` extra installed above. If formatting needs changes, run `uv run --frozen ruff format backend scripts frontend/scripts`, review the diff, then repeat the checks. Tests use temporary databases/workspaces and include API/auth, approvals/restart recovery, WebSocket replay, budgets, plugins, research, engineering/QA, blueprints and real MCP transports.

For an isolated area, run an existing target, then the required full suite before opening the PR:

```bash
uv run --frozen pytest backend/tests/test_departments.py backend/tests/test_mcp_blueprints.py
```

### Frontend: `frontend/`

```bash
cd frontend
npm run typecheck
npm run lint
npm run format:check
npm test
npm run build
```

`npm test` runs Vitest once. `npm run build` performs TypeScript project compilation and Vite's production build. Use `npm run format` only when you intend to change frontend formatting; inspect its results.

### Chromium E2E: `frontend/`

Build first and provision Playwright Chromium (on a fresh Linux runner, `--with-deps` may require administrator privileges):

```bash
cd frontend
npm run build
npx playwright install --with-deps chromium
npm run test:e2e
```

If a compatible system Chromium is already installed, skip the download and use:

```bash
cd frontend
PLAYWRIGHT_CHROMIUM_EXECUTABLE=/usr/bin/chromium npm run test:e2e
```

`frontend/playwright.config.ts` starts its own temporary-data backend on **8001** and Vite on **5174**. Keep those ports free. The two projects exercise development Vite and the production FastAPI SPA mount. They verify real demo execution, approval/defer/reject behavior, replay-driven panels, artifact inspection, responsive layout and the blueprint contract. Browser logs/traces go to ignored `frontend/test-results/`; these tests do not replace the generated application's own QA tests.

The last locally verified baseline is **408 backend + 34 frontend + 6 Chromium tests**. Counts can change with contributions; report the counts actually executed and distinguish skips/failures. CI runs the commands in [.github/workflows/ci-backend.yml](.github/workflows/ci-backend.yml) and [.github/workflows/ci-frontend.yml](.github/workflows/ci-frontend.yml); a local pass is separate from a remote workflow result.

## Add a department plugin

1. Choose a unique lowercase identifier matching `[a-z][a-z0-9_]{0,63}`. Do not replace a canonical department or legacy alias. Create `backend/app/departments/<name>.py` and subclass `BaseDepartment` from `backend/app/departments/base_department.py`. The [20-line README example](README.md#build-your-own-department-in-20-lines) is executable.
2. Add `@register_department`, nonempty `name`/`role_description`, and optional unique lowercase `capabilities: tuple[str, ...]` and `plugin_version`. Keep the inherited constructor accepting `LLMRouter`, or call `super().__init__(router)` in your constructor. Never capture mission-specific mutable state on a shared plugin instance.
3. Implement exactly this async interface (these are existing types):

   ```python
   async def execute_task(
       self,
       task: TaskNode,
       sandbox: WorkspaceSandbox,
       memory: MemoryVault,
       emit_event: EventEmitter,
   ) -> DepartmentOutput:
   ```

   `EventEmitter = Callable[[WarRoomEvent], Awaitable[None]]`; `DepartmentOutput = dict[str, JsonValue]`. Import the aliases from `base_department.py`. A returned `summary` is persisted as the task's output summary. An optional `artifacts` list contains `{"file_path": "relative/file.md", "artifact_type": "report"}` entries. Every artifact must already exist as a regular contained file in the supplied sandbox. Do not return Path, Decimal, datetime, bytes or ORM instances as raw JSON values.
4. Write files with `sandbox.write_file()`/`write_bytes()` and retrieve context with `memory.search()`. Use `memory.remember(MemoryKind.RESEARCH_FINDING, title, content, department=self.name)` for findings; choose the actual appropriate `MemoryKind`. Meter every LLM call through `self.router.complete(task.mission_id, messages, json_schema=...)`. Never call a provider or browser-use LLM Agent outside the router.
5. Emit only `AgentThoughtEvent`, `BrowserFrameEvent` or `CodeDiffEvent`, with this mission ID and the current task ID. Use the canonical plugin name as `department`; legacy `Department` enum values remain compatibility aliases. The orchestrator owns status/approval events and DAG materialization. Raise `DepartmentExecutionError(message, output=...)` when failed work has diagnostic artifacts worth retaining; never label an unexecuted check as passed.
6. Import the module explicitly when starting the backend:

   ```bash
   OPENFOUNDRY_DEPARTMENT_PLUGINS=backend.app.departments.readiness uv run --frozen openfoundry
   ```

   This command uses the README example's exact module. Substitute your actual module for another plugin. The environment variable accepts comma-separated modules. Decorators execute when imported; a file's presence alone does not register it. External packages may declare `[project.entry-points."openfoundry.departments"]` in their own `pyproject.toml`, mapping an entry name to `your_package.module:YourDepartment`. Entry-point code is trusted installed Python, not a sandboxed marketplace download.
7. Confirm metadata in `GET /api/plugins`. The planner can use available registered departments, but no task runs merely because a plugin loaded. Add deterministic tests in `backend/tests/test_<name>.py`: real sandbox/memory artifacts, typed events, malformed output, two concurrent mission calls, cancellation and provider budget errors. Extend `backend/tests/test_departments.py` only when the shared contract changes; avoid registry pollution between tests.
8. Run backend checks and update docs. If adding a card beyond the five canonical departments, coordinate its UI behavior explicitly; the current `DepartmentCard.tsx` layout is built around those five. Bundled blueprint schema version 1 also restricts task departments to the four canonical execution departments; do not silently bypass that validation for a new plugin.

Use guarded test subprocesses only for operator-approved code. The allowlisted commands are pytest, `npm test`, Ruff and Flake8 with bounded arguments and a maximum 60-second timeout; package install, arbitrary shell, Git push and curl are not general sandbox commands. Plugins themselves run in the service process, with service privileges. External tools use `self.mcp` only when present and explicitly granted; see [MCP permissions](docs/mcp.md).

## Add a blueprint

1. Copy a close template into a new root `blueprints/<id>.json`; keep `id` equal to the filename stem. Do not edit the frontend to hardcode another option.
2. Preserve the full `Blueprint` contract in `backend/app/core/blueprints.py`: `schema_version: 1`, a semver `version`, `id`, `title`, `description`, `goal_template`, `parameters`, `suggested_configuration`, `tasks`, `deliverables`, and `acceptance_criteria`. Unknown fields are rejected. The loader currently permits **1–32 files**, at most **128 KiB each**, **16 parameters** and **30 tasks** per template.
3. Parameter entries define `key`, `label`, `description`, `kind` (`text`, `url`, `path`), `required`, and `default`. Defaults must themselves resolve successfully. Use Python `string.Template` syntax `$key` or `${key}` in the goal and task descriptions; use `$$` for a literal dollar sign. Task titles, deliverables and criteria are currently literal text, not interpolated fields. Paths must be canonical mission-relative paths; URL parameters accept HTTP(S) without embedded credentials or fragments. A URL parameter passing schema validation does not grant network access.
4. Tasks use unique symbolic `key`s, `department`, `title`, `description`, symbolic `dependencies`, and an explicit `requires_approval` boolean. Use only `researcher`, `engineer`, `qa_self_healer`, `growth_career`. Do not add a Chief of Staff task: the scheduler creates it. Reject missing/self/cyclic dependencies and preserve `requires_approval: true` for every Engineering task.
5. Make deliverables measurable and acceptance criteria verifiable. State required input files, missing-input behavior, sources and publication limits. Suggested budgets cannot exceed the operator's server ceiling; the UI clamps defaults and the API enforces policy. A template is planning context, not a command-execution grant or an exact task replay promise.
6. Validate the full catalog and inspect the resolved defaults from the repository root:

   ```bash
   uv run --frozen python - <<'PY'
   from backend.app.core.blueprints import load_blueprints
   for blueprint in load_blueprints():
       goal, tasks, parameters = blueprint.resolve({})
       print(blueprint.id, len(tasks), parameters, goal)
   PY
   uv run --frozen pytest backend/tests/test_mcp_blueprints.py backend/tests/test_frontend_contract.py
   ```

7. Add template-specific tests for default/custom parameters, unknown keys, missing inputs and DAG/gate invariants. Extend `frontend/src/services/blueprints.test.ts` or `frontend/e2e/demo.spec.ts` only for a changed rendering contract. The command bar fetches the catalog, fills the goal and parameters, previews tasks, and submits `blueprint_id`/`blueprint_parameters` with the edited goal.

## Keep REST and WebSocket contracts aligned

The running app exposes OpenAPI at `/openapi.json` and Swagger UI at `/docs`. Authoritative models are in `backend/app/models/events.py`, `api/routes_missions.py`, `api/routes_plugins.py`, `api/routes_runtime.py` and `core/blueprints.py`. After a schema change, regenerate from `frontend/`:

```bash
cd frontend
npm run contracts
npm run typecheck
npm test
```

Review `frontend/src/services/generated.ts`, `openapi.json`, `events.schema.json` and `probes.schema.json`, then run `backend/tests/test_frontend_contract.py`. Never hand-edit the generated types to conceal drift.

REST clients use `Authorization: Bearer <operator-token>` when configured. Browser WebSockets connect to `/ws/missions/{mission_id}?after=<last_sequence>` with subprotocols `openfoundry` and `bearer.<operator-token>`; tokens must not go into URLs. The socket sends replayable version-1 events with `event_id`, `mission_id`, UTC timestamp and durable `sequence`. `heartbeat` is a separate transport message with a cursor, not a `WarRoomEvent`. Client messages accept only the literal `ping`; approvals go through REST. Reconnect/replay must not overwrite newer REST state.

Current decision routes are `POST /api/missions/{mission_id}/tasks/{task_id}/approve` and `/reject`, with optional `{"reason": "..."}`. Same-decision retries are idempotent; the opposite decision returns 409. Rejection cancels the mission. Proposed chat approval and GitHub publication routes in [ROADMAP_SPECS.md](ROADMAP_SPECS.md) do not exist yet.

## Regenerate the demo assets

Install frontend dependencies, build the UI and provision Chromium using the steps above. From the root:

```bash
uv run --frozen python scripts/generate_demo_assets.py
```

With an existing build and system Chromium:

```bash
PLAYWRIGHT_CHROMIUM_EXECUTABLE=/usr/bin/chromium uv run --frozen python scripts/generate_demo_assets.py --skip-build
```

The script starts a temporary local API with a fresh token/database, clears provider/MCP credentials/configuration, runs and approves the deterministic demo, and tears down its process. It captures the actual mission-start, QA-failure, auto-patch and verified states through `frontend/scripts/capture_demo.mjs`. Output: `docs/assets/war-room-hero.png` (2560×1600 from a 1280×800 viewport at 2×), `war-room-demo.gif` (1280×800, four frames) and `demo-assets.json`. Review the images and metadata; do not replace the UI with invented pass states. These assets and `blueprints/*.json` belong in Git. Ignore transient captures, browser traces, databases and mission workspaces.

## Pull request checklist

- Describe the concrete trigger, behavior change and validation; link the ticket or proposal.
- Include deterministic failure/recovery evidence for behavior changes and screenshots for UI changes.
- Preserve existing user files, cost accounting, cancellation, approval ownership and path containment.
- Do not include credentials, localStorage dumps, private research/profile content, SQLite files, dependency caches or generated mission directories.
- Keep lockfiles frozen unless dependency changes are intentional; explain every new runtime dependency.
- Run all applicable commands above and report actual counts. Publication, chat delivery and external repository writes require explicit operator intent; mocks do not establish live-service compatibility.

Contributions are provided under the repository's [MIT license](LICENSE). No CLA or DCO process is currently configured. See [future interface specifications](ROADMAP_SPECS.md) before implementing chat approvals, PR publishing or a registry.
