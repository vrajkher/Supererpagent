# RAJ Finance Agent — Production Product Plan

Version: 1.0 | Planning baseline: 3 November 2026, India

Owner: Vanraj Kher / proposed CA-CMA practice.

Status: proposed specification, not an implementation or a deployment certification. Repository source was inspected; no live Tally, SSH deployment, load test or repository test suite was run for this plan.

## 1. Product outcome and boundaries

Build a Windows companion for each client's Tally PC and a firm portal accessible from a desktop or phone browser. Staff can ask questions about books, reconcile statements, prepare vouchers, approve exact changes and verify the resulting Tally records. Later releases add source-backed costing and management scenarios.

Use DeepSeek Harness for finance agent execution, the DeepSeek model for interpretation, RAJTALLYFINLMCP for Tally integration, and OpenSSH reverse forwarding for connectivity. DeepSeek Super-agent develops the software in a separate development environment. It is not a ready-made accounting agent.

There is no separate Finance Workflow service. Business instructions live in Harness skills; durable execution, scheduling, approval validation and recovery are implemented as a RAJ Harness plugin plus local MCP enforcement. The portal still needs ordinary identity, storage and administration APIs.

Initial supported scope: one validated Windows/TallyPrime release family, accounting vouchers, bank CSV/XLSX, trial balance, ledger and outstanding reports. Tally.ERP 9, inventory invoices, scanned documents and advanced costing need explicit compatibility gates. Do not advertise every existing MCP tool as production supported on day one.

Not included in v1: automatic payments, statutory portal filing, autonomous deletion/cancellation, remote desktop, unattended upgrades of Tally, or a guarantee of zero errors. E1pi is a feature reference, not an assertion that its internal implementation is SSH.

## 2. Verified starting point

- DeepSeek Harness describes itself as a plugin-based agent harness in developer preview, with breaking changes possible. Pin a tested commit/release and package lock, rather than installing latest at customer startup.
- Its MCP subsystem documents stdio and Streamable HTTP integration. Confirm exact configuration against the pinned release before implementation.
- DeepSeek Super-agent is a research prototype for one GitHub repository. Use its compatible Harness version in a development-only deployment; it need not be the same version as production.
- RAJTALLYFINLMCP README documents reads, reports, writes, dry runs and a demo server. It explicitly states live Tally testing has not yet been done.
- The inspected config.py defaults to read_write and uses mutable process-global company/settings. server.py exposes a settings update tool that can call config.update. These are product-hardening items, not demonstrated exploits.
- OpenSSH provides encrypted remote forwarding and controls such as AllowTcpForwarding, PermitListen, GatewayPorts and MaxSessions. Validate exact behavior on the selected server version.

Inspected repository baseline: RAJTALLYFINLMCP tree 54644ebed174efb0e1a056ded86073b9b6cc3c97; server.py blob 93393a28c4a3f1699298ce16bd88d298c8dbaf90; config.py blob 811f3b9ba5ea87269b56a61432f80c4c67813e79. These identify observations, not a recommended release pin.

## 3. Deployment architecture

Client Windows PC:

1. RAJ signed installer and tray/status application.
2. Dedicated background service supervising the local MCP gateway and outbound OpenSSH client.
3. RAJ MCP integration running with a fixed permitted Tally endpoint and company policy.
4. Tally remains the accounting system of record. Tally must be running with the permitted company open.
5. Local execution journal, protected credentials and source staging area.

Central Linux deployment:

1. HTTPS firm portal with authentication and approval screens.
2. API for users, client bindings, jobs, approvals, artifacts and device administration.
3. PostgreSQL for durable product state and an encrypted object store for authorized artifacts.
4. Isolated Harness worker processes/containers created per client job or reserved client queue.
5. SSH ingress and a connector registry routing authorized workers to the correct tunnel.
6. A tightly scoped MCP bridge/gateway for application authentication over the tunnel.
7. Monitoring, backup and signed release distribution.

The finance worker receives no SSH administration key, unrelated client credentials or unrestricted shell/network access. Readable bank narrations and uploaded documents are data, never executable instructions.

## 4. OpenSSH connection design

Each client PC initiates an outbound SSH connection to a reachable central endpoint. Client routers need no incoming forwarding for this design. The central server does require reachable ingress. A network that blocks the chosen outbound SSH transport must be detected during onboarding; do not silently bypass its policy.

