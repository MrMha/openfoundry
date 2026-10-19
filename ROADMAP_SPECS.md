# Post-December roadmap: implementation contracts

**Status: design specifications, not shipped APIs.** The current package is `0.1.0`; v1.1, v1.2 and v1.3 below are future release targets in this order, without committed dates. Every path labeled **new** is proposed. Commands/routes not explicitly described as current must not be advertised as available in the present release.

The design extends the existing single-operator, single-coordinator SQLite application. It preserves the current `BaseDepartment.execute_task` signature, server-owned credentials, durable cost reservations, safe workspace helpers, mission execution profiles and replayable events. There is no requirement for Redis, a separate queue service, a second coordinator or a frontend rewrite.

## Existing interfaces all versions must preserve

| Interface | Current source and contract |
| --- | --- |
| Mission creation | `backend/app/api/routes_missions.py`: `POST /api/missions` takes `goal`, optional `title`, `configuration`, `blueprint_id`, `blueprint_parameters`; returns a mission with HTTP 202. `POST /api/missions/demo` runs the key-free fixture-backed workflow. |
| Canonical task decisions | `MissionOrchestrator.decide(mission_id: UUID, task_id: UUID, decision: Literal['approved', 'rejected'], *, reason: str | None = None) -> TaskApproval`. Same-decision retries return the existing decision; conflicting decisions raise `ApprovalConflictError`. Rejection cancels the mission. |
| REST decision paths | `POST /api/missions/{mission_id}/tasks/{task_id}/approve` and `/reject`, optional `{"reason": "..."}`, return `ApprovalView`. `approval_id` identifies a pending approval but is not the REST path's task ID. |
| Approval persistence | `backend/app/models/orchestration.py`: `TaskApproval` is keyed by task ID, has a unique approval UUID, mission ID, requested/approved/rejected state and UTC timestamps. Do not invent a second task-approval state machine. |
| Event log and replay | `MissionEventRecord.sequence` is a global SQLite integer cursor; history is mission-filtered and may have gaps. `ConnectionManager.append(session, event)` persists in the caller's transaction; notify after commit. The browser connects to `/ws/missions/{mission_id}?after=<sequence>`. |
| Authentication | `api/auth.py`: optional loopback-only tokenless access, otherwise bearer operator token; browser WS authentication through `openfoundry`/`bearer.<token>` subprotocols and allowed origins. Do not send tokens as URL parameters. |
| Plugin/catalog | `@register_department`, `load_department_plugins()` and installed `openfoundry.departments` entry points. `GET /api/plugins` returns department metadata, measured sandbox status and MCP discovery status. Plugins are trusted service-process code. |
| Storage and lifecycle | `database.py` creates new tables but does not migrate deployed columns. `main.py` owns lifecycle; `MissionOrchestrator.start()` locks out another coordinator for the same SQLite database. Background workers must share that ownership and close before database disposal. |
| Source execution | `WorkspaceSandbox` exposes contained reads/writes, Git initialization/branches/commits/diffs, and bounded test commands. Existing code does not implement Git fetch/push, shell access or GitHub publishing. |

### Shared persistence and worker rules

Introduce an explicit versioned migration runner before the first deployed schema change. The proposed **new** `backend/app/migrations/__init__.py` and numbered migration modules maintain an `openfoundry_schema_migrations(version, applied_at)` table. Run migrations before orchestrator startup under a database-scoped migration file lock, with transactional SQL and a second version check after lock acquisition. Use `BEGIN IMMEDIATE` for SQLite writers, preserve foreign keys, back up deployed data before upgrade, and fail startup on an unsupported newer schema. Test upgrades from a populated 0.1.0 fixture, repeated application, rollback on failure and concurrent startup. Do not imply that `create_all()` upgrades existing columns.

Outboxes and jobs live in SQLite, with unique idempotency keys, bounded attempts, a lease owner/deadline, UTC timestamps and explicit terminal/uncertain states. Workers acquire one job in a short transaction, perform network I/O outside that transaction, and settle conditionally on lease owner. Claim leases only while the app owns the coordinator lock. Crash recovery retries only operations known to be safe; ambiguous external effects require reconciliation. Cancellation closes clients and persists a useful recoverable state without claiming success.

