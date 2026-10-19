# OpenFoundry launch kit

Ready-to-copy drafts for the implemented `0.1.0` project. Repository: **https://github.com/MrMha/openfoundry**. License: **MIT**. These are publishing materials; no post, message, issue or pull request is created by this file.

Use the actual assets: [four-stage demo GIF](assets/war-room-demo.gif), [2× hero screenshot](assets/war-room-hero.png), and [MCP client setup](mcp.md). For services requiring hosted media, upload those files rather than a GitHub HTML blob page. The checked-in GIF shows a real fixture-backed mission; backend scheduling, Git/test execution and repair verification are real.

## Shared fact sheet

| Claim | Supported scope |
| --- | --- |
| Five departments | Chief of Staff, Researcher, Engineer, QA Self-Healer, Growth/Career. |
| Demo | Five departments, one seeded-defect repair, 29 registered artifacts, $0.00 API-token cost; Engineering still asks for approval. Provider/research fixtures make it repeatable. |
| Local execution | Python backend + React War Room; SQLite and mission workspaces persist locally. Docker serves API + built UI on port 8000. |
| Models | Server-side BYOK OpenAI/Gemini/Anthropic and local Ollama routing, schema validation and a durable budget ledger; default mission ceiling $2.00. |
| QA | Actual test subprocesses and ASGI/HTTP/DOM checks, up to three repairs after the initial round; browser adapters when available, labeled diagnostics otherwise. |
| MCP | Official SDK; `openfoundry mcp` proxies an already-running API over stdio. Departments can call explicitly configured/granted external MCP tools. |
| Verification | Locally verified baseline: 408 backend + 34 frontend + 6 Chromium E2E tests. Remote CI status comes from workflow badges, not this count. |
| Roadmap | Chat approvals, GitHub PR publication and a community registry are future specifications, not current capabilities. |

## Hacker News

**Title**

```text
Show HN: OpenFoundry – an AI software team that tests and repairs its own code
```

**Link**

```text
https://github.com/MrMha/openfoundry
```

**First comment / text submission**

```text
I’m building OpenFoundry, an MIT-licensed software and research studio with five departments: Chief of Staff, Research, Engineering, QA and Growth/Career.

The part I wanted to make inspectable is the repair loop. You can start a key-free demo, review its specification, approve Engineering, watch an arithmetic/readiness defect fail real checks, inspect the committed patch, and watch verification pass. That run produces 29 registered artifacts after one repair at $0.00 API-token cost. Provider responses and research are deterministic fixtures; SQLite, DAG scheduling, Git, test subprocesses and ASGI/DOM checks run for real.

Live work uses your OpenAI/Gemini/Anthropic credentials or Ollama. There’s a durable budget ledger with conservative reservations, typed/replayable WebSocket events, per-mission memory, and an MCP server/client integration for terminal/editor workflows.

The sandbox reports whether Bubblewrap actually works. Hosts without namespaces use guarded execution; that mode is not an OS jail. The QA loop has a three-repair limit and can fail visibly.

The README has the GIF, local and Docker setup, and a small department-plugin example. I’d appreciate feedback on the verification evidence and plugin contract, particularly what would make a generated repair convincing enough to review.
```

## Reddit: r/LocalLLaMA

**Title**

```text
OpenFoundry: an MIT AI software team with Ollama routing and a real test → patch → retest loop
```

**Body**

```markdown
I’m building **OpenFoundry**, a local software/research studio with five departments and a live War Room. It can route through local Ollama (`OLLAMA_BASE_URL`, model default `ollama_chat/llama3.2`) or server-side OpenAI, Gemini and Anthropic credentials.

The easiest way to inspect it is the **key-free self-healing demo**: approve the specification, watch actual tests and DOM checks catch a seeded defect, inspect the Engineering diff, then watch the checks pass. Current demo: **5 departments, 1 repair, 29 artifacts, $0.00 API-token cost**. Its model/research responses are fixtures; the rest of that execution is real. No Ollama server is needed for this demo.

For live local-model work, Ollama must already serve the selected model and support the requested structured output. Files, SQLite and mission memory stay on your machine; research may fetch public sources you request. Ollama’s API-token cost is recorded as zero, which excludes hardware/electricity costs.

It also exposes an MCP stdio server through `openfoundry mcp`, and departments can use explicitly granted external MCP tools. QA retries are bounded; failed work stays visible. The measured sandbox mode is shown in the UI, including guarded fallback when Bubblewrap namespaces are unavailable.

**Code + demo GIF:** https://github.com/MrMha/openfoundry

I’d like feedback from people testing local coding models: what model/context settings give you reliable structured plans and small, reviewable bug-fix patches?
```

## Reddit: r/opensource

**Title**

```text
OpenFoundry (MIT): a five-department AI studio with a reproducible, key-free self-healing demo
```

**Body**

