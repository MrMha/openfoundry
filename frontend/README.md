# OpenFoundry War Room

React, TypeScript, Tailwind CSS 4, and Vite. Requires Node 22.12+ and the Python backend.

Build once from `frontend/` with `npm ci` and `npm run build`. From the repository root,
**`uv run --frozen openfoundry`** now serves both the API and built War Room at
**http://127.0.0.1:8000**. FastAPI detects `frontend/dist/index.html` when the app is
created; restart after building if the backend was already running. Missing builds leave
the API available without mounting a frontend. The build is local and ignored by Git.

For frontend development with hot reload:

Start the backend from the repository root:

```bash
uv sync --frozen --extra dev
uv run openfoundry
```

In another terminal:

```bash
cd frontend
npm ci
npm run dev
```

Open **http://127.0.0.1:5173**. Click **⚡ Run Self-Healing Demo (No API Key)**,
review `SPEC.md` in the engineering approval modal, and click **Approve & Continue**. The offline demo
runs all five real departments, executes tests and ASGI/DOM checks, repairs a seeded bug
once, and writes launch assets. API cost is **$0.00**; no Ollama service is needed.
The browser frame is a labeled diagnostic card when a real browser is unavailable.
Tests and Git must be installed in the backend environment. The configured sandbox mode
is respected; `strict` mode requires working Bubblewrap namespaces.

Local loopback access needs no operator token. If `OPENFOUNDRY_API_TOKEN` is set, open
**Connect backend**, enter that operator token, and connect. Token persistence is opt-in
with **Remember on this browser**. This stores the operator token in this origin's
localStorage; leave it unchecked on shared browsers. Only the operator token belongs here;
provider keys remain in backend environment variables. Browser-compatible operator tokens
must use HTTP token characters (for example, a random hexadecimal or URL-safe token).

The provider/model selector and budget apply to the next live mission. The backend rejects
unconfigured providers, mismatched model prefixes, and budgets above its server ceiling.
Blueprints fill the mission prompt. They do not grant additional tools or bypass approval.

Vite proxies `/api` and `/ws` to `http://127.0.0.1:8000`. Set `OPENFOUNDRY_BACKEND_URL`
when the backend uses another origin. For a different browser origin, also update the
backend's `OPENFOUNDRY_CORS_ORIGINS`. FastAPI's production mount serves assets and browser
navigation with HTML fallback, leaving `/api`, `/ws`, health, and documentation routes
intact. Missing assets and unknown API paths return errors. A deployment reverse proxy
must preserve Authorization, Origin, and Sec-WebSocket-Protocol. Vite's development proxy
is not bundled into `dist/`.

```bash
npm run build          # strict TypeScript and production bundle
npm run lint
npm run format:check
npm test               # REST, event schema, replay/reconnect, state, controls, graph
npm run contracts      # regenerate from FastAPI/Pydantic; requires uv and the backend
npx playwright install chromium
npm run test:e2e       # starts isolated backend on 8001 and Vite on 5174
```

If Chromium is already installed, use
`PLAYWRIGHT_CHROMIUM_EXECUTABLE=/usr/bin/chromium npm run test:e2e`.
The browser tests run against both Vite and the production FastAPI mount. They authenticate,
review Engineering, verify keyboard focus containment, observe the repair loop and
structured endpoint evidence, inspect launch assets, check a mobile viewport, and exercise
deferred approvals and rejection. Build before running them. Test artifacts
are ignored by Git. Its backend uses an isolated temporary SQLite database and explicit
guarded mode.

REST types and the event validator are generated from the backend into
`src/services/generated.ts`, `openapi.json`, `events.schema.json`, and `probes.schema.json`. Backend tests check
these contracts for drift. The socket uses `openfoundry` / `bearer.<token>` subprotocols;
only the durable `after` cursor goes into its URL. Reconnect uses bounded exponential
backoff, duplicate events are ignored, and replayed evidence cannot regress a newer REST
snapshot. UI logs and diff history are bounded; full artifacts stay in the mission workspace.

The three-column workspace separates registered department status, code/DAG inspection,
and QA/activity observations, then collapses for narrower screens. Department cards show
the sandbox capability measured by `/api/plugins`, refreshed every 30 seconds; explicit
guarded configuration is labeled separately from a namespace probe. The QA viewer loads
the current worker's `probes.json` report to display HTTP status, DOM failures, console
errors, and the actual capture backend. Missing evidence stays unknown. Healing progress
uses typed QA events and survives eviction from the bounded diff buffer; a clean first pass
does not claim that a repair happened. The code inspector supports unified and side-by-side
diffs, real hunk line numbers, revision selection, workspace file inspection, and full
downloads when previews are truncated. Source code and report text are rendered as text.