All new models reject unknown fields, bound strings/lists/payload sizes, use UUIDs/UTC and serialize monetary values as decimals. Operator configuration supplies credential references; public APIs/events never return credentials or raw webhook bodies. Extend `GET /api/plugins` with additive, redacted integration status only. Regenerate frontend contracts with `npm run contracts` for every public schema change and test both projects in Chromium. New event variants must be added explicitly to the backend union and generated validators; never smuggle them into an existing event shape. Maintain schema-version negotiation or a documented client upgrade requirement before exposing an additive discriminator to older strict clients.

Declare directly imported signature/version dependencies (for example `cryptography` and `packaging`) in `pyproject.toml` and regenerate `uv.lock` in the implementing release. Do not rely on incidental MCP/LiteLLM transitive dependencies for provider signature checks or version compatibility.

## v1.1 — Webhook Approval Bridge: Telegram, Discord, Slack

### Operator outcome

A pending Engineering checkpoint produces a private chat notification with mission/title, a bounded specification excerpt or authenticated UI link, and Approve/Reject controls. A configured human can decide from that chat. The War Room receives the same canonical approval/task/mission events as a browser decision. Disconnecting a chat service cannot bypass a checkpoint or stop browser approval.

### Proposed files and responsibilities

| Target | Required implementation |
| --- | --- |
| `backend/app/core/approval_bridge.py` **new** | Typed provider protocol, event-log scanner, transactional delivery outbox, action-token issuance, receipt dispatcher and status aggregation. Accept the existing `Database`, coordinator and manager; do not construct another coordinator. |
| `backend/app/core/approval_providers/base.py` **new** | `ApprovalProvider` protocol: `verify_callback(raw_body: bytes, headers: Mapping[str, str]) -> VerifiedAction | ProviderPing`; `async send_notification(notification: ApprovalNotification) -> DeliveryReceipt`; `async update_notification(receipt: DeliveryReceipt, state: ApprovalState) -> None`. Provider methods have bounded transport deadlines. |
| `backend/app/core/approval_providers/telegram.py` **new** | Bot send/edit/answer-callback adapter, webhook secret verification, allowed chat/user binding and compact opaque callback IDs. |
| `backend/app/core/approval_providers/discord.py` **new** | Ed25519 interaction verification, PING acknowledgement, authorized guild/channel/user matching, deferred interaction response and bot-message updates. |
| `backend/app/core/approval_providers/slack.py` **new** | Raw-body Slack HMAC verification, interactivity parser, allowed team/channel/user matching, bot-message updates; do not follow caller-supplied response URLs. |
| `backend/app/models/approval_bridge.py` **new** | Delivery/outbox, action-token hash, provider receipt, scan cursor and decision-audit rows with indexes and uniqueness constraints described below. |
| `backend/app/api/routes_approval_bridge.py` **new** | Authenticated operator integration status/test controls and separately provider-authenticated webhook routes. Bounded raw request bodies; never place provider callbacks behind the ordinary bearer-token dependency. |
| `backend/app/config.py` | Add validated operator-owned integration configurations and secret references, disabled by default. No secret values in browser configuration or execution profiles. |
| `backend/app/main.py` | Migrate, construct/start bridge after coordinator readiness; stop bridge before coordinator/database teardown. Include routes explicitly before the SPA mount. |
| `backend/app/api/routes_plugins.py` | Add `approval_bridges` status: integration ID, provider, enabled, last successful delivery time, pending/error counts and sanitized error code. |
| `frontend/src/components/ApprovalModal.tsx` | Keep current task decisions; reflect remote approval/rejection without reopening a completed gate. Show remote-channel audit metadata only when the new read contract supplies it. |
| `backend/tests/test_approval_bridge.py`, `backend/tests/test_approval_webhooks.py` **new** | Real SQLite state/races/restart tests and mocked provider HTTP/signature fixtures. |
| `docs/approval-bridge.md` **new** | Provider app setup, callback URLs, operator identity allowlists, rotation, polling/delivery diagnostics and live-service verification checklist. |

