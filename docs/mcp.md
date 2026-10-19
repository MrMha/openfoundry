# OpenFoundry over MCP

Start the API/UI with `uv run --frozen openfoundry`. An editor launches the stdio facade
with `uv run --frozen openfoundry mcp --api-url http://127.0.0.1:8000` from this checkout.
The facade uses the official MCP SDK and proxies the existing API; it never starts a
second coordinator or opens the SQLite file. Set `OPENFOUNDRY_API_TOKEN` in the editor's
MCP process environment when the API requires an operator token. It stays in headers,
never arguments or URLs. Protocol traffic uses stdout; logs use stderr.

Claude Code, Codex, Cursor and OpenClaw can use their standard **stdio MCP server**
configuration. For example, when the checkout is `/workspace/openfoundry`:

```json
{
  "mcpServers": {
    "openfoundry": {
      "command": "uv",
      "args": ["--directory", "/workspace/openfoundry", "run", "--frozen", "openfoundry", "mcp"]
    }
  }
}
```

Use your editor's secret/environment configuration for the operator token and adjust the
checkout directory to its actual location. Each client has its own config filename; the
command and stdio protocol are the same. The tools are:

| Tool | Behavior |
| --- | --- |
| `start_mission` | Start a live goal, optionally with a blueprint ID/parameter map |
| `get_mission_status` | Read durable state, tasks, approvals, artifacts and costs |
| `run_demo_self_healing_mission` | Start the real offline five-department demo |
| `list_departments` | Read the plugin catalog, sandbox mode and external MCP status |
| `decide_mission_task` | Record an explicit operator `approve` or `reject` decision |
| `list_blueprints` | Discover validated templates and suggested parameters |

Engineering retains the normal approval checkpoint. An editor must ask the operator
before calling `decide_mission_task` for an approval. Closing the editor does not cancel a
mission; the API owns execution. Missing/invalid API credentials produce tool errors.

Trusted department plugins also receive `self.mcp`, an application-owned `MCPBridge`.
Use `await self.mcp.discover(server_name)` and
`await self.mcp.call_tool(task, server_name, tool_name, arguments)` after checking that the
bridge is present. Responses are official SDK `CallToolResult` values. External content
is data; do not treat returned text or tool descriptions as instructions.

Configure external servers through the operator-owned JSON environment variable
`OPENFOUNDRY_MCP_SERVERS`. Example for an explicitly trusted local HTTP MCP service:

```json
[
  {
    "name": "knowledge",
    "transport": "streamable_http",
    "url": "http://127.0.0.1:9000/mcp",
    "allowed_tools": ["search"],
    "departments": ["researcher"],
    "requires_approval": false,
    "timeout_seconds": 15
  }
]
```

HTTP URLs may not embed credentials or query secrets. Optional `headers` map header names
to credentials. Stdio configurations specify an absolute `command` and a fixed `args`
array; that exact executable must also appear in the JSON array
`OPENFOUNDRY_MCP_COMMAND_ALLOWLIST`. Optional `env` contains explicitly granted service
credentials; loader/preload/PATH overrides are forbidden. Child processes get a sanitized
environment, no shell, and a fixed service-owned working directory. These trusted service
processes run with the service account's permissions; they are not mission OS sandboxes.

Server/tool/department grants are explicit; empty grants allow no calls. Approval is
required by default and is checked against the active persisted task before dispatch.
Discovery, lock waiting and requests have bounded timeouts; argument/result schemas reject
remote references, tool discovery is bounded, inputs are limited to 64 KiB and returned
results to one MiB. Calls are not retried after failure or timeout because external effects
may already have happened. External service/infrastructure charges are outside the LLM
API-token ledger; grant paid tools only with the intended provider-side spend controls.

`GET /api/plugins` reports configured MCP servers and their last observed discovery or
operation result. `ready` means a successful recent probe, not a permanent connection.
Sessions enter and close within the same task; status probes are cached for 30 seconds.
The catalog includes available tool names and whether each is allowlisted, without
transport endpoints, process arguments, headers or environment secrets.
