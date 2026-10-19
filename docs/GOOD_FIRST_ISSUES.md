# 25 contributor tickets you can claim

These are implementation briefs, **not already-created GitHub issues or assigned work**. Search the repository's issues first; copy one brief into a new issue if needed, reference its `OF-` identifier, and comment that you would like to take it. Ask for maintainer assignment before duplicating active work. `good first issue` and `help wanted` below are suggested labels, not assertions that those labels have already been applied remotely.

All paths are relative to the repository root. **(new)** denotes a proposed file that does not exist yet. Existing integration points must keep their documented contracts. See [CONTRIBUTING.md](../CONTRIBUTING.md); use deterministic fixtures and the existing async plugin signature, JSON output, router accounting and typed events. Department files need explicit discovery configuration or an installed entry point. Current blueprint schema 1 accepts the four canonical execution departments only; new-plugin tasks are initially planner-driven, not silently added to bundled blueprints.

## OF-01 — Security Red-Team: offline Python risk report

**Labels:** `help wanted`, `department` · **Size:** medium

**Scope:** Add a read-only `security_red_team` department that reviews mission Python files for a deliberately small set of patterns: `eval`/`exec`, shell-enabled subprocesses and embedded credential-like assignments. Produce findings with rule ID, path, line, severity and a remediation explanation. Describe this as a heuristic code review, not proof of security or a penetration test.

**Targets:** `backend/app/departments/security_red_team.py` **(new)**; `backend/tests/test_security_red_team.py` **(new)**; contract in `backend/app/departments/base_department.py`.

**Acceptance:** Register unique metadata; parse with `ast` without importing generated code; save `security/report.json` and `security/REPORT.md`; index a summary in memory; emit a typed thought. Fixtures cover positive/negative patterns, invalid Python, secret-value redaction and no source changes. No network or new executable allowlist entries.

## OF-02 — DevOps: Dockerfile and Compose drafts

**Labels:** `help wanted`, `department` · **Size:** medium

**Scope:** Add `devops` to propose deployment files from a mission's `SPEC.md` and manifests. Generate drafts inside the mission, separate from OpenFoundry's own root deployment files.

**Targets:** `backend/app/departments/devops.py` **(new)**; `backend/tests/test_devops.py` **(new)**; use `backend/app/core/llm_router.py` and `backend/app/core/workspace_sandbox.py` unchanged.

**Acceptance:** Meter generation; save `deployment/Dockerfile`, `deployment/compose.yaml` and a assumptions report; reject embedded secrets, privileged mode and unbounded arbitrary host mounts in the structured output validator. Tests cover missing manifests, rejected paths, cancellation and artifact registration. Do not build, run, push images or execute Docker from a plugin.

## OF-03 — Data Analyst: bounded CSV profile

**Labels:** `good first issue`, `department` · **Size:** small

**Scope:** Add `data_analyst` for an existing mission CSV: row count, column names, missingness, bounded unique counts and numeric min/max with explicit parsing failures.

**Targets:** `backend/app/departments/data_analyst.py` **(new)**; `backend/tests/test_data_analyst.py` **(new)**; reuse `backend/app/core/memory_vault.py`.

**Acceptance:** Use the standard-library CSV parser and sandbox read limits; enforce at most 10,000 rows/100 columns; save a machine-readable profile and Markdown report; record truncation and inferred-type uncertainty. Fixtures include quoted newlines, empty cells, malformed rows and invalid UTF-8. Never execute spreadsheet formulas or interpolate cells as instructions. No pandas dependency needed.

## OF-04 — API Load Tester: bounded ASGI timings

**Labels:** `help wanted`, `department` · **Size:** medium

**Scope:** Add an opt-in `api_load_tester` for a supplied local ASGI app using a dedicated pytest worker, reporting latency samples and HTTP failures. This is a small local regression probe, not an internet load generator.

**Targets:** `backend/app/departments/api_load_tester.py` **(new)**; `backend/app/departments/_load_probe_worker.py` **(new)**; `backend/tests/test_api_load_tester.py` **(new)**; follow `backend/app/departments/_qa_probe_worker.py`.

**Acceptance:** Import generated apps only in the sandbox worker; cap at 20 requests, concurrency 2 and the existing 60-second command deadline; no remote origins, redirects, shell or dependency installation. Save p50/p95 with sample count and method definition. Tests cover timeouts, startup failure, failed HTTP status and cancellation cleanup. No changes to the general subprocess allowlist.

## OF-05 — Accessibility Auditor: offline HTML checks

**Labels:** `good first issue`, `department` · **Size:** small

**Scope:** Add `accessibility_auditor` for saved mission HTML: document language, one descriptive title, image alternative text and form-control labels. Keep dynamic/visual accessibility explicitly out of the result.