### Public and internal contracts

`ApprovalNotification` contains `integration_id`, `approval_id`, `mission_id`, `task_id`, `title`, `description_excerpt`, `review_url`, `approve_action_id`, `reject_action_id` and `expires_at`. Excerpts are opt-in and at most 2,000 characters; default notifications contain no private research/source content. `review_url` comes from an operator-configured public UI origin plus server-generated IDs, not a URL embedded in mission text.

`VerifiedAction` contains provider, integration ID, provider event ID, actor ID, channel ID, action ID and received timestamp. Provider identity is derived from verified callback data and compared to configured stable ID allowlists, never a display name. `ApprovalState` is `requested | approved | rejected | expired | superseded`; the last two are bridge presentation states, not additions to `TaskApproval.decision`.

Proposed routes:

| Route | Authentication and behavior |
| --- | --- |
| `GET /api/approval-bridges` | Operator bearer/loopback rules; redacted status only. |
| `POST /api/approval-bridges/{integration_id}/test` | Operator-authenticated explicit test delivery to a preconfigured destination; HTTP 202 plus a delivery ID. No arbitrary destination or URL in the body. |
| `POST /api/webhooks/approvals/{provider}/{integration_id}` | Provider signature/secret plus actor/destination checks. Limited to 64 KiB. Accept only supported interaction types and configured integration IDs. Provider-appropriate acknowledgement only after the verified receipt is durably stored. |

Action IDs are at least 128 bits of randomness, short enough for provider button limits; store only their hash. Bind each to approval/task/mission, integration, destination, decision, UTC expiry (24-hour default, configurable 1 minute–7 days) and consumed timestamp. Tokens are not operator bearer credentials. A token cannot move to another task, integration or decision. Rotation invalidates undispatched tokens; expiry never auto-approves or cancels a mission.

Persist `approval_bridge_deliveries` with a unique `(integration_id, approval_id)` pair, provider message ID, state (`queued`, `sending`, `sent`, `retryable`, `uncertain`, `failed`, `superseded`), attempts/lease/timestamps and sanitized failure code. `approval_bridge_receipts` has a unique `(integration_id, provider_event_id)` and dispatch state; never mark an action consumed until a durable receipt exists. `approval_bridge_actions` hashes tokens. A decision-audit row records provider event ID, actor/channel IDs, canonical approval ID, requested decision, outcome and timestamps; avoid raw callback payload storage.

### Delivery, callback and race semantics

1. Scan committed `MissionEventRecord` rows in sequence order. In one transaction, insert delivery rows for `approval_request` and advance the bridge cursor. Resume from that cursor after restart. In-memory `ConnectionManager.notify()` may wake the scanner, but is not the delivery queue or source of truth.
2. Recheck the current approval before sending. If already decided, mark delivery superseded. A timeout after a provider may have accepted a message becomes `uncertain`; reconcile by provider message/delivery ID where supported. Do not promise exactly-once chat delivery.
3. Telegram checks `X-Telegram-Bot-Api-Secret-Token` in constant time and validates the configured bot/chat/user. Telegram has no generic callback request HMAC; do not claim otherwise. Slack checks `X-Slack-Request-Timestamp` within 300 seconds and `X-Slack-Signature` using HMAC-SHA256 over `v0:timestamp:raw_body`. Discord checks `X-Signature-Ed25519` and `X-Signature-Timestamp` over raw bytes using the configured application public key and a 300-second replay window; authenticated PING gets the required PONG response.
4. Validate destination, actor allowlist, token hash/binding/expiry and provider-event deduplication. Reject forged signatures (401), unauthorized actors/destinations (403) and oversized bodies (413), without disclosing private mission data. An expired/unknown action receives a provider-appropriate ephemeral unavailable response; no decision is enqueued. A forged body is never acknowledged as accepted.
5. Durably store one verified action receipt and return the provider's required acknowledgement within its response deadline (Discord/Slack: target under 2 seconds). A duplicate provider event receives the same acknowledgement; a worker processes its existing receipt. If receipt persistence fails, return a retryable failure rather than losing the action after acknowledgement.
6. The dispatcher calls **only** `MissionOrchestrator.decide(..., 'approved' | 'rejected', reason=<bounded bridge audit reference>)`. Same decisions are idempotent. A contrary browser/chat decision results in conflict; canonical state wins and the notification is updated. Stale tokens must check the current `approval_id` as well as task ID before dispatch. Never write task/mission statuses directly.
7. Delivery retries: at most 5 attempts; exponential delay 1–60 seconds with jitter; clamp supported `Retry-After` to 60 seconds. Only retry definitive rejection/transient failures known to be safe. A 10-second transport deadline and cancellation apply. Provider outages preserve the normal UI checkpoint. A bridge audit outbox may update messages later; message edit failure does not undo a canonical decision.

