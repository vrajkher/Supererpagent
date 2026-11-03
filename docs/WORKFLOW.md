# RAJ Finance Agent — end-to-end workflow

Updated: 2026-11-03 (India time).

## સરળ સમજ

Staff Portal માં client પસંદ કરીને કામ આપે છે. Harness યોગ્ય finance Skill પસંદ કરે છે. Server Bridge SSH tunnel મારફતે client ના Local Gateway સુધી પહોંચે છે. Gateway permissions અને company તપાસીને Tally adapter દ્વારા data વાંચે અથવા approved voucher post કરે છે. પરિણામ ફરી Portal માં આવે છે.

**હાલની સ્થિતિ (update):**
- Gateway હવે default તરીકે locked RAJTALLYFINLMCP વાપરે છે (`raj/tally/mcp_backend.py`); `--backend xml` થી જૂનો `xml_client.py` adapter પણ ચાલે છે.
- પ્રશ્નો DeepSeek Harness session તરીકે ચાલે છે (`raj/server/dsh_worker.py`).
- બંને માત્ર RAJTALLYFINLMCPના demo Tally અને scripted model સાથે tested છે. Real TallyPrime અને live DeepSeek હજી verified નથી.

## 1. Current implementation

```mermaid
flowchart TD
    USER["Staff browser / mobile"] --> PORTAL["Portal + FastAPI API"]
    PORTAL --> DB["SQLite: users, clients, jobs, drafts, approvals, events"]
    PORTAL --> HARNESS["DeepSeek Harness session (sdk-minimal, patched: no shell/files)"]
    HARNESS <--> MODEL["DeepSeek Messages API"]
    HARNESS --> AMCP["RAJ MCP bridge (stdio): finance skills + RAJTALLYFINLMCP read tools"]
    AMCP --> SESSION["Read job token: allowed tools, org/client/device/company"]
    SESSION --> BRIDGE["Bridge: device-hostname-verified mTLS"]
    PORTAL --> APPROVAL["Draft review + signed execution envelope"]
    APPROVAL --> BRIDGE
    BRIDGE --> ROUTE["Server loopback connector port"]
    ROUTE --> TUNNEL["SSH reverse tunnel: intended production transport"]
    TUNNEL --> GATEWAY["Windows Local Gateway: identity + policy"]
    GATEWAY --> JOURNAL["Local SQLite execution journal"]
    GATEWAY --> READER["RAJTALLYFINLMCP reader: --lock, read profile"]
    GATEWAY --> WRITER["RAJTALLYFINLMCP writer: --lock, post profile, GUID check"]
    READER --> TALLY["TallyPrime XML/HTTP"]
    WRITER --> TALLY
```

The local demo and existing end-to-end tests connect Bridge directly to Gateway over loopback mTLS, without sshd. Production SSH configuration exists but requires correction and actual tunnel testing; see [ROADMAP.md](ROADMAP.md).

The central database is SQLite.

- **Runtime.** With `DEEPSEEK_API_KEY` set, each question runs as a session in the real DeepSeek Harness runtime (`deepseek-harness-sdk`, pinned).
- **Tools.** The session's only tool server is the RAJ MCP bridge, which holds a read-only job token. A per-job Cordis patch disables the shell, file and working-directory tools.
- **Without a key.** A deterministic planner runs the same skills.
- **Figures.** Every figure in the model's text is checked against the tool data saved for the job. Unsupported figures are flagged as unverified.

## 2. RAJTALLYFINLMCP integration (implemented; real-Tally validation pending)

```mermaid
flowchart LR
    BR["Server Bridge"] --> SSH["Authenticated reverse tunnel"]
    SSH --> GW["Local Gateway: policy + journal + approval checks"]
    GW --> DRIVER["Scoped MCP adapter (raj/tally/mcp_backend.py)"]
    DRIVER --> MCP["Pinned RAJTALLYFINLMCP stdio children: reader + writer"]
    MCP --> T["Fixed local Tally endpoint"]
```

Keep the Gateway's security and recovery boundaries when adding MCP:

- Pin and test a specific RAJTALLYFINLMCP revision and its actual supported protocol/tools.
- Initialize the MCP session and map only the product-supported read/write operations.
- Fix the permitted Tally endpoint and company binding outside model control.
- Do not expose raw XML/TDL, settings changes or unrestricted write tools to the model.
- Preserve signed exact-payload approvals, local write ceiling, journal and read-back verification.
- Test the real MCP protocol path separately from the current custom REST adapter path.

How each requirement above is met:

- RAJTALLYFINLMCP is pinned to a commit.
- Children start with `--lock --company <bound>`.
- The reader has `--tool-profile read --access read_only`.
- The writer has `--tool-profile post --access read_write --company-guid <bound guid>` and starts only when posting is needed.
- No settings, raw XML, TDL, SQL, alter or delete tools are registered.
- Approvals, the journal and read-back are unchanged.

Tests (`tests/test_mcp_backend.py`) use the real MCP stdio path against RAJTALLYFINLMCP's demo Tally. Real TallyPrime remains a gate. The Bridge ↔ Gateway hop is still REST over mTLS (`/tools/list`, `/tools/call`).

## 3. One-time pairing