**Targets:** `backend/app/departments/accessibility_auditor.py` **(new)**; `backend/tests/test_accessibility_auditor.py` **(new)**; use the installed BeautifulSoup dependency.

**Acceptance:** Produce deterministic rule IDs and element excerpts in `accessibility/report.json` and Markdown; include checks for `aria-label`, `aria-labelledby`, explicit labels and decorative images. Fixtures cover false-positive cases and malformed HTML. No web fetch, JavaScript execution or source rewriting; report unsupported WCAG checks as unassessed.

## OF-06 — API Documentation Writer from saved OpenAPI

**Labels:** `good first issue`, `department` · **Size:** small

**Scope:** Add `api_documenter` that reads an existing OpenAPI JSON artifact and renders an endpoint index, operation summaries and documented request/response media types.

**Targets:** `backend/app/departments/api_documenter.py` **(new)**; `backend/tests/test_api_documenter.py` **(new)**.

**Acceptance:** Bound document size using sandbox limits; support only local `$ref` references and detect cycles; save `api-docs/README.md` plus a source hash manifest. Fixtures include missing descriptions and unsupported schemas. Do not claim generated examples were sent to a server; no external refs or network calls.

## OF-07 — Dependency Reviewer for existing manifests

**Labels:** `help wanted`, `department` · **Size:** medium

**Scope:** Add `dependency_reviewer` that inventories declared dependencies in mission `pyproject.toml` and `package.json`, flags unpinned ranges, and documents license/security fields as unknown unless evidence is supplied.

**Targets:** `backend/app/departments/dependency_reviewer.py` **(new)**; `backend/tests/test_dependency_reviewer.py` **(new)**.

**Acceptance:** Parse TOML/JSON without importing packages; list manifest path and dependency group; preserve source and distinguish declared from installed versions. Test extras, malformed documents and credential-bearing URLs with redaction. Do not run pip/npm install, fetch advisories or claim vulnerability detection in this first ticket.

## OF-08 — Release Notes Writer from existing diff evidence

**Labels:** `good first issue`, `department` · **Size:** small

**Scope:** Add `release_notes` that consumes saved Engineering commit manifests and QA reports to write a reviewable changelog draft with source references.

**Targets:** `backend/app/departments/release_notes.py` **(new)**; `backend/tests/test_release_notes.py` **(new)**; evidence format in `backend/app/departments/engineer.py`.

**Acceptance:** Cite artifact paths/commit IDs; label unverified changes and missing reports; route any LLM work through the existing router. Save `release/CHANGELOG_DRAFT.md` and a manifest. Test an unverified fix and successful repair separately. No Git remote, release tagging, publication or invented semantic version.

## OF-09 — Localization Reviewer: JSON key parity

**Labels:** `good first issue`, `department` · **Size:** small

**Scope:** Add `localization_reviewer` that compares a source locale JSON object with one or more target locale objects and reports missing/extra keys and placeholder mismatches.

**Targets:** `backend/app/departments/localization_reviewer.py` **(new)**; `backend/tests/test_localization_reviewer.py` **(new)**.

**Acceptance:** Define a documented bounded nested-key and `{name}` placeholder grammar; reject duplicate JSON keys; preserve Unicode and distinguish non-string leaves. Save `localization/report.json`; do not translate or overwrite inputs. Tests cover nested keys, escaped braces and duplicate keys.

## OF-10 — Documentation QA: contained Markdown links

**Labels:** `good first issue`, `department` · **Size:** small

**Scope:** Add `documentation_qa` to inspect mission Markdown links to local files and report broken relative targets, ignoring external URLs.

**Targets:** `backend/app/departments/documentation_qa.py` **(new)**; `backend/tests/test_documentation_qa.py` **(new)**.

**Acceptance:** Resolve links relative to each Markdown file through sandbox containment; handle fragment-only links separately; never follow symlinks or `..` outside the mission. Save `documentation/link-report.json` and a summary. Fixtures include fenced code, images, encoded spaces and missing paths. Do not claim full CommonMark coverage unless the parser supports it.

## OF-11 — Blueprint: FastAPI service starter

**Labels:** `good first issue`, `blueprint` · **Size:** small

**Scope:** A parameterized Python API mission with domain, resource name and persistence choice, using the four existing execution departments.

**Targets:** `blueprints/fastapi_service_starter.json` **(new)**; cases in `backend/tests/test_mcp_blueprints.py`.

**Acceptance:** Resolve defaults; require Engineering approval; specify OpenAPI, health and CRUD tests, plus a launch README. Validate unique DAG keys and unknown-parameter rejection. Keep the $2.00 suggestion and explain missing dependencies; no deployment promise. The existing API/UI must discover it without hardcoded frontend options.

## OF-12 — Blueprint: local document research notebook

**Labels:** `help wanted`, `blueprint` · **Size:** medium