All destinations are operator configuration; enforce TLS for remote provider APIs, static approved hosts, redirects disabled and `trust_env=False`. Redact Telegram token-bearing API paths, Slack bot tokens and Discord interaction tokens from logs. No plaintext secrets in SQLite; store references to operator-provided environment/secret-manager bindings. Do not reuse a URL from an incoming payload as a transport destination.

### Definition of done

Tests cover valid/forged/stale/replayed callbacks for all three providers, wrong actor/channel, token expiry/rebinding, HTTP 429, ambiguous timeout, an offline provider, cursor replay, process restart, same/opposite browser races, and rejection cancelling the mission. A callback response under the provider deadline is measured with a slow mocked send operation. A full deterministic mission pauses, is approved through a signed fixture callback, and reaches completion with unchanged accounting and replay events. Live service credentials are optional; document live tests separately from mocked transport checks.

## v1.2 — GitHub draft pull requests from verified self-healed branches

### Operator outcome

After a mission's QA succeeds, the operator can review the exact verified source revision and approve creating a **draft GitHub pull request** in one explicitly configured repository. A mission never pushes, opens a PR or modifies an external repository merely because its tests passed. Publication approval is separate from a task approval and does not rerun the mission.

### Proposed files and responsibilities

| Target | Required implementation |
| --- | --- |
| `backend/app/core/github_publisher.py` **new** | Job workflow for approved immutable snapshots; GitHub client with conditional requests, rate limits, reconciliation and a fixed API origin. |
| `backend/app/core/publication_contracts.py` **new** | `PublicationRequest`, `PublicationSnapshot`, `PublicationView`, digest calculation, change/path/byte bounds and typed state transitions. |
| `backend/app/models/publication.py` **new** | `RepositoryBinding`, `VerificationAttestation`, immutable snapshot records, publication job/approval/audit records. Versioned migration required. |
| `backend/app/core/repository_import.py` **new** | Operator-approved import of a configured repository/base SHA into an empty mission workspace, validating every path/blob and recording its remote base/tree identity. No arbitrary Git URL or shell. |
| `backend/app/core/workspace_sandbox.py` | Add narrow public async read-only `git_revision() -> str` and `export_committed_files(revision: str, paths: Sequence[str]) -> Mapping[str, bytes]` helpers that resolve exact validated commit IDs and contained regular blobs using the existing descriptor and Git safety rules. Never expose generic `_git`, fetch/push or arbitrary `git` args to agents. |
| `backend/app/core/orchestrator.py` | On successful QA, persist a service-owned attestation tying the actual source commit/tree and original acceptance digest to the passing report. Do not trust a model-authored manifest as proof of verification. |
| `backend/app/departments/qa_self_healer.py` | Supply structured verification evidence to the orchestrator; preserve existing output and event contracts. Record skipped/failed checks explicitly; only fully qualifying outcomes authorize publication readiness. |
| `backend/app/api/routes_publications.py` **new** | Operator-only snapshot preview, publication request/review/status endpoints. No provider credentials or automatic approval. |
| `backend/app/models/events.py` | Add an explicit `publication_status_change` variant with publication/mission IDs, state, snapshot digest and optional validated GitHub PR URL. Do not reuse a task approval with a fake task ID. |
| `backend/app/main.py`, `backend/app/api/routes_plugins.py`, `backend/app/config.py` | Lifecycle, configured repo grants and redacted publishing capability/status, disabled by default. |
| `frontend/src/components/PublishPullRequestModal.tsx` **new** | Show repository, base, exact file set/diff, QA revision, draft title/body and digest; separate explicit approve/reject controls. |
| `frontend/src/services/api.ts`, `frontend/src/App.tsx` | Typed publication routes and events; keep ordinary task approvals unchanged. Regenerate all contracts. |
| `backend/tests/test_github_publisher.py`, `backend/tests/test_publication_api.py` **new** | Mock GitHub transport plus real Git, SQLite, verification and approval races. |
| `docs/github-publishing.md` **new** | GitHub App/fine-grained token setup, repository binding/import, permissions, review flow and uncertain-operation recovery. |