Reverse-forward only the local RAJ gateway, not Tally port 9000, RDP, a general shell or the whole client network. Bind the reverse listener to loopback on the central host. Configure server-side GatewayPorts=no and a unique allowed remote listener per connector. A TCP port number is a routing label, not proof of client identity.

Each device has a unique key, with approved host-key verification. A restricted tunnel account permits only required reverse forwarding, no command sessions, PTY, agent forwarding, X11 or SFTP. Use configuration appropriate to the tested OpenSSH version, such as AllowTcpForwarding=remote, PermitListen and MaxSessions=0. Test restrictions rather than relying on a nologin shell alone. Server-side listen restrictions do not prove the destination is the genuine MCP gateway.

For production, authenticate the MCP channel in addition to SSH. A proposed implementation is a per-device TLS gateway with client and server certificate validation, reached through the SSH tunnel. The local gateway fronts the MCP process, preferably using local stdio. A small scoped stdio-to-remote-MCP bridge can handle mutual TLS if the pinned Harness client cannot configure it directly. This bridge is new development, not a claim about existing Harness features.

Certificates and signed job authorizations bind organization, client, device and permitted company. The gateway validates a short-lived server-signed execution envelope including job ID, operation, draft hash, expiry and nonce. Do not put model-generated client IDs in charge of authorization.

Loopback alone does not isolate processes on the same Linux host. Use application authentication plus worker network isolation and a trusted connector broker. Workers cannot directly scan all loopback tunnels. A forged or incorrectly assigned endpoint must fail certificate/identity verification before tools are usable.

Reconnect uses keepalives and exponential backoff with jitter. Treat tunnel connected, MCP healthy, Tally running and company open as four separate statuses. After reconnect, re-authenticate, verify company identity and reconcile unfinished writes before accepting new ones. Do not equate an SSH heartbeat with Tally readiness.

## 5. Installation, pairing and removal

Client experience target: Download -> Install -> Sign in/pair -> Select company -> Run checks -> Connected.

1. An authorized firm admin creates a client and an expiring, single-use enrollment code. Store its hash, not its plaintext.
2. The installer verifies publisher signature, supported OS and required dependencies. Bundle validated runtime versions; no pip install from the moving main branch on customer machines.
3. The app generates the device private key locally, protects it using Windows facilities and restrictive file permissions, then redeems enrollment over authenticated HTTPS.
4. A local authorized person confirms the device/client and the allowed company. A dedicated SSH identity and gateway identity are registered. No reusable firm-wide enrollment secret.
5. Test outbound connectivity, host identity, tunnel, gateway authentication, MCP handshake, Tally health and company identity. The portal displays actionable errors.
6. Run a read-only trial balance and ledger verification. Write capability stays disabled until test-company validation and explicit administrator enablement.
7. Local Pause stops accepting new work; distinguish pausing future writes from interrupting a write already in flight. Show recent activity and pending approvals.
8. Offboarding revokes device credentials, closes live tunnels, denies further jobs, invalidates pending approvals and applies the agreed retention/export policy. Deleting authorized_keys entries alone does not close an already established SSH session.

Automatic connector startup does not imply automatic Tally/company startup. Tally and Windows interactive session behavior must be validated in the compatibility matrix.

## 6. Portal and roles

Screens: Clients; Ask the Books; Upload Statement/Bill; Reconciliation; Voucher Preview; Approvals; Jobs/Exceptions; Reports/Management; Device Health; Settings.

Every work screen prominently shows client, company and financial period. Provide English first with Gujarati/Hindi explanations where useful. The phone experience is a responsive portal, not a native app promise.

Roles:

| Role | Access |
|---|---|
| Firm administrator | Enroll devices, assign staff, manage policy; cannot rewrite audit history |
| Preparer | Read assigned books and create drafts |
| Reviewer | Inspect evidence and approve exact drafts within policy |
| Client viewer | View permitted reports and own-client jobs |
| Local client administrator | Confirm company connection, pause and revoke device |
| Support | Health metadata by default; temporary explicit access for data diagnostics |

Maker/checker separation is configurable; do not require two people for every pilot, but enforce it where the firm's policy requires it. High-value thresholds and approval expiry are configured, not invented defaults.

