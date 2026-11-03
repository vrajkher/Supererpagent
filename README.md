# RAJ Finance Agent

A finance agent for CA/CMA practices. Staff ask questions about a client's Tally books, reconcile bank statements, approve exact vouchers, and post them to Tally with verification.

```
Firm server (Linux)                                   Client Tally PC (Windows)
┌──────────────────────────────────────────┐         ┌───────────────────────────────────┐
│ Portal (web/mobile) ─ API ─ SQLite/PG    │         │ RAJ Gateway (mTLS, policy,        │
│ DeepSeek Harness session per question    │         │   signed-token checks, journal)   │
│   └ only tool server: RAJ MCP bridge     │         │ RAJTALLYFINLMCP, locked:          │
│ Broker/Bridge (mTLS client, cert pinning)│◄═SSH═══►│   reader (read) + writer (post)   │
│ sshd: restricted reverse-forward account │ (client │   ─► TallyPrime (XML :9000)       │
└──────────────────────────────────────────┘ starts) │ ssh -R supervisor (backoff)       │
                                                     └───────────────────────────────────┘
Development: DeepSeek Super-agent (separate machine and account) ─► pull requests on this repo
```

## The three DeepSeek / RAJ components

| Component | Role here | Where |
|---|---|---|
| **DeepSeek Harness** (`deepseek-harness-sdk`, real runtime) | Runs each "Ask the books" question as a Harness session. A per-job Cordis patch removes shell, file and working-directory tools and mounts one MCP server: the RAJ bridge. Each client has its own Harness home | `raj/server/dsh_worker.py`, `raj/server/agent_mcp.py` |
| **RAJTALLYFINLMCP** (54 tools) | The connector's Tally adapter. Two locked child processes on the client PC: *reader* (`--tool-profile read`, read_only) and *writer* (`--tool-profile post`, voucher creation only, company GUID re-checked before every write). The model can use every read tool: P&L, balance sheet, GST, GSTR-1, HSN, TDS, bank reconciliation, audit checks, stock, vouchers and masters | `raj/tally/mcp_backend.py`; hardening in the RAJTALLYFINLMCP repo (`policy.py`) |
| **DeepSeek Super-agent** | Develops this repository: it turns issues and comments from allowed people into draft PRs and fixes red CI on `main`. Its workers follow `AGENTS.md`. It never touches production | `infra/superagent/`, `AGENTS.md` |

## Workflow and next steps

- [Complete workflow — ગુજરાતી સમજ + current/planned architecture](docs/WORKFLOW.md)
- [Ordered roadmap — fixes, regression tests, Windows/SSH CI, real Tally and pilot gates](docs/ROADMAP.md)
- [Operations and go-live gates](docs/OPERATIONS.md)

**Implementation boundary:**
- The finance agent runs on the DeepSeek Harness runtime when a key is configured; otherwise the rule-based planner runs.
- The connector uses RAJTALLYFINLMCP by default; the built-in XML adapter is still available with `--backend xml`.
- Central state is SQLite.
- The local demo and tests use direct loopback mTLS without SSH.
- Harness and RAJTALLYFINLMCP are tested against a scripted model and RAJTALLYFINLMCP's demo Tally, not against live DeepSeek inference or real TallyPrime.

**Review update (2026-11-03):** an independent Windows review passed the original 27 tests but found accounting, model-evidence, onboarding, Windows packaging and SSH configuration defects (R1–R7). [ROADMAP.md](docs/ROADMAP.md) records each one and whether this branch addresses it. Follow the roadmap before client posting.

## What works now