### Repository and verification contracts

`RepositoryBinding` is operator-created and pins integration ID, normalized `owner/repo`, allowed base branch, imported base commit SHA/tree SHA, mission ID and allowed path prefixes. Only GitHub-hosted repositories on the configured API origin are in v1.2; no GitHub Enterprise/custom origins until separately implemented. Importing source is a distinct operator action, never inferred from a URL in a prompt. Never overwrite a populated mission without a separate reviewed import mechanism. Archives/blobs reject traversal, symlinks, submodules, devices, credentials and oversized content.

A generated workspace with the current empty Git baseline is **not** assumed to share history with an arbitrary repository. Reject PR creation without a verified `RepositoryBinding`; the UI can explain how to import/bind first. This prevents an entire unrelated repository being replaced by a demo project.

`VerificationAttestation` contains `id`, `mission_id`, `qa_task_id`, committed source SHA/tree SHA, ordered verification commands and results, original acceptance-file SHA-256 digest, report path/hash and completion timestamp. Store it in the service database after successful verification; compare actual Git objects, never only a JSON report's `passed` field. This records observed checks, not a proof against malicious programs running in guarded mode.

`PublicationSnapshot` freezes binding/base SHA, local verified commit SHA, selected paths, blob SHA-256s/sizes, acceptance digest, attestation ID, normalized title/body, unique remote head branch and an overall SHA-256 digest over a canonical JSON manifest. Limit 100 files, 1 MiB per file and 10 MiB total for the first release; text-only regular files. Exclude `.git`, runtime data, research/profile/secret files and credentials. Publish only allowlisted committed paths. Growth artifacts written after QA are excluded unless covered by a separate verified snapshot; never substitute mutable working-tree files for committed source.

### Proposed API and approval state machine

| Route | Contract |
| --- | --- |
| `POST /api/missions/{mission_id}/repository-binding` | Operator-only explicit import/bind request for a preconfigured integration/repository grant and base branch; HTTP 202 import operation. Return no access credentials. |
| `POST /api/missions/{mission_id}/pull-requests/preview` | Body: binding ID, verified commit SHA, attestation ID, selected paths, title and Markdown body. Return immutable snapshot ID/digest, diff and validation results; no network write. |
| `POST /api/missions/{mission_id}/pull-requests` | Body: snapshot ID/digest and `idempotency_key` (8–128 URL-safe characters). HTTP 202 creates/reuses a publication in `awaiting_approval`; still no external write. |
| `GET /api/publications/{publication_id}` | Redacted `PublicationView` with state, pinned manifest, timestamps, error code and optional PR number/URL. |
| `POST /api/publications/{publication_id}/approve` | Body: expected snapshot digest and optional bounded reason. Same digest/decision retry is idempotent; stale digest or opposite decision returns 409. Queue only the approved immutable snapshot. |
| `POST /api/publications/{publication_id}/reject` | Reject this publication only. It never cancels an already completed mission or calls task rejection. |