## 7. Durable execution inside Harness

Implement a RAJ Harness plugin for durable job state and checkpoints. A conversational session alone is not the execution ledger.

Job lifecycle: RECEIVED -> VALIDATED -> WAITING_FOR_DEVICE / READING -> DRAFTED -> WAITING_FOR_APPROVAL -> POSTING -> VERIFYING -> COMPLETED. Other explicit states: REJECTED, CANCELLED_BEFORE_POSTING, PARTIALLY_COMPLETED, RESULT_UNKNOWN and FAILED.

Persist each transition before external effects where appropriate. Record who initiated the job, authorization context, company identity, source hashes, pinned software/skill/model versions, tool inputs/results, draft and approval versions, row outcomes and final read-back evidence. Record concise decisions and observable actions, not hidden model reasoning.

Queue offline jobs with an expiry and a visible waiting state. An old approval does not authorize a changed ledger mapping, changed company or materially changed source data after a long offline period. Revalidate before executing.

Cancellation stops unsent work. It does not assert that an in-flight Tally request was undone. Bulk import is a set of per-row outcomes, not an assumed atomic transaction.

## 8. MCP product hardening

Required before any production writes:

- Default read_only. Administrator-controlled immutable permission ceiling, not modifiable by model tools or saved settings. Separate preferences from authorization.
- Bind permitted Tally host/port, client, company and period outside prompt text. Do not allow settings changes to redirect tools to arbitrary hosts.
- Replace process-global company switching with per-request validated context or one isolated instance per bound company. Recheck company identity immediately before each mutation.
- Restrict tools by capability. Read jobs cannot discover/use write, delete, raw XML, arbitrary TDL/evaluate or unrestricted SQL tools.
- Enforce policies on resources as well as tools. All access paths must use the same company boundary.
- Existing direct write tools must be internal or require valid execution authorization. Exposing a safe posting tool while leaving the original write path open is insufficient.
- Use Decimal or integer minor units and explicit rounding rules for monetary calculations. LLM output never supplies authoritative totals without deterministic validation.
- Validate ledger existence, voucher balance, dates, units and supported schema. Large reads must be bounded and support paging/incremental extraction.
- Preserve Gujarati/Hindi Unicode and source identifiers.
- Place limits on XML payloads, parsing, file sizes, execution time and output size. Treat spreadsheet formulas, macros and document instructions as untrusted input.
- Separate data reads from permitted side effects. Disable delete/cancel by default in v1.

## 9. Approval, posting and uncertainty

Draft -> validate -> show exact preview -> authorize -> post -> read back.

Approval is a server-signed authorization for an exact canonical payload hash, client, company, period and allowed operations, with reviewer identity, expiry and single-use identifier. The local gateway checks it. A model cannot approve by printing 'approved' or passing confirm=true.

Use a local durable outbox. Persist job/row identity and intended content before sending. Serialize writes per connector initially; validate whether safe per-company concurrency is possible later. Capture Tally-returned identifiers and read back accounting fields and totals.

Tally may accept a request before the network response is lost. Therefore exactly-once execution cannot be assumed. Do not blindly retry writes. Use a stable source/job reference stored through a validated supported field if possible, together with Tally IDs and before/after evidence. If no reliable correlation can be established, mark RESULT_UNKNOWN and require reconciliation before further affected writes. A local idempotency key alone cannot guarantee remote exactly-once behavior.

Do not promise one-click undo for every operation. Before-post cancellation is straightforward. After posting, prepare an approved correction/reversal where valid, or follow the agreed Tally correction procedure. Do not automatically restore the entire company backup, which could overwrite unrelated work.

## 10. Bank reconciliation and document processing

CSV/XLSX release first:

1. Preserve the source file and content hash according to retention policy.
2. Detect columns and require confirmation for uncertain debit/credit/date interpretations.
3. Validate row totals and opening/closing balances where provided. A mismatch blocks automatic preparation.
4. Extract Tally entries with period, bank ledger and company fixed.
5. Deterministic matching first: references, amount, date windows and one-to-many rules. Never match solely because amounts coincide.
6. Assign matched, unmatched, duplicate-source or ambiguous status to every source row. AI proposes categorization for unresolved narrations, with evidence.
7. Preview accounting treatment and mapping. Transfers, fees, tax components and reversals have explicit handling.
8. Approve/post only selected valid rows; persist per-row outcome and reconcile again.