**Scope:** A `local_document_research` template that organizes existing workspace documents into cited findings, a small keyword-retrieval prototype and a reviewer summary.

**Targets:** `blueprints/local_document_research.json` **(new)**; cases in `backend/tests/test_mcp_blueprints.py`.

**Acceptance:** Parameters include a real input path and question; stop with a useful error when documents are absent. Use only canonical departments, preserve Engineering approval, and request tests for citation-to-source mapping. Do not promise embeddings, OCR or an upload endpoint. All defaults must validate without pretending an input file exists.

## OF-13 — Blueprint: static-site accessibility repair

**Labels:** `good first issue`, `blueprint` · **Size:** small

**Scope:** A site-source mission emphasizing semantic HTML, keyboard interactions and defined DOM assertions through the existing QA department.

**Targets:** `blueprints/static_site_accessibility.json` **(new)**; cases in `backend/tests/test_mcp_blueprints.py`.

**Acceptance:** Require existing source context, a target journey and verifiable selectors; distinguish automated DOM checks from manual/visual review. Keep approved Engineering and a QA dependency before Growth. Do not reference the unimplemented Accessibility Auditor plugin from OF-05 in schema-1 tasks.

## OF-14 — Blueprint: tested Python library

**Labels:** `good first issue`, `blueprint` · **Size:** small

**Scope:** A library mission with API examples, packaging metadata, boundary tests and documentation instead of website checks.

**Targets:** `blueprints/python_library_builder.json` **(new)**; cases in `backend/tests/test_mcp_blueprints.py`.

**Acceptance:** Include package-name/purpose parameters, approved Engineering, pytest/Ruff acceptance and explicitly skipped browser verification for a library. Require fewer than 200 lines per generated module. No PyPI upload or package installation from generated commands.

## OF-15 — Blueprint: evidence-backed career portfolio

**Labels:** `good first issue`, `blueprint` · **Size:** small

**Scope:** A `career_portfolio_refresh` template focused on organizing supplied profile/project evidence into portfolio narratives and targeted outreach drafts.

**Targets:** `blueprints/career_portfolio_refresh.json` **(new)**; cases in `backend/tests/test_mcp_blueprints.py`.

**Acceptance:** Use Researcher → Growth/Career, canonical department names and bounded parameters. Require the evidence file, flag unsupported personal claims, and never send messages. Tests cover custom paths, unknown parameters and missing-input instructions. Do not include an unnecessary code-generation task.

## OF-16 — Blueprint: incident postmortem drafts

**Labels:** `help wanted`, `blueprint` · **Size:** medium

**Scope:** A template for existing sanitized incident notes/logs that extracts a cited timeline and drafts a postmortem with unresolved questions.

**Targets:** `blueprints/incident_postmortem.json` **(new)**; cases in `backend/tests/test_mcp_blueprints.py`.

**Acceptance:** Distinguish observed facts from hypotheses and timestamps from guessed ordering; use canonical Researcher/Growth tasks. Explicitly prohibit contacting services, changing infrastructure or inventing an outage root cause. Require redacted inputs and a reviewer sign-off draft; loader/default-resolution tests pass.

## OF-17 — DAG keyboard navigation

**Labels:** `good first issue`, `frontend`, `accessibility` · **Size:** small

**Scope:** Extend the existing Tab/Enter/Space task selection with arrow-key traversal and a roving focus target for larger DAGs.

**Targets:** `frontend/src/components/WarRoomGraph.tsx`; `frontend/src/components/WarRoomGraph.test.tsx`; `frontend/e2e/demo.spec.ts`.

**Acceptance:** A documented arrow-key order, visible focus and Enter/Space selection; every node announces department/title/status. Tab must not trap focus, and live rerenders must preserve the active node where possible. Add keyboard assertions and a Chromium check without changing API schemas.

## OF-18 — Persisted pause-motion control

**Labels:** `good first issue`, `frontend`, `accessibility` · **Size:** small

**Scope:** Add an operator-visible pause-motion control on top of the existing `prefers-reduced-motion` CSS. Let a user pause animation even when the operating system has no reduced-motion preference.

**Targets:** `frontend/src/components/MotionPreference.tsx` **(new)**; `frontend/src/components/MotionPreference.test.tsx` **(new)**; `frontend/src/App.tsx`; `frontend/src/styles.css`; `frontend/e2e/demo.spec.ts`.

**Acceptance:** Persist only the boolean UI preference, apply it before animated panels mount, and keep the OS reduced-motion rule effective even when the app control is off. Status stays visible through text/icon/color. Test toggle/persistence, OS preference and unavailable localStorage; no JS polling or extra sockets. Capture both visual states for review.

## OF-19 — Accessible budget meter

**Labels:** `good first issue`, `frontend`, `accessibility` · **Size:** small

**Scope:** Improve spoken budget/cost information and the over-budget visual state without altering backend accounting.