Require the existing operator authentication and allowed-origin checks for every route. A mission must be completed and the selected attestation must satisfy policy; otherwise 409. Unsafe paths/malformed bodies return 422, missing grants 403, missing objects 404. One `(mission_id, idempotency_key)` maps to one snapshot; reuse with a different digest returns 409.

States: `awaiting_approval → queued → creating_branch → creating_pr → created`; rejection leads to `rejected`. Definitive failure is `failed`; expired lease after a potentially accepted remote write leads to `reconciling`, then `created`, `retryable` or `uncertain`. New source, changed base, changed title/body or changed verification creates a new snapshot and requires fresh approval. Record who approved under the current single-operator identity model, digest and timestamp; do not invent multiuser ownership guarantees.

### GitHub transport and reconciliation

Use a GitHub App installation token or fine-grained operator token scoped only to granted repositories with Contents read/write and Pull requests read/write; no admin, workflow or organization-wide permission. Resolve short-lived credentials at dispatch time, never persist them in job payloads or source/Git config. Use a fixed HTTPS `api.github.com` origin, `X-GitHub-Api-Version: 2022-11-28`, redirects disabled and bounded request/overall job deadlines. Validate response URLs as the granted repository's `github.com` PR paths.

Use the Git Data REST API to create blobs/tree/commit based on the pinned imported base tree and then an owned `openfoundry/<mission-id>/<publication-id>` branch. No force push; refuse unrelated existing branches. Exclude `.github/workflows/` from first-release publication grants. Set `draft: true` on PR creation. The body includes a nonsecret stable publication marker and verification/diff summary for reconciliation, without dumping private research or profiles. Account for GitHub blob SHA-1 versus local integrity SHA-256 explicitly.

Check the base ref before branch creation. If it moved from the approved base SHA, stop with `base_changed`; do not silently rebase or retest under prior approval. A proposed base refresh requires a new import/snapshot/verification/approval cycle. Existing remote branches/PRs belonging to this job can be reused after comparing pinned content; never modify another actor's branch.

Creation retries must reconcile by the exact owned head branch and stable publication marker. A timeout after `POST /pulls` may have created a PR: query for that exact head/base first. If outcomes remain ambiguous, mark uncertain and require operator reconciliation, rather than issuing another blind create. Rate limits use bounded backoff; auth/permission errors are terminal. Reject response-controlled redirects or arbitrary clone/upload URLs. Record created PR URL/number and pin it in the publication row before emitting an event.

### Definition of done

A fixture-backed Engineer → failing QA → repair → passing QA mission creates a service-owned verification attestation. A mocked GitHub service receives **no write** before a separate publication approval, then exactly the approved path set and a draft PR request. Tests cover absent binding, stale/forged attestations, changed working tree, growth files excluded, secret paths, base movement, idempotency conflicts, foreign branch collisions, timeout-after-create, retry/restart, credential redaction and operator rejection. Original mission approvals and accounting tests stay unchanged. A Chromium test reviews the diff and approves publication through the new modal; no live repository is modified by CI.

## v1.3 — Community blueprint and department registry

### Operator outcome

Browse a curated remote catalog, inspect publisher/version/license/capabilities, and opt into a pinned blueprint or trusted department package. Catalog browsing never imports Python, executes an installation hook or grants tools. Bundled/offline behavior remains available during registry outages.

### Proposed files and responsibilities