Release gate: all source rows accounted for; unexplained differences visible; no ambiguous match silently accepted.

PDF/OCR release later: page/row evidence, confidence and amount/date cross-checks; uncertain fields need review. Benchmark using representative anonymized Indian bank and invoice layouts. Do not claim any-bank support based on a few samples.

## 11. Reports and CMA decision support

v1: verified trial balance, ledger statements, receivables/payables ageing, bank reconciliation and monthly working note. Distinguish Tally-native outputs from derived working papers and include extraction time, company, period and tie-out status.

v1.1: operating cash forecast, budget-versus-actual, product/customer contribution, break-even and pricing scenarios. Inputs beyond Tally may include budget, cost classification, BOM, labour hours, capacity and allocation rules. Missing data stays missing; label scenarios and assumptions.

Use deterministic calculation tools with approved definitions. Show source, assumptions, sensitivity, exclusions and reviewer sign-off. Distinguish contribution margin from allocated full-cost profit. Produce recommendations for professional judgment rather than automatic business commitments.

If tax/compliance checks are added, maintain dated rule definitions, authoritative references, effective dates, tests and professional review. GSTIN format/checksum validation is not proof of portal registration status. Do not call a working paper a filed return.

## 12. Data model and storage

Minimum entities: organizations, clients, company_bindings, devices, users, role_assignments, jobs, job_events, sources, drafts, draft_rows, approvals, execution_attempts, tally_record_links, artifacts, credential_metadata and releases.

Every business object carries organization/client/company scope. Enforce authorization on the server; database row policies provide additional protection. Client-scoped storage keys and access checks cover files, retrieval indexes, logs, exports and caches. Cache keys include permissions, company, period and data revision; never reuse another client's prompt context.

Central PostgreSQL stores product state, not a replacement general ledger. Connector uses a small durable local journal for disconnected/recovery state. Selective local snapshots/read caches must carry freshness markers and invalidate on relevant changes. Export monthly evidence packs in CSV/XLSX/PDF plus machine-readable job metadata as needed.

## 13. Privacy and model execution

Baseline is a central firm-managed deployment. Accounting data returned from client tools and selected documents will reach that server. Necessary context also reaches the selected model provider when a cloud API is used. Do not market this as 'nothing leaves the client PC'.

Document precisely what is sent, mask unnecessary fields, restrict retention and isolate client retrieval. Provide configurable source retention and export/deletion policies. Do not assume a provider's zero-retention terms without verification.

Future private mode may run Harness/model on premises, but requires separate hardware, quality and support validation. The selected DeepSeek model and API rates are procurement decisions, not fixed by this plan.

## 14. Reliability and operational behavior

| Failure | Required behavior |
|---|---|
| PC offline / Tally closed | Queue bounded work; show precise status; do not fabricate reports |
| SSH disconnect before write | Reconnect, authenticate and revalidate context |
| Disconnect after write sent | RESULT_UNKNOWN until Tally reconciliation confirms outcome |
| Company switched manually | Block mutation and ask for correct binding |
| Partial batch | Show row results; resume only confirmed unsent rows |
| Model unavailable | Preserve job/checkpoint; no blind financial retry |
| Permission revoked | Deny new calls and close affected sessions; reconcile in-flight work |
| Update fails | Restore previous application version; preserve journal/state |
| Central database fails | Fail closed for posting until durable authorization/journaling is available |

Use per-client queues, limits, backpressure and capped model/tool budgets. The number of registered devices is separate from the number of concurrent active jobs. Start with read concurrency limits and one writer per connector; tune only after measurement.

## 15. Observability, backup and support

Track device last-seen, tunnel/gateway/MCP/Tally health separately; job duration, queue depth, read-back mismatches, unknown write results, reconnect counts, update failures and model cost per completed job. Tag with opaque identifiers, not sensitive bank details.

Every support incident has a job/correlation ID. Diagnostic exports redact source content and credentials by default. Support access is explicit, time-bounded and audited. Do not store passwords, private keys or full bank statements in ordinary logs.

Proposed service objectives for validation: portal monthly availability 99.5% initially, excluding explicitly agreed maintenance; central job-state recovery-point target <=15 minutes and recovery-time target <=4 hours. These are design targets, not promises or measured results. Local pre-write journal is synchronous; demonstrate replay/reconciliation after a crash.