1. Firm admin creates a client and requests a single-use enrollment code.
2. The connector lists open Tally companies; the local operator confirms the permitted company.
3. The connector generates a device key/CSR locally and prepares an SSH public key.
4. The server redeems the code and chooses the certificate's device identity.
5. The connector stores its device certificate, bridge CA, signing public key, SSH host-key information and assigned tunnel port.
6. The binding records company name/GUID and books-from date.
7. Posting starts disabled and needs separate server and local enablement.

The code is consumed only after the CSR is validated and the certificate issued. Redemption and device creation happen in one transaction (R7).

## 4. Connection and trust

The client PC starts an outbound OpenSSH connection. It requests a reverse listener on the server's loopback interface, mapped to the local Gateway. The Bridge connects through that route and verifies the Gateway's device hostname through mTLS.

| Layer | Responsibility |
|---|---|
| Portal API | Signed-in user, organization, client assignment and role |
| Harness / ToolSession | Select supported skills, constrain tools and issue job context |
| SSH | Restricted network transport; no general command sessions |
| mTLS | Authenticate the server Bridge and expected device Gateway |
| Job token | Bind capability/tools to org, client, device and company |
| Execution envelope | Bind a write approval to the exact voucher payload |
| Local journal / read-back | Track attempted effects and verify what Tally stored |

A connected SSH process is not proof that Gateway, Tally and the company are ready. Keep these statuses separate. The corrected `PermitListen` configuration is exercised against a real sshd by `infra/ssh/ci-sshd-test.sh` (R2). Re-run it on the production OpenSSH version.

## 5. Read-only question

Example: “90 દિવસથી વધુ બાકી receivables બતાવો.”

1. Portal validates client access and creates a read-report job.
2. Harness selects a skill using the model or deterministic rules fallback.
3. ToolSession creates a signed read token with an allowed-tool set.
4. Bridge resolves the device route and calls Gateway over mTLS.
5. Gateway checks the token, local pause policy and company binding.
6. RAJTALLYFINLMCP (reader) reads Tally; with `--backend xml` the built-in adapter does.
7. Decimal calculation code computes totals, ageing or running balances.
8. Portal shows report metadata, tables, totals and job activity.

The model may select tools and explain results. Financial values stay tied to tool results: the answer's figures are checked against the job's saved tool data. Any unsupported figure marks the answer `unverified` in the portal (R4).

## 6. Bank reconciliation and draft preparation

1. Upload CSV/XLSX; persist the source and its SHA-256.
2. Preview and explicitly confirm column mapping and date format.
3. Validate running balances and parse every accepted transaction.
4. Read the selected bank ledger and relevant Tally vouchers.
5. Classify statement rows: matched, unmatched, duplicate-source or ambiguous.
6. Suggest ledgers for unmatched rows; create reviewable voucher drafts.
7. A staff edit increments the draft version and invalidates unused approvals.
8. Reviewer checks exact rows, company, date, ledger and amount.

Ambiguous matches are never auto-accepted. Matching is order-independent (R3). Multi-year books are read in bounded windows (R6).

## 7. Approval and posting

```mermaid
sequenceDiagram
    participant R as Reviewer
    participant S as Server
    participant G as Local Gateway
    participant T as Tally
    R->>S: Approve exact draft version and rows
    S->>S: Validate role / maker-checker / threshold
    S->>S: Sign execution envelope over payload hash
    S->>G: Signed write token + envelope + payload
    G->>G: Check identity, signature, expiry, nonce, hash
    G->>G: Check local write switch, company and ledgers
    G->>G: Persist INTENT then SENT
    G->>T: Create voucher
    G->>T: Read back by correlation reference
    T-->>G: Stored accounting fields
    G-->>S: VERIFIED / REJECTED / UNKNOWN / MISMATCH
    S-->>R: Row result and audit evidence
```

Both server and local write switches are required on the normal product path. Acknowledging a request is not proof of posting. Only verified read-back establishes the stored result.

## 8. Connection loss and restart recovery

Journal intent is persisted before mutation. Unfinished INTENT/SENT records become UNKNOWN on restart and block subsequent local writes until reconciliation.

- Exactly one matching voucher: verify the accounting fields and mark VERIFIED.
- Multiple or differing vouchers: preserve uncertainty and require manual review.
- No voucher found: the current implementation marks NOT_POSTED and requires fresh approval. Absence must be validated against real Tally's timing/visibility; it is not universally proof that a delayed import cannot appear later.
- Never blindly replay an uncertain write.

Central-server recovery, delayed results and crash windows need additional tests; the local journal alone does not establish complete end-to-end crash recovery.

## 9. Pause, revoke and operations

- Local pause blocks new work; local disable-writes blocks posting.
- Server disable-writes blocks new normal-path posting requests.
- Revocation blocks server routing, invalidates unused approvals and should terminate the established SSH session.
- Reconnect must re-establish identity and reconcile unfinished effects before accepting new writes.
- Test backup/restore, certificate renewal, service restart and device offboarding.

Accounting data travels from client PC to the firm server. If the model is enabled, questions, company context and sampled report results also reach the configured provider. Do not describe this architecture as zero data egress.

## 10. Validation and next steps

Follow [ROADMAP.md](ROADMAP.md) in order: fix defects and add regression tests → add Windows/actual-SSH CI → real-Tally read comparisons → test-company posting/failure recovery → small client pilot.

Operational setup and go-live procedures remain in [OPERATIONS.md](OPERATIONS.md).
