# Pre-Publish Security, Secret & Privacy Audit

Audit date: **2026-10-19**. Scope: the complete local working tree, ignored files, all reachable local Git commits/diffs, commit identities, demo media, REST/WebSocket boundaries, subprocesses and research/QA URL fetching.

## Publication decision

**Local hardening is implemented. Remote history remediation still requires an explicitly approved history-replacing push.** The repository was pushed before this audit was requested. One published commit contained a non-public author/committer email. The local history is being rewritten to the project's existing public GitHub noreply identity; updating a local repository does not remove an already published GitHub commit, cached reference, fork or clone. No force-push is part of this audit.

The exact old identity and recovery/history evidence are stored in the restricted local audit directory outside the checkout, rather than reintroducing private contact details into this public report. The public Git author identity selected from the initial repository commit is **MrMha <114929652+MrMha@users.noreply.github.com>**. GitHub's existing `noreply@github.com` committer identity is also public.

This is a source/history audit and focused adversarial verification, not a claim that arbitrary generated code is safe to execute with host privileges.

## Findings and fixes

| ID | Priority | Finding | Remediation |
| --- | --- | --- | --- |
| PRIV-01 | High privacy | Published commit author and committer used a non-public academic email. | Set repository-local author defaults to the existing public noreply identity; rewrite local author/committer metadata, remove old local refs/reflogs/objects, and rescan. Remote replacement remains pending operator approval. |
| ENV-01 | High | The optional browser-use 0.11.13 launcher can inherit the backend environment; its profile `env` field is not sufficient to prevent this. | Run browser extraction in a fixed-module worker with a positive environment allowlist and isolated Python startup. Its browser SDK subprocesses inherit only the sanitized worker environment. Require a provisioned browser executable instead of a task-time SDK installation. |
| ENV-02 | Medium | Document parsing inherited host `PYTHONPATH`, allowing startup/module injection. | Reuse the sanitized worker runner, pass Python `-I`, strip preload/startup/provider/cloud variables, and test a malicious `sitecustomize.py` canary. Preserve offline parsing/resource limits. |
| LOG-01 | Medium | Standard access/WebSocket/SDK/error logging lacked centralized credential redaction; URL query contents could be written to logs. | Install redaction before standard Python log handlers; mask configured and sensitive environment credentials, bearer/subprotocol values, URL userinfo and URL queries, including encoded queries; sanitize exception text while preserving Uvicorn formatter arguments. |
| API-01 | Medium privacy | Default request-validation errors could echo an accidentally submitted credential in `input`/`ctx`. | Return only sanitized location/message/type fields; omit submitted values and context. |

Changed implementation paths:

- `backend/app/core/research_worker.py`: fixed-module process allowlist, positive environment, timeout/output bounds, cancellation and process-group cleanup.
- `backend/app/departments/_research_browser.py`: private browser worker; SDK output is discarded outside the bounded result protocol.
- `backend/app/departments/_research_web.py`: sanitized worker dispatch and provisioned browser selection; existing public-IP/DNS/proxy checks retained.
- `backend/app/departments/_research_documents.py`: isolated sanitized dispatch; existing document limits and offline worker retained.
- `backend/app/core/security_logging.py`: log redaction at record creation, before handlers format or persist messages.
- `backend/app/main.py`: install redaction and suppress request-input echoes.
- `backend/tests/test_security_audit.py`: 18 adversarial regression cases; existing browser-adapter tests remain in `test_researcher.py`.

## Secret and credential scan