Back up PostgreSQL and artifacts using encrypted off-host backups, tested restores and agreed retention. Tally backup uses supported customer procedures and a separate schedule. Neither SSH nor the current MCP automatically backs up Tally. Restore drills must include pending approvals, revoked devices and uncertain writes.

## 16. Updates and software supply chain

Pin compatible Harness/MCP/SDK/model configurations. Build signed Windows packages and signed manifests. Verify signatures and hashes locally before upgrade. Restrict write operations during upgrade; preserve local journal and migration checkpoints. Use staged rollouts: test lab -> internal -> small opted-in client cohort -> general availability.

Include application rollback and database migration compatibility. A rollback must not discard executed job identities or re-enable revoked credentials. Generate dependency inventory/SBOM, license notices and release notes. Do not auto-update from arbitrary GitHub branch HEAD.

## 17. Super-agent development deployment

Use a separate repository workspace and bot identity with least necessary GitHub rights. Synthetic/anonymized fixtures only by default. No production SSH keys, client documents or live Tally write access.

Authorized issue -> branch/worktree -> implementation -> unit/integration tests -> PR -> human review -> signed build -> staged release.

Super-agent can propose skills and rule changes, but they pass the same versioned review process as code. No autonomous production merges, self-modification of running finance workers or widening of accounting permissions.

## 18. Implementation organization

Keep RAJTALLYFINLMCP as the independently testable Tally adapter. Use a new RAJ product repository if desired, referencing a pinned adapter version rather than copying uncontrolled source.

Suggested logical directories: apps/portal; apps/windows-connector; packages/harness-raj; packages/mcp-bridge; packages/contracts; packages/calculations; infra/ssh; infra/deploy; tests/fixtures; tests/e2e; docs/runbooks.

These are proposed directories, not files already created. Reuse Harness capabilities after auditing them; add only the missing product contracts and durable state behavior.

## 19. Phased delivery with exit gates

Planning estimate for a small experienced team with live Tally access and prompt decisions: 16–24 weeks to a narrowly supported commercial v1.1; 250-office rollout follows measured validation. One developer working part-time will take longer. AI coding assistance is not a substitute for Windows/Tally testing and review.

| Phase | Indicative window | Deliverable | Exit gate |
|---|---|---|---|
| Discovery/compatibility | Weeks 1–2 | Supported matrix, threat model, source/license audit, fixed scope | Live Tally reads verified and unresolved blockers logged |
| Secure read-only alpha | Weeks 3–5 | Pairing, reverse SSH, MCP authentication, Harness reports | Cross-client denials and native report tie-outs pass |
| Draft/reconciliation | Weeks 6–8 | CSV/XLSX import, matching, source evidence, approval UI | Every source row classified and totals checked |
| Controlled writes | Weeks 9–11 | Signed approval, local journal, posting/read-back/recovery | Failure injection and duplicate-prevention checks pass |
| Product beta | Weeks 12–15 | Installer, updates, diagnostics, backups, 3–5-client pilot | Two complete monthly cycles; dates may extend this window |
| Commercial v1 | Weeks 16–18 | Support playbook, validated 10–25-client cohort | Restore/security/support drills and release checklist pass |
| Decision support + scale | Weeks 19–24+ | Selected CMA models, measured 50/100/250-device rollout | Each cohort meets isolation/reliability/cost criteria |

Calendar gates supersede week labels: two real monthly cycles can extend the schedule even if implementation is finished. Do not enroll all 250 offices before these gates pass.

## 20. Validation and launch gates

Automated checks: deterministic calculations, company/access boundaries, signature/hash/expiry validation, malformed inputs, duplicate source detection, per-row partial failures and schema migrations.

Integration checks: actual MCP protocol over the full bridge/tunnel path, supported Windows releases, real Tally test companies, financial period boundaries, Gujarati names, multiple open companies and manual Tally changes.

Failure injection: kill SSH, connector, worker or database before/during/after posting; lose the Tally response; reboot Windows; expire approval; revoke key/certificate; rotate server host key; fill disk; restore backups; upgrade then roll back.

Release acceptance:

