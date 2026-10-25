# Development checkpoint — 2026-10-25

Stopped at the owner's request. Resume only when asked. Branch:
`codex/v1-readiness`; owner and commit identity: **MrMha**. Version remains
**0.1.0**, with no release or merge. Draft
[PR #7](https://github.com/MrMha/openfoundry/pull/7) remains open.

## Changes saved

- Independent pytest ignores project configuration/conftest and addopts, and
  requires a bounded JUnit receipt with actual tests and no skips/errors/failures.
  Structural draft-test checks reject several vacuous patterns, but remain
  incomplete: see the independent review below.
- Metered test-contract reviews inspect draft engineering/acceptance tests
  against quoted requirements before freezing. Drafts and audits are retained.
  Review is design evidence, not proof that tests detect broken implementations.
- Repairs format new regression tests before freezing and preserve existing
  tests. Common caller/test-context workarounds are rejected. Recorded evasive
  CSV code is preserved byte-for-byte as `.py.txt` data; fixed external CSV checks
  now exercise different caller filenames.
- Planner output validates downstream schemas before dispatch, with metered
  corrections. Gemini reasoning effort is configurable (default medium); output
  is bounded at 8192 tokens and reported reasoning usage is counted.
- Fixed POSIX missing-parent reads, pytest script imports, a frontend dependency
  advisory, and a functional MCP deadline exposed by Linux execution. Dedicated
  short-deadline tests remain. Docker now uses HTTPS Debian indexes, retries,
  and explicit failure on index-update errors.

## Evidence and limits

The [sanitized summary](evidence/development-validation-2026-10-25.json) records
all eight live batches and their source revisions. Raw reports, databases,
generated workspaces, receipts, logs, and independent review remain locally in
ignored `.openfoundry/live-evaluations/` and `.openfoundry/validation/2026-10-25/`.
A local checkpoint archive is saved outside Desktop under
Documents/OpenFoundryCheckpoints. Raw evidence is not uploaded to public Git.

The previous checkout was missing when this session resumed. The historical
[October 22 summary](evidence/development-validation-2026-10-22.json) survives
in Git, but its ignored raw receipts were not recovered.

Results apply only to their recorded snapshots:

- GitHub backend CI on `6a5648f7eb14882b41f37652f3d4072653c50f84`:
  **512 passed**, plus lint/format and strict mypy (50 source files).
  [Backend job](https://github.com/MrMha/openfoundry/actions/runs/40202043823/job/121195071806).
  [Frontend CI](https://github.com/MrMha/openfoundry/actions/runs/40202043881)
  at that source also succeeded.
- Local frozen Linux `314891b`: **511 passed, no skips**. Earlier baseline:
  **478 passed, 3 failed, 1 skipped**; the failed run remains evidence.
- Earlier Windows baseline: **474 passed, 8 skipped**, before later changes.
  The frozen current-source Windows run was interrupted around 14% on shutdown;
  it has no complete result and must not be described as passed or failed.
- Local frontend: 34 unit and 6 development/production Playwright tests passed
  at their recorded checkpoints; dependency audit after fast-uri update: zero
  vulnerabilities.
- Wheel/sdist built before the latest changes; fresh installed-wheel smoke is
  pending. Docker image `openfoundry:validation-docker-fix` built after the HTTPS
  fix. Original failed build is retained. Container/Compose runtime smoke pending.
- Latest completed CSV batch `20261025T142428Z-e33a0b`: **3/3 end-to-end and
  fixed external passes**. Earlier failed batches remain evidence.
- Six-case/two-repeat campaign `20261025T143402Z-f2dc07` stopped after slugify
  and invoice repeat 1 passed. CSV repeat 1 was interrupted. The raw report still
  says `current_run.status = running`; a separate operator-stop receipt records
  shutdown. This is an incomplete campaign, not a 12-case result.

## Independent Antigravity review: unresolved blockers

The installed IDE used its observed **Gemini 3.8 Flash High** on an isolated
snapshot (`cdb92d8`, no remote). Actual backend execution: **497 passed, 8 skipped**.
Its collection and raw-fixture lint findings were fixed in main source; this
older snapshot does not certify later changes.

Its executable reproducer found three structural false-pass paths: bare
`pytest.raises` without a context manager, assertions inside `if False`, and
assertions disconnected from an unused production-interface reference. A broken
implementation raising `RuntimeError` passed a two-test acceptance suite and
produced a valid zero-failure JUnit receipt. This demonstrates validator/sandbox
false passes in that snapshot. It does not establish that the newer LLM
contract-review stage accepts the same suite; that integration is unverified.
Reproduce these on current source and fix them before release.

Evidence: `antigravity-review/.review-results/report.md`,
`test-design-review.md`, `reproduce_false_passes.py`, `reproducer_output.log`,
and raw execution receipts. No fixes were attempted after the stop instruction.

## Shutdown and credentials

Live evaluation and Windows pytest were stopped. Antigravity is idle. Newly
installed WSL Docker service/socket, containerd, and ubuntu-fan are inactive and
disabled. No validation containers remain running. Tools, snapshots and build
caches remain available for resumption.

The temporary plaintext API key was deleted; the existing provider key was not
revoked. AI Studio was observed on the requested account as **Free tier / Set up
billing**. Billing was not enabled and no paid fallback or new key/account was
configured. Live calls used Gemini 3.5 Flash Lite except an explicit 3.8 reviewer
attempt that timed out; small 3.8 availability probes also failed. Receipt costs
are estimates, not provider invoices. Recheck free-tier status before deliberately
restoring the existing key into a process-local environment.

## Resume order

1. Preserve failures/interrupted receipts; read the independent reproducer.
2. Reproduce and fix remaining false-pass paths. Include fixed negative controls
   proving acceptance tests reject broken implementations. Preserve independent
   external checks and never weaken frozen tests.
3. Run full current backend suites on Windows/Linux with Python 3.12 and locked
   dependencies (`.venv312` is ready). Rebuild/smoke-test the installed wheel,
   then Docker/Compose runtime paths.
4. Recheck unbilled API availability and start fresh live missions after offline
   guards pass. Keep interrupted mission/ledger state historical; do not silently
   replay it as a new result or relabel it as complete.
5. Finish repeated six-case and realistic multi-file adversarial campaigns under
   [V1_RELEASE_GATES.md](V1_RELEASE_GATES.md). Green CI and a small passing live
   batch are insufficient to certify v1.0.

The final pause commit uses `[skip ci]` to avoid starting more work. CI results
cover `6a5648f`; the later checkpoint adds documentation and the separately built
Dockerfile change.
