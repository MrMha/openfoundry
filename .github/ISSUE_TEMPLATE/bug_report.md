---
name: Bug report
about: Report a reproducible backend, War Room, sandbox or MCP defect
labels: bug
---

## Observed behavior

Describe what happened and the expected result. If a check was skipped or used a diagnostic fallback, distinguish that from a failing browser test.

## Reproduction

1. Commit/version and install/start command:
2. Mission goal or blueprint ID and non-sensitive parameters:
3. Demo or live mode, and approval action taken:
4. Exact steps and observed result:

Can the key-free Self-Healing Demo reproduce this? If not, attach a minimal synthetic input that can.

## Environment

- OS/architecture; local, Docker or cloud:
- Python/uv and Node/npm versions:
- Browser/version or MCP client/transport:
- Configured sandbox mode and measured `/api/plugins` execution mode:
- Optional adapters installed (Playwright, browser-use, Docling):
- Provider/model names if relevant (never keys):
- Single or multiple backend processes sharing a SQLite file:

## Evidence

Paste bounded, redacted logs or a failing test command/result. Relevant paths can include `qa/<task_id>/report.json`, `engineering/<task_id>/`, `research/<task_id>/manifest.json`, or `frontend/test-results/`. Include mission/task IDs only when safe. For budgets, include amounts and attempt states without prompt or credential dumps.

**Do not attach API/operator tokens, `.env` files, authorization headers, WebSocket bearer subprotocol values, private profiles/research, raw databases or unreviewed screenshots.** Report security-sensitive vulnerabilities privately to repository maintainers through an available private channel; do not publish exploit details or secrets in an issue. There is no dedicated security email configured in this template.

## Checks

- [ ] I checked existing issues and `CONTRIBUTING.md`.
- [ ] I separated expected approval waits, missing inputs and guarded-mode limitations from the defect.
- [ ] I included a reproducible command or minimal example.
- [ ] I removed secrets and private mission data from all evidence.
