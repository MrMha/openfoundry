# Development checkpoint — 2026-10-22

Work paused at the owner's request. Branch: `codex/v1-readiness`. This is a
development checkpoint, **not a v1.0 release or a reliability certification**.
The package version remains 0.1.0. No release, deployment, or billing setup was
performed. No evaluation jobs are left running.

## Changes saved

- Windows research worker output now uses explicit UTF-8. Protected paths and
  generated-file uniqueness are checked without case-sensitive aliases.
- Engineering validates straightforward ASGI factory/export mismatches, returns
  bounded validation feedback to the model, and runs static Ruff checks on draft
  files before the existing review freezes generated tests.
- Independent QA is enabled by default. It generates acceptance tests from the
  requirements and public signatures, with a separate required empty/missing
  input test source. Its tests are protected during repair. Legacy fixed-response
  test fixtures and the deterministic demo explicitly disable generation.
- An optional `OPENFOUNDRY_ENGINEERING_REVIEW_MODEL` selects an explicit review
  model with the same budget and ledger. Unit coverage passes; a stronger model
  has **not** been tested live in this role.
- The live evaluator now checkpoints results, records source hashes and pending
  mission IDs, bounds execution, and grades delivered production Python modules
  with fixed external checks. Generated tests and configuration are excluded
  from that external grading workspace. Added graph, inventory, and FastAPI
  acceptance cases, reviewed after advisory input from Gemini web chat.
- Wheels include the three blueprint templates. Windows SPA routing handles
  native separators correctly. Platform-specific sandbox tests now reflect
  available OS capabilities; integration deadlines accommodate measured cold
  subprocess startup times without removing functional assertions.

## Evidence and limitations

The sanitized [validation summary](evidence/development-validation-2026-10-22.json)
records each live batch, including failures. Raw receipts, logs, databases, and
generated workspaces remain locally under ignored `.openfoundry/validation/`
and `.openfoundry/live-evaluations/`. They are not included in Git.

All 26 recorded live missions used uncached `gemini/gemini-3.5-flash-lite` calls
on the existing AI Studio project observed as **Free tier / Set up billing**.
No paid fallback or new provider account was configured. Cost values in receipts
are estimated list prices; provider invoice amounts are unknown. The temporary
plaintext API key is removed at shutdown; retrieve the existing key from AI
Studio when resuming. Do not enable billing. The runner requires
`--unbilled-project-confirmed`; that flag is an operator confirmation, not an
automatic billing-account check.

Results span successive source revisions and are not one final-version benchmark:

| Live batch | Scope | End-to-end passes | External acceptance passes |
| --- | --- | ---: | ---: |
| `20261021T190240Z-13ac42` | Original slugify, research encoding failure | 0/1 | 0/1 |
| `20261021T190752Z-e4177b` | Slugify after encoding fix | 1/1 | 1/1 |
| `20261021T191333Z-935055` | Six cases | 4/6 | 6/6 |
| `20261021T193023Z-2acbc7` | Six cases, two repetitions | 8/12 | 10/12 |
| `20261021T195048Z-68314a` | CSV with first independent-QA design | 1/3 | 1/3 |
| `20261021T195621Z-985358` | CSV with required empty-input test source | 1/3 | 3/3 |

In the two-repetition batch, one completed CSV mission failed external checks;
the first independent-QA design produced another two such false completions.
The latest three CSV deliveries passed external checks, but two missions failed
their own generated QA. Generated CSV tests can contain invalid assumptions;
repair correctly refuses to weaken frozen tests. This remains a release blocker.
The latest optional review-model change was made after these live batches.

Recorded local verification:

- Latest focused router, release-regression, QA diagnostic, and phase-two tests:
  **53 passed**. Latest strict mypy: **46 source files passed**.
- Independent-QA tests: **5 passed**; earlier combined new-feature checks:
  **72 passed**. Engineering suite: **32 passed**. External evaluator tests:
  **18 passed**. These runs cover different checkpoints; do not sum the counts.
- SPA checks after the Windows fix: **18 passed**. Frontend: **34 unit tests**
  and **6 Playwright E2E tests passed**, plus typecheck/lint/format/build checks
  at their recorded checkpoint.
- Isolated installed-wheel smoke check passed health and three blueprint
  templates. That wheel predates the latest QA/review changes.
- Earlier full Windows suite: **456 passed, 6 failed, 8 skipped**. Its four SPA
  failures and two integration deadline failures were subsequently addressed;
  relevant focused reruns passed. The **full current suite has not been rerun**.
- Earlier WSL source snapshot: **468 passed, 1 failed, 1 skipped**. The failure
  was the QA diagnostic deadline subsequently increased locally. That snapshot
  predates independent QA and is not validation of the final source.
- An earlier combined Windows run encountered a native access violation during
  MCP work. Later isolated MCP integration and cancellation runs passed. The
  original native crash's cause remains unproven.

## Resume here

1. Review this checkpoint and the sanitized evidence before interpreting results.
   Preserve the existing failed receipts; never replace them with passing runs.
2. Use Python 3.12 with locked dependencies. The local `.venv312` is ready;
   `.venv` contains the earlier 3.14 attempt. Run the entire backend suite on
   current source on Windows and Linux, then rebuild and smoke-test the wheel.
3. Improve generation/review of specification-correct tests without allowing
   repair to weaken frozen tests. Repeat the CSV false-completion and invalid-test
   cases with independent fixed acceptance checks and fresh missions.
4. If using a stronger engineering reviewer, first verify its current free-tier
   availability in AI Studio. `--review-model` is explicit; it was not used in
   the saved live batches. Keep the provider Gemini-only and respect quotas.
5. Escalate to repeated six-case and broader adversarial acceptance campaigns
   only after focused cases pass. A few passes do not establish v1.0 reliability.

Useful commands (PowerShell, repository root; configure the API key only in the
current process environment when deliberately resuming live work):

```powershell
$env:UV_PROJECT_ENVIRONMENT = '.venv312'
uv sync --frozen --extra dev --python 3.12
.venv312/Scripts/python.exe -m pytest backend/tests -q
.venv312/Scripts/python.exe -m mypy backend/app
.venv312/Scripts/python.exe -m scripts.run_live_evaluation --help
```

Frontend checks run from `frontend/` with the locked npm install. WSL's previous
source snapshot and receipts are under
`~/.cache/openfoundry-validation/01a15fce/source`; refresh that source before a
new Linux run. Local evidence is retained for the next session, with no scheduled
background continuation.