| Surface | Method | Outcome |
| --- | --- | --- |
| Published source and complete reachable diff history | Official Gitleaks **8.24.3**, binary SHA-256 checked against its published checksum file; redacted JSON reports; `git --log-opts='--all --full-history'`. | No operational credential finding. Original history contained two commits; final rewritten history is rescanned before handoff. |
| Every regular file in the initial working tree | A supplemental byte-pattern inventory read **30,359 files / 674,824,252 bytes**, including ignored dependencies, caches, binary files and build outputs; no unreadable regular files. Symlinks were inventoried without following them outside the tree. | No first-party API-key/token/private-key value found. This complements Gitleaks' ignore/default rules; it does not pretend the default directory scan covers every dependency byte. |
| Credential references | Reviewed `sk-`, `AIza`, `ghp_`/other GitHub token prefixes, `github_pat_`, AWS key shapes, private-key markers, bearer strings, key-variable names, Authorization and password references. | Source matches were configuration names, documentation syntax and explicit synthetic negative-test fixtures. A variable/header name alone is not a leaked credential. |
| Ignored dependencies | Reviewed key-shaped matches in boto/botocore examples, cryptography format markers and LiteLLM examples/metadata. | Third-party examples/format identifiers; none are publication files. No provider requests were made to test whether a candidate credential is live. |
| Environment/database files | Filename inventory plus tracked-file and historical-path checks for `.env`, `.db`, `.sqlite`, `.sqlite3` and sidecars. | No runtime environment/database file in the publication manifest. The observed `.mypy_cache/3.11/cache.db` is an ignored tool cache, not a mission database. |
| Git metadata and objects | Author/committer review, all-ref history scan, source-tree comparison during rewrite, reflog/unreachable-object cleanup and post-rewrite scan. | Public noreply identities are required in final local history; remote history is a separate pending remediation. |

The audit's recovery bundle, original full diff stream, raw scan classifications and private identity record remain under `/tmp/openfoundry-security-audit/` with owner-only permissions. They are outside the checkout and are not part of the publication manifest. Do not copy that directory into a release or Docker context.

## Media and privacy review

The hero PNG and all four GIF frames were inspected, decoded and OCR-scanned. The PNG has no metadata fields; the GIF contains only animation/image metadata (version, duration, loop, background and extension). No personal email/name, private account, environment token or internal cloud-host identity was observed. OCR is supplementary and can miss text; visual review and source-fixture inspection are also part of this conclusion.

`http://asgi.local/` in the captures is the deliberate synthetic ASGI test origin. `metadata.google.internal` and credential-bearing `example.com` URLs in tests are deliberate SSRF/URL-rejection fixtures, not internal infrastructure or live credentials. The repository owner name and GitHub noreply address are intentional public project metadata. Dependency-author addresses inside ignored installed packages are not project-private identities or publication content.

## Application and agent controls verified

| Control | Result and boundary |
| --- | --- |
| Local binding | CLI defaults to `127.0.0.1`. Non-loopback CLI binding without `OPENFOUNDRY_API_TOKEN` is rejected before Uvicorn starts. |
| REST authorization | Every documented `/api` inspection/mutation route rejects a missing operator token when token authentication is configured. No unauthorized operation executes. `/health`, static UI and API schema documentation intentionally remain public and do not expose mission records or credentials. |
| Local tokenless policy | Existing local development intentionally authorizes loopback clients without a token; it is **not universal credential authentication**. Non-loopback tokenless REST/WS clients are rejected. Set an operator token for deployments, reverse proxies and any shared local environment. |
| WebSockets | Authentication runs before mission inspection/accept; configured tokens use the Authorization header or `bearer.<token>` subprotocol with allowed-origin checks. Browser clients put only the replay cursor in the URL. Forged origins/invalid tokens are rejected. |
| Environment isolation | Real child pytest processes in both `guarded` and simulated-unavailable-namespace `auto` omit OpenAI/Gemini/Anthropic/Google, AWS, GitHub, npm, application/cloud tokens, credential file bindings, Python startup/preload and Node-option variables. A nested process verifies the research worker's sanitized inheritance. |
| Paths | Canonical relative-path validation, descriptor-relative Linux reads/writes, `O_NOFOLLOW`, regular-file/single-hardlink checks, root identity checks and portable link/reparse guards reject traversal, absolute paths, symlinks and hardlinks. API tests confirm private outside-file contents never return. |
| Commands | Agent tests accept only the existing pytest/npm-test/Ruff/Flake8 command grammar with bounded arguments, no shell strings, sanitized environment and a maximum 60-second deadline. Internal Git/worker helpers construct fixed argument vectors; no `create_subprocess_shell`/`os.system` path is used. Trusted developer asset tooling and browser drivers are distinct from the agent command interface. |
| Research SSRF | Only HTTP(S), permitted ports, no userinfo, public literal/DNS addresses, pinned destination IPs with original Host/TLS identity, checked redirects and a same-host browser proxy. `file://`, `gopher://`, metadata/link-local/private/multicast destinations and DNS rebinding are rejected. |
| QA SSRF | Only validated HTTP(S) origins, pinned DNS/address class, no redirects or cross-origin probe paths. Metadata/private/link-local destinations are rejected. Explicit loopback is intentionally permitted for operator-selected local test targets; arbitrary private LAN addresses are not. |
| Output/XSS | Source/diff/report text is rendered as React text; captured pages are not mounted as executable application HTML. The API returns file content in JSON and serves only the fixed built SPA/static directory. Existing contract and DOM tests remain enabled. |
| Costs and external tools | Existing atomic budget reservations, metered provider routing, persisted approvals and explicit MCP server/tool/department grants remain intact. MCP endpoints/executables are trusted operator configuration, not agent-controlled research URLs. |