| Target | Required implementation |
| --- | --- |
| `backend/app/core/registry_contracts.py` **new** | Versioned signed catalog/item models, compatibility checks, canonical manifest digest, namespace/version validation, artifact allowlists and bounded metadata. |
| `backend/app/core/registry_client.py` **new** | Fixed-origin HTTPS fetch with ETag/cache, signature/digest verification, bounded downloads and last-known-good catalog. No plugin imports. |
| `backend/app/core/registry_installer.py` **new** | Stage, validate and atomically activate blueprint JSON; generate a department installation plan for manual out-of-process review. No pip/npm execution inside API requests. |
| `backend/app/models/registry.py` **new** | Catalog cache metadata, approved install intents, pinned installed-item records and audit log, with a versioned migration. Store payloads outside generated workspaces. |
| `backend/app/api/routes_registry.py` **new** | Authenticated catalog search/detail, install preview/approval, installed-item and rollback/status routes with pagination. |
| `backend/app/core/blueprints.py` | Merge validated bundled and operator-installed blueprints without shadowing builtins. Keep schema 1; introduce explicit schema 2 for namespaced plugin requirements and collision-free IDs. |
| `backend/app/departments/base_department.py` | Retain decorator/entry-point contract. Validate installed package name/version against the approved manifest at startup; no network discovery on import. |
| `backend/app/api/routes_plugins.py`, `backend/app/api/routes_blueprints.py` | Add source/publisher/version/digest/compatibility information without leaking install paths or secrets. Keep existing field meanings; explicitly version incompatible changes. |
| `backend/app/config.py`, `backend/app/main.py` | Disabled-by-default catalog origin, pinned publisher keys, cache location under application data, install policy and lifecycle. |
| `frontend/src/components/RegistryBrowser.tsx`, `frontend/src/components/RegistryInstallModal.tsx` **new** | Searchable catalog, manifest diff, capability/permission view, explicit opt-in and restart/install-plan status. |
| `backend/tests/test_registry_client.py`, `backend/tests/test_registry_installer.py` **new** | Signed fixture catalogs, offline/rollback/tamper/compatibility cases and no-code-execution assertions. |
| `docs/registry-publishing.md` **new** | Manifest schema, publisher key registration/rotation, review process, package reproducibility and revocation handling. |

### Manifest and identity contracts

Catalog envelope: `schema_version: 1`, registry ID, generated/expiry UTC timestamps, monotonic catalog revision, bounded `items` and signature metadata (`key_id`, Ed25519 signature over canonical bytes). Pin accepted keys in operator configuration; never accept a new trust key merely because the downloaded catalog includes it. Document canonical JSON serialization (UTF-8, sorted keys, compact separators, no floats/non-finite values) and signature test vectors.

An item requires:

| Field | Contract |
| --- | --- |
| `id`, `kind`, `version` | Namespaced `publisher/slug`, kind `blueprint | department`, immutable semver. Namespace/publisher identity is bound to a pinned signing key. |
| `title`, `description`, `license`, `publisher` | Bounded plain text, SPDX license identifier and display metadata; no HTML execution or secret-bearing URLs. |
| `compatibility` | OpenFoundry API/blueprint schema ranges and Python range, parsed with a documented packaging-version implementation. Unsupported versions are visible but not installable. |
| `artifact` | One HTTPS URL on an operator-allowlisted distribution origin, SHA-256, exact byte length and content type. Blueprint: JSON only. Department: pinned package/wheel descriptor, not executable installer instructions. |
| `capabilities`, `permissions`, `requirements` | Explicit declared tool/server grants, network/service requirements and dependencies for operator review; declarations grant nothing automatically. |
| `source_url`, `documentation_url` | HTTPS project/documentation links, displayed as untrusted metadata. No userinfo, query credentials or custom schemes. |
| `signature` | Publisher key ID/signature over the item's canonical manifest including digest and version. A signed item is attributable, not automatically safe. |

First-release bounds: catalog 2 MiB/500 items, item metadata 32 KiB, blueprint artifact 128 KiB, department artifact metadata 64 KiB (actual package installation remains out of process), 15-second fetch deadline, 3 allowed redirects maximum only if each hop is HTTPS and explicitly allowlisted. Pin validated public DNS endpoints using the existing research fetch boundary or an equivalent reviewed transport; reject loopback, private/metadata addresses, credential URLs, downgrade redirects and remote JSON schema references. Cache accepted catalog bodies by digest under `OPENFOUNDRY_HOME/registry/`; do not resolve cache paths from publisher-controlled strings.

### Proposed API, install and rollback contracts