- No successful unauthorized cross-client access in automated and independent review tests.
- All golden test-company reports tie out to native Tally outputs with documented rounding.
- No unexplained duplicate in the failure-injection suite; ambiguous outcomes block retry.
- Every accepted write has source, exact approval, execution record and read-back or explicit unresolved state.
- Installer, uninstall, revoke, reconnect, restore and rollback demonstrated on supported systems.
- Load test simulates the target registered-device count plus measured active-job concurrency; central sizing is based on results, not device count alone.
- Two monthly pilot cycles accepted by the designated finance reviewer.

## 21. Team, cost and commercial model

Roles: technical lead/backend developer; Windows/Tally integration developer; portal developer; QA/operations engineer; finance domain reviewer/product owner. Roles may be combined, but independent review of financial writes and security remains necessary.

OpenSSH, Harness and the selected open-source components remove some software subscription fees; they do not make the entire service free. Budget for server compute, database, storage, backup, bandwidth, model API/local model hardware, signing, monitoring, support and professional validation. Confirm licences for exact shipped versions and dependencies.

Monthly cost model: fixed infrastructure + model usage + storage/bandwidth + support labour + maintenance reserve. Per-client cost = shared costs / paying clients + that client's usage/support allocation. Model usage = input/output tokens at the actual selected provider rates, plus OCR/other services if used. Track cost per completed task and per reviewed monthly account, not just token price.

Given a proposed fee of INR 1,000/client/month, 250 paying clients imply INR 250,000 monthly gross revenue before taxes, labour and operating costs. This is arithmetic, not a profitability forecast. Validate real support time, workload, collection and scope before committing to that price. Offer explicit volume limits or fair-use definitions based on measured service economics.

## 22. Decisions and inputs needed before implementation

Defaults proposed: Windows-first connector; Linux central deployment; one firm initially with multiple isolated clients; cloud DeepSeek model initially with disclosed data flow; read-only first; explicit approval for writes; English portal plus Gujarati explanations; CSV/XLSX before scanned PDFs; OpenSSH remains the initial transport.

Implementation requires: accessible development repository, representative Windows/Tally test PC, installed Tally version, authorized test company, anonymized statement and voucher samples, company/ledger verification owner, server/domain and chosen model credentials, retention policy and approval roles. Credential values must be supplied through proper secret management, never pasted into specifications or GitHub issues.

## 23. First ten engineering tasks

1. Pin and inventory repository/runtime versions; establish the supported Tally test fixture.
2. Run existing MCP tests and verify representative live Tally reads.
3. Remove agent-controlled privilege escalation and enforce fixed company/endpoint policy.
4. Define job, draft, approval and execution-attempt schemas/contracts.
5. Build per-device SSH enrollment/restrictions and authenticated local gateway.
6. Connect a least-privileged Harness worker through the tunnel/bridge.
7. Deliver verified trial balance, ledger and outstanding reports in the portal.
8. Add source-preserving CSV/XLSX reconciliation and voucher preview.
9. Implement signed approval, durable local journal and read-back posting.
10. Inject disconnect/timeout failures, complete pilot acceptance, then package signed installer.

## 24. Reference sources

Primary sources consulted on 3 November 2026:

- Harness: https://github.com/deepseek-ai/deepseek-harness
- MCP subsystem: https://github.com/deepseek-ai/deepseek-harness/blob/master/docs/subsystems/mcp.md
- Super-agent: https://github.com/deepseek-ai/dsh-superagent-prototype
- Super-agent version requirements: https://github.com/deepseek-ai/dsh-superagent-prototype/blob/main/package.json
- RAJ adapter: https://github.com/vrajkher/RAJTALLYFINLMCP
- RAJ settings: https://github.com/vrajkher/RAJTALLYFINLMCP/blob/main/src/rajtally_mcp/config.py
- RAJ server: https://github.com/vrajkher/RAJTALLYFINLMCP/blob/main/src/rajtally_mcp/server.py
- OpenSSH server configuration: https://man.openbsd.org/sshd_config
- OpenSSH client options: https://man.openbsd.org/ssh.1
- Windows OpenSSH: https://learn.microsoft.com/en-us/windows-server/administration/openssh/openssh_overview

All implementation choices, effort estimates, service objectives and product features beyond the verified starting point are proposals to validate, not capabilities already delivered by these repositories.
