---
name: New department plugin
about: Propose a focused, typed OpenFoundry department
labels: enhancement
---

## Department identity

- Proposed lowercase `name`:
- One-sentence `role_description`:
- Capabilities and intended users:
- Existing department behavior this complements:

## Concrete mission

Describe an example goal, required workspace inputs, proposed DAG position/dependencies, and the files or findings produced. Include missing-input behavior and measurable acceptance criteria.

## Interface and registration

- Proposed path: `backend/app/departments/<name>.py`
- Implements the existing `BaseDepartment.execute_task` signature and JSON `DepartmentOutput`:
- Discovery via `OPENFOUNDRY_DEPARTMENT_PLUGINS` or an installed entry point:
- Sandbox file paths and artifact types:
- Typed thought/frame/diff events emitted:
- Shared-memory inputs/outputs:

## Execution and approval needs

Describe network destinations, optional dependencies, LLM calls through `self.router`, external MCP grants and any operator approval gates. Explain whether this is read-only or creates an external effect. Plugins run as trusted service code; a new capability does not expand the sandbox command allowlist automatically.

## Test plan

- Deterministic fixture mission and expected artifacts:
- Invalid input/output and missing-dependency cases:
- Concurrent missions and cancellation:
- Cost ceiling and timeout behavior:
- Proposed test path: `backend/tests/test_<name>.py`

## Delivery scope

- [ ] I read `CONTRIBUTING.md` and `backend/app/departments/base_department.py`.
- [ ] This proposal preserves scheduler-owned statuses and approvals.
- [ ] Live providers/services are optional; automated tests require no paid keys.
- [ ] UI and blueprint limitations for noncanonical departments are addressed explicitly.
- [ ] This proposal does not include secrets, private inputs or unapproved publication.

Link a ticket from `docs/GOOD_FIRST_ISSUES.md` if applicable and say whether you want to implement it. Maintainers assign work explicitly; posting this template does not create an automatic assignment.