| Route | Behavior |
| --- | --- |
| `GET /api/registry/items?kind=blueprint&query=...&cursor=...&limit=20` | Authenticated, bounded catalog read with source freshness and signature verification status; limits 1–100. Opaque cursors pin the catalog revision. |
| `GET /api/registry/items/{publisher}/{slug}/versions/{version}` | Return manifest and compatibility/capability review, never code execution. |
| `POST /api/registry/installations/preview` | Body: exact item ID, version and digest. Return a stable preview ID/digest, namespace conflicts, dependencies, permissions, changes and required restart/manual actions. |
| `POST /api/registry/installations` | Body: preview ID, expected digest and idempotency key. Explicit operator approval of that reviewed plan; HTTP 202. No automatic transitive dependency install. |
| `GET /api/registry/installations/{installation_id}` | State, pinned source/version/digest, audit timestamps and sanitized error code. |
| `POST /api/registry/installations/{installation_id}/rollback` | Explicit operator approval restores the preceding validated blueprint version or generates a manual department rollback plan. Never silently rewrite running plugin code. |

Installation states: `awaiting_approval` at preview persistence, then `queued → validating → installed` for JSON blueprints; department items become `manual_install_required → restart_required → active` only after a trusted out-of-process install and startup verification. Errors are `incompatible`, `rejected`, `revoked`, `failed` or `rollback_required`. A browser cannot run a returned command automatically. Authentication, digest mismatch/409, missing grants/403, unknown item/404 and invalid schema/422 behavior is consistent with prior versions.

For blueprints, download to service-owned staging, check size/hash/signature/schema/default resolution, atomically replace only the approved namespaced destination and persist installed metadata. Do not overwrite `blueprints/*.json` in the source checkout. Keep the last working installed version and activate only after the file and metadata are durable; reconcile an interrupted activation at startup. Schema 1 IDs remain unchanged. Schema 2 adds an immutable namespaced identity and declared department package/name/version requirements; a resolver maps them to available registered department names, rejects builtin shadowing and unavailable capabilities, then uses the existing DAG validator. Required Engineering gates remain enforced in both schemas.

For departments, the API produces an exact package/version/hash installation plan (wheel only; source distributions and arbitrary build hooks excluded initially). The operator installs it outside the running API into a dedicated managed environment with dependency changes reviewed and hashes enforced, then restarts. Startup verifies distribution metadata and entry points against the approved record before import; a mismatch fails activation rather than falling back to another package version. A registry item must not enable arbitrary `OPENFOUNDRY_DEPARTMENT_PLUGINS` imports, subprocesses or MCP grants. Dependency conflicts with the backend lockfile require a maintained compatible lock/install plan, not live `pip install`.

Remote outages retain a verified, unexpired last-known-good catalog for reads. Expired catalogs may be shown with a stale label but cannot authorize new installs. Downgrade revisions, changed content for the same publisher/version, revoked signatures, unknown keys, hash mismatches and clock-expiry failures stop installation. Revocation disables new installs and flags an already installed item for operator review; it does not abruptly mutate a running mission. Rollback/restart must preserve mission state and accounting.

### Definition of done

Fixture tests prove catalog pagination, signature vectors, unknown/rotated/revoked keys, stale/offline cache, hash/size mismatch, malicious URLs, namespace collisions, version compatibility, path traversal, partial activation recovery and repeated idempotent install/rollback. Assert no Python import or package-manager subprocess occurs during browse/preview/blueprint install. A schema-2 blueprint resolves only when its approved plugin requirements are installed; builtin names cannot be shadowed. Chromium tests browse, inspect permissions, approve a JSON blueprint and see it in CommandBar after catalog refresh. Current three bundled templates, offline demo and all MCP/API tests continue to pass without network access to a registry.

## Release gates

Each version ships migrations, typed models, API/WS contract regeneration, focused unit tests, a real SQLite integration path, frontend tests and Chromium scenarios. Keep full backend/frontend static checks green and retain reproducible demo assets. Document mocked versus live provider/service checks. Never use a live Slack/Telegram/Discord channel, GitHub repository or package registry as an unapproved CI side effect. Contributors should implement the smallest versioned slice that satisfies its contracts and obtain design review for a public contract change before coding a competing state machine.