**Targets:** `frontend/src/components/CommandBar.tsx`; `frontend/src/components/CommandBar.test.tsx`.

**Acceptance:** Announce amount, currency and limit; parse decimal strings without displaying `NaN`; provide a restrained live announcement on meaningful thresholds rather than every event. Test zero budget-display edge cases, tiny decimal cost and exhausted budget. Do not infer available spend by dropping reserved cost.

## OF-20 — Inspect per-frame QA provenance

**Labels:** `good first issue`, `frontend` · **Size:** small

**Scope:** Extend the existing capture-source label with an expandable per-frame provenance view and a copy-report-path action.

**Targets:** `frontend/src/components/LiveBrowserViewer.tsx`; `frontend/src/components/LiveBrowserViewer.test.tsx`; `frontend/src/services/qaEvidence.ts`.

**Acceptance:** Show actual backend, frame timestamp, target URL and the contained probe-report artifact path; copy only that path, without tokens or URL queries. Reuse the current fetched evidence and retain the existing diagnostic warning. Missing source stays unknown. Test copy success/failure, absent evidence and frame changes without inventing a BrowserFrameEvent field or making another fetch.

## OF-21 — Search within the selected diff

**Labels:** `good first issue`, `frontend` · **Size:** small

**Scope:** Add local literal-text search over the currently selected diff revision.

**Targets:** `frontend/src/components/CodeDiffInspector.tsx`; `frontend/src/components/CodeDiffInspector.test.tsx`.

**Acceptance:** Show match count and next/previous controls; preserve hunk line numbers; reset search position on revision changes; render user input as text, never HTML or an unchecked regex. Test empty/no-match/multiple-match cases in unified and side-by-side modes.

## OF-22 — Filter artifacts by type

**Labels:** `good first issue`, `frontend` · **Size:** small

**Scope:** Add a lightweight artifact-type filter to the existing mission file inspector.

**Targets:** `frontend/src/components/CodeDiffInspector.tsx`; `frontend/src/components/CodeDiffInspector.test.tsx`; existing types in `frontend/src/services/types.ts`.

**Acceptance:** Generate options from real `artifact_type` values, keep a visible all-files option and stable selection, and show an empty filtered state. Filtering makes no extra fetch and preserves file-download behavior. Cover unknown third-party artifact types.

## OF-23 — Reset blueprint parameters with an edit guard

**Labels:** `good first issue`, `frontend` · **Size:** small

**Scope:** Add a reset-to-template-defaults action with explicit handling of a manually edited goal.

**Targets:** `frontend/src/components/CommandBar.tsx`; `frontend/src/components/CommandBar.test.tsx`; `frontend/src/services/blueprints.test.ts`.

**Acceptance:** Restore parameter defaults and suggested configuration within the server ceiling; do not silently overwrite an edited goal. Test pristine reset, dirty-goal confirmation/cancellation and a nonconfigured suggested provider. Preserve backend submission field names and avoid hardcoded templates.

## OF-24 — External MCP status panel

**Labels:** `help wanted`, `frontend`, `mcp` · **Size:** medium

**Scope:** Display the existing `GET /api/plugins` MCP status alongside department/sandbox metadata.

**Targets:** `frontend/src/components/MCPStatusPanel.tsx` **(new)**; `frontend/src/components/MCPStatusPanel.test.tsx` **(new)**; `frontend/src/App.tsx`; contract in `backend/app/api/routes_plugins.py`.

**Acceptance:** Render ready/error/unprobed/disabled, checked time, tool names and allowlist state; reuse the current catalog refresh instead of opening a new poller/socket. Explain that ready is a recent probe, not a persistent connection. Empty config has a useful state; no endpoint, header or environment secrets are exposed. Use generated types unchanged unless an actual backend schema change is justified.

## OF-25 — Redacted mission diagnostics download

**Labels:** `help wanted`, `frontend`, `diagnostics` · **Size:** medium

**Scope:** Add an operator-triggered JSON download of a bounded mission troubleshooting snapshot from already-loaded state.

**Targets:** `frontend/src/services/diagnostics.ts` **(new)**; `frontend/src/services/diagnostics.test.ts` **(new)**; `frontend/src/App.tsx`.

**Acceptance:** Include schema version, mission/task status, cost values and sandbox mode; exclude goals, descriptions, file contents, raw events, provider/operator tokens, URL queries and localStorage by default. Show a preview and an explicit private-data warning before download; never upload automatically. Tests assert forbidden fields stay absent and download URLs are revoked. No new REST route is required.

## Completion evidence

Each ticket should ship focused deterministic tests, any required contract regeneration, and the checks listed in [CONTRIBUTING.md](../CONTRIBUTING.md#validation). For UI work include screenshots; for new plugins include an offline task/artifact example. Keep these briefs updated when a ticket is implemented, and link the real issue/PR only after it exists.