```markdown
I’m opening up **OpenFoundry**, an autonomous software and research studio built with Python/FastAPI, SQLite, React/TypeScript and the official MCP SDK.

There are five departments: Chief of Staff, Researcher, Engineer, QA Self-Healer and Growth/Career. The War Room shows the task DAG, thoughts, human approval gate, verification evidence, source diffs and generated files.

The included demo needs no external API key. It uses deterministic provider/research fixtures while running the actual scheduling, Git commits, tests and ASGI/DOM verification. You can watch a seeded bug fail, approve/inspect the workflow, and see one committed repair verified. It finishes with **29 artifacts at $0.00 API-token cost**.

Live missions support BYOK or local Ollama, with durable per-mission budget accounting. The backend/UI can run together through Docker Compose. Guarded fallback is explicit; it does not pretend to provide OS isolation.

I’ve included a typed plugin interface, three validated JSON blueprints, contributor instructions and **25 scoped ticket briefs** covering new departments, templates and UI/accessibility improvements. Everything is under **MIT**.

**Repository, screenshots and quickstart:** https://github.com/MrMha/openfoundry

If you want to build an Accessibility Auditor, Data Analyst or another department, the contributor guide shows how registration, artifacts, memory and metered calls fit together. I’d welcome small, testable contributions and feedback on the interface.
```

## Reddit: r/programming

**Title**

```text
OpenFoundry: durable DAG orchestration, explicit approvals, and a bounded QA repair loop in Python
```

**Body**

```markdown
I’m building **OpenFoundry**, an MIT software/research studio organized as five department plugins. The engineering question is how to make a multi-agent workflow inspectable when tests fail, a process stops, or an external call may already have been billed.

The current implementation uses async SQLAlchemy/SQLite for mission state, task approvals, an LLM-attempt ledger and a replayable event log. State changes and events commit together. One coordinator owns a database. Pending tasks/approval waits recover; interrupted running tasks fail visibly instead of blindly repeating external effects.

Engineering writes modular files and commits a branch. QA runs bounded test subprocesses and ASGI/HTTP/DOM checks, records a structured bug report, requests a patch and reruns the same acceptance checks. It allows at most three repair rounds. The repair API protects original acceptance files; this remains a trust boundary rather than a proof against hostile generated programs.

A key-free demo makes the failure → patch → pass path reproducible: real execution with fixture provider/research responses, five departments, one repair and 29 artifacts at $0.00 API-token cost. The UI shows the diff and evidence. There’s also an official-SDK MCP facade and explicitly granted external MCP tools.

**Source + GIF:** https://github.com/MrMha/openfoundry

The current baseline is 408 backend, 34 frontend and 6 Chromium tests verified locally. I’d appreciate review of the transaction/replay boundaries, conservative spend reservations and department contract.
```

## X: seven-post launch thread

Post in this order, as replies to the first post. Each block is a complete post below 280 characters even when counting the literal URL. Attach the GIF to post 1 and the hero screenshot to post 4 if desired.

### 1/7 — Hook

```text
1/7 If an AI agent is a freelancer, OpenFoundry is your autonomous software & research company—with a QA department that tests and self-heals its code. MIT, local War Room, visible diffs. https://github.com/MrMha/openfoundry
```

### 2/7 — Company workflow

```text
2/7 Five departments: Chief of Staff → Researcher → Engineer → QA Self-Healer → Growth/Career. The CEO gives a goal, reviews the spec, and approves Engineering. Every task, thought and approval appears in the War Room.
```

### 3/7 — Observable repair

```text
3/7 The key-free demo catches a seeded bug with real tests + DOM checks, commits one patch, and verifies it. Five departments, 29 artifacts, $0.00 API-token cost. Model/research replies are fixtures; scheduling, Git and checks execute for real.
```

### 4/7 — Evidence

```text
4/7 Watch the DAG, measured sandbox mode, failed checks, patch diff and passing verification in one view. QA has a three-repair limit. Original acceptance checks are protected from repair edits; unsuccessful work stays visibly failed.
```

### 5/7 — Models and budgets

```text
5/7 Bring OpenAI, Gemini or Anthropic keys—or route to local Ollama. A durable ledger reserves and records mission spend; the default ceiling is $2.00. Ollama token cost excludes hardware. Guarded sandbox fallback is labeled, not sold as OS isolation.
```

### 6/7 — Editor workflow

```text
6/7 Already in a terminal/editor? openfoundry mcp exposes mission, status, demo, department, blueprint and approval tools through the official MCP SDK. It connects to the running API. Departments can also use explicitly granted external MCP tools.
```

### 7/7 — Contributor invitation

```text
7/7 Build the next department: Security Red-Team, DevOps, Data Analyst, Load Tester or Accessibility Auditor. There are 25 scoped ticket briefs, typed plugin contracts and three starter blueprints. Start with the demo: https://github.com/MrMha/openfoundry
```

## Awesome-list pull request entries

Select the intended repository by its exact list title, check its current contribution format/category, and paste the corresponding entry. These list names do not specify unique owners; this kit does not invent target URLs or claim acceptance, existence of a particular fork, or a submitted PR. Each proposal links the verified OpenFoundry repository. Add the real target repository/PR link only after choosing it.

### awesome-mcp-servers