| Area | Status |
|---|---|
| Ask the books: trial balance (with tie-out), receivables/payables ageing (with "older than N days"), ledger statement, ledger search | Done, tested end to end |
| DeepSeek Harness worker: real runtime, RAJ MCP bridge as its only tools, finance skills plus all RAJTALLYFINLMCP read tools, cannot post, token wiped after each run | Done. Tested end to end through the real Harness with a scripted offline model (`raj/testing/fake_deepseek.py`). **Not yet run against the live DeepSeek API** |
| RAJTALLYFINLMCP connector backend: locked reader and writer, pass-through read catalog, posting with read-back, lost-response reconciliation | Done, tested against RAJTALLYFINLMCP's demo Tally |
| Super-agent development setup: profile, Policy/Source/Executor settings matching its schema, team prompts, `AGENTS.md` | Written. **Not started here: it needs a dedicated GitHub bot account, tmux and a long-running Harness Web profile** |
| Rule-based planner when no API key; direct DeepSeek tool loop (`RAJ_AGENT_RUNTIME=direct`) | Done |
| Pairing: single-use 24h code, local device key and CSR, server-chosen certificate identity, company binding by GUID | Done |
| Security: per-call Ed25519 job tokens, tool allow-list, mTLS with separate device and bridge CAs, hostname-pinned gateway identity, cross-client denial | Done, tested |
| Bank CSV/XLSX: column/date detection with required confirmation, Indian number formats, running-balance check, duplicates | Done |
| Matching: reference+amount, amount+date window, one-to-many | Implemented; competing-row uniqueness defect reproduced. See roadmap R3. |
| Drafts: ledger suggestions with evidence, edit creates a new version and voids approvals | Done |
| Approval: signed envelope over the exact payload hash; maker-checker, high-value threshold | Done |
| Posting: server and local write switches, local SQLite journal, one writer per connector, company re-check, ledger validation, read-back verification | Done |
| Recovery: lost Tally response is resolved by correlation reference (no blind retry); unknown results block further writes until reconciled | Done, failure-injection tested |
| Portal: login, client switcher, Ask, Reconciliation, Jobs/audit trail, Device Health (separate statuses), staff/roles, policy, evidence export | Done, browser-tested |
| SSH: sshd config, per-key `permitlisten`, AuthorizedKeysCommand from the DB, kill-on-revoke, verification script | Written. **Not executed here: no sshd in the build container** |
| Windows: connector CLI, NSSM service installer, PyInstaller build script | Build reproduced on Windows; frozen EXE startup fails with a relative-import error. Service/reboot validation pending. |

**Not yet validated against real TallyPrime.** All Tally request and response shapes are exercised against `raj/tally/fake_server.py`, which mimics Tally's XML collection export and IMPORTDATA. Before any client use, run the read checks and posting tests on a test company. Read [docs/OPERATIONS.md](docs/OPERATIONS.md), section "Go-live gates", first.

## Quick start (local demo, no Tally or SSH needed)

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev,agent,tally]"
pytest -q                       # 36 tests, unit and end to end
raj-demo --reset                # http://127.0.0.1:8080   admin@demo.local / demo-password-1
raj-demo --reset --backend mcp --agent dsh-fake   # RAJTALLYFINLMCP + real DeepSeek Harness, offline scripted model
DEEPSEEK_API_KEY=... raj-demo --reset --backend mcp --agent dsh   # real model
```

The 36 tests cover:
- unit tests;
- portal API → mTLS → gateway → fake Tally;
- the gateway on RAJTALLYFINLMCP;
- the DeepSeek Harness worker.

In the demo:
1. Use **Ask the Books** and choose "Show receivables older than 90 days".
2. In **Bank Reconciliation**, upload `fixtures/hdfc_oct_2026.csv` with bank ledger `HDFC Bank`, preview, then confirm.
3. To post, enable posting in **Device Health**, then run `raj-connector --data var/demo/connector-xml enable-writes`.
4. Select the rows, approve, then post.

With `DEEPSEEK_API_KEY` set and the `agent` extra installed, questions run on DeepSeek Harness (`RAJ_AGENT_RUNTIME=auto`). `RAJ_MODEL` picks the model, `deepseek-flash` by default or `deepseek-v4-pro`. Without a key, a rule-based planner picks the skill.

Figures come from code, never from the model:
- RAJ skills total with Decimal in `raj/calculations.py`.
- Other reports come straight from RAJTALLYFINLMCP.

## Layout

```
raj/contracts.py        canonical JSON, signed job tokens and execution envelopes
raj/calculations.py     Decimal maths: TB, ageing, voucher validation, statement parsing, matching, suggestions
raj/pki.py              device CA + bridge CA, CSR signing
raj/tally/              Tally adapters: RAJTALLYFINLMCP over MCP (default), built-in XML client, fake Tally
raj/gateway/            local gateway (FastAPI over mTLS) + durable journal
raj/connector/          raj-connector CLI: pair, run (gateway + ssh -R supervisor), status, pause, write switch
raj/server/             portal API, auth/roles, broker+bridge, skills, DeepSeek Harness worker + RAJ MCP bridge, jobs/approvals/posting
raj/testing/            scripted fake DeepSeek Messages API for offline tests
raj/server/static/      portal (dependency-free HTML/JS, responsive, light/dark)
infra/ssh|deploy|windows  sshd config, key command, systemd, Caddy, backups, Windows service install
infra/superagent/       DeepSeek Super-agent profile, settings and prompts for developing this repo
docs/                   product plan, implementation plan, operations runbook
```