## Remaining trust boundaries

Guarded execution is **not OS isolation**. Clearing child environment variables does not prevent a hostile program with the service account's privileges from reading accessible files, inspecting other same-user processes where the host permits it, contacting networks or attacking a tokenless local service. `auto` may deliberately select guarded execution on this cloud host. Use `strict` on a host with working Bubblewrap, and use appropriately isolated service accounts/containers and provider-side controls for untrusted workloads. The native Windows/macOS portability paths and successful real Bubblewrap isolation were not independently exercised here.

Plugins execute as trusted backend Python code. Operators granting external MCP tools trust their configured services; external tool charges are outside the LLM-token ledger. Redaction covers the configured standard Python log pipeline and tested application/SDK/access/error paths; it cannot police deliberately bypassed logging or arbitrary native/plugin output. Optional browser-use behavior was verified against its pinned source and adapter/worker tests; no live third-party browser/Docling integration is claimed when that optional dependency is absent.

No live credential was discovered requiring rotation. The already published private commit identity remains a **remote privacy remediation**, not a secret-key incident. Replacing `main` requires a reviewed force-with-lease push using the recorded expected old remote SHA; cached/old GitHub references may still require repository-host assistance. Do not treat a local rewrite as proof of remote erasure.

## Validation

- Security regression module: **18 passed**.
- Full backend regression suite: **408 passed**, including the 18 added security cases.
- Frontend unit tests: **34 passed**; TypeScript, ESLint, Prettier and production build passed.
- Chromium E2E: **6 passed** against development and FastAPI production after the final log-hardening changes.
- Ruff check/format, Flake8 and strict mypy: passed (44 application source files).
- Existing third-party test warnings are reported separately from failures; tests and assertions were not disabled.

Commands run from the repository root unless noted:

```bash
uv run --frozen pytest
uv run --frozen mypy
uv run --frozen ruff check backend scripts frontend/scripts
uv run --frozen ruff format --check backend scripts frontend/scripts
uv run --frozen flake8 backend scripts frontend/scripts --max-line-length=100 --extend-ignore=E203,W503
(cd frontend && npm run typecheck && npm run lint && npm run format:check && npm test && npm run build)
(cd frontend && PLAYWRIGHT_CHROMIUM_EXECUTABLE=/usr/bin/chromium npm run test:e2e)
gitleaks git --redact=100 --log-opts='--all --full-history' .
gitleaks dir --redact=100 .
```

Gitleaks and the private supplemental inventory are audit tooling, not new application runtime dependencies. The full file inventory does not follow filesystem symlinks or expand dependency archives into an unrelated host filesystem.

## Exact publication manifest and Git state

The final `git ls-files` list is recorded verbatim in [PUBLISH_MANIFEST.txt](PUBLISH_MANIFEST.txt). It includes source/tests, dependency lockfiles, documentation, the two media assets and three blueprints. Runtime workspaces, `.env`/database files, `.venv`, node_modules, caches, build outputs, audit tool downloads and the private recovery report are excluded.

The final command outputs, sanitized commit IDs and exact original identity are recorded in the private audit handoff report outside the repository. A clean `git status --short` describes local committed state only and does not imply the sanitized commits have been pushed. This audit does not authorize an automatic force-push.