**Suggested category:** developer tools / agent orchestration.

**PR title:** Add OpenFoundry — software-studio MCP server

**Entry**

```markdown
- [OpenFoundry](https://github.com/MrMha/openfoundry) — MIT software/research studio with an official-SDK stdio MCP server for starting missions, checking tasks/costs, running a key-free self-healing demo, listing departments/blueprints, and recording explicit approval decisions. Python; local API required.
```

**PR body**

```markdown
Adds OpenFoundry under developer tools / agent orchestration.

- Server command and setup: https://github.com/MrMha/openfoundry/blob/main/docs/mcp.md
- The stdio facade proxies the running FastAPI API; operator authentication and Engineering approval are preserved.
- Includes an offline fixture-backed demo and automated MCP transport tests. Licensed MIT.
```

### awesome-mcp-collection

**Suggested category:** developer tooling / MCP servers and clients.

**PR title:** Add OpenFoundry bidirectional MCP integration

**Entry**

```markdown
- [OpenFoundry](https://github.com/MrMha/openfoundry) — Local AI software studio offering a stdio MCP server and a guarded external MCP client bridge. Five departments share mission context, explicit tool grants, approvals and a durable budget ledger; includes a reproducible self-healing demo. MIT, Python.
```

**PR body**

```markdown
Adds a developer-tool integration that both exposes MCP tools and consumes configured external tools through the official Python SDK.

Setup and capability limits: https://github.com/MrMha/openfoundry/blob/main/docs/mcp.md

External server/tool/department permissions are allowlisted; the key-free demo runs without provider credentials. No external tools are enabled by default.
```

### awesome-ai-agents-2026

**Suggested category:** multi-agent software engineering / frameworks.

**PR title:** Add OpenFoundry five-department software studio

**Entry**

```markdown
- [OpenFoundry](https://github.com/MrMha/openfoundry) — MIT autonomous software and research studio with five department plugins, validated DAG planning, human approval gates, shared mission memory, and a bounded test → patch → retest loop. Includes a live React War Room, BYOK/Ollama routing and MCP integration.
```

**PR body**

```markdown
Adds a software-engineering agent framework with inspectable verification and a key-free deterministic demo.

The demo executes real Git/tests/ASGI checks with fixture provider/research responses, one repair and 29 artifacts. Live missions use configured providers; publication remains a separate operator action.

Repository includes contributor contracts, validated blueprints, screenshots, CI workflows and an MIT license.
```

### awesome-local-ai

**Suggested category:** local development tools / multi-agent applications.

**PR title:** Add OpenFoundry with local Ollama routing

**Entry**

```markdown
- [OpenFoundry](https://github.com/MrMha/openfoundry) — MIT local software/research studio with Ollama routing, SQLite mission memory, a live War Room and bounded self-healing QA. Cloud BYOK routes are optional; a key-free fixture-backed demo runs without an Ollama server. Local-model infrastructure costs are separate from API-token accounting.
```

**PR body**

```markdown
Adds an Ollama-compatible local development application with a visible engineering/verification loop.

The backend accepts OLLAMA_BASE_URL and a configured ollama_chat model, persists mission state locally, and can run with its built frontend in one process or container. Live public-source research may access the network; the included demo uses deterministic research/provider fixtures.

Setup: https://github.com/MrMha/openfoundry#60-second-quickstart-zero-api-key-needed
```

### free-ai-tools

**Suggested category:** open-source developer tools.

**PR title:** Add OpenFoundry — free MIT software with a key-free demo

**Entry**

```markdown
- [OpenFoundry](https://github.com/MrMha/openfoundry) — Free MIT software for a five-department AI software/research studio with live task graphs, approval gates and self-healing QA. Includes a reproducible $0.00 API-token demo. Live cloud providers charge separately; local Ollama requires your own hardware.
```

**PR body**

```markdown
Adds free, self-hosted open-source developer software rather than a claim of unlimited free hosted inference.

The included key-free demo uses fixture provider/research responses and runs real tests, Git commits and repair verification. Live missions support optional paid BYOK providers or local Ollama, with durable cost accounting.

License: MIT. Quickstart and demo: https://github.com/MrMha/openfoundry
```

## Demo recording and responses

For a live walkthrough: start the backend with a built frontend, connect with the operator token if configured, run the no-key demo, review and approve Engineering, switch to **Focus War Room**, inspect the original failure and patch, and finish on verified QA and the launch artifacts. Use [the asset generator](../scripts/generate_demo_assets.py) to capture the four phases automatically; commands are in [CONTRIBUTING.md](../CONTRIBUTING.md#regenerate-the-demo-assets).

If asked whether it works on any codebase: the included demo proves a bounded workflow with controlled provider responses. Live results depend on the model, available source/dependencies and acceptance checks. If asked about isolation: show the measured `/api/plugins` mode and explain guarded execution accurately. If asked about sending chat approvals, opening GitHub PRs or installing marketplace plugins: link [the future roadmap specifications](../ROADMAP_SPECS.md), not a nonexistent CLI command.
