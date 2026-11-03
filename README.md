# RAJ Finance Agent

A finance agent for CA/CMA practices. Staff ask questions about a client's Tally books, reconcile bank statements, approve exact vouchers, and post them to Tally with verification.

```
Firm server (Linux)                                   Client Tally PC (Windows)
┌──────────────────────────────────────────┐         ┌───────────────────────────────────┐
│ Portal (web/mobile) ─ API ─ SQLite       │         │ RAJ Gateway (mTLS, policy,        │
│ Harness: finance skills + DeepSeek model │         │   signed-token checks, journal)   │
│ Broker/Bridge (mTLS client, cert pinning)│◄═SSH═══►│ Tally XML adapter ─► TallyPrime   │
│ sshd: restricted reverse-forward account │ (client │ ssh -R supervisor (backoff)       │
└──────────────────────────────────────────┘ starts) └───────────────────────────────────┘
```

## Workflow and next steps

- [Complete workflow — ગુજરાતી સમજ + current/planned architecture](docs/WORKFLOW.md)
- [Ordered roadmap — fixes, regression tests, Windows/SSH CI, real Tally and pilot gates](docs/ROADMAP.md)
- [Operations and go-live gates](docs/OPERATIONS.md)

**Implementation boundary:** the current runtime uses a custom Python harness, SQLite and its own Tally XML adapter. RAJTALLYFINLMCP and the separate DeepSeek Harness are planned integrations, not dependencies currently wired into this runtime. The local demo/tests use direct loopback mTLS without SSH.

**Fix update (2026-11-03):** finance matching, ageing, history paging, model-answer grounding, atomic pairing and frozen-startup fixes are implemented with regression coverage. Local Windows validation: 43 passed, 2 Linux-only checks skipped; rebuilt EXE startup passed. CI now includes Windows build/startup and actual isolated sshd tests. Real Tally and service/reboot acceptance gates remain required; see the [roadmap](docs/ROADMAP.md).

## What works now

| Area | Status |
|---|---|
| Ask the books: trial balance (with tie-out), receivables/payables ageing (with "older than N days"), ledger statement, ledger search | Done, tested end to end |
| DeepSeek model loop (OpenAI-compatible tool calling); deterministic planner when no API key | Done; model path tested with a scripted model |
| Pairing: single-use 24h code, local device key and CSR, server-chosen certificate identity, company binding by GUID | Done |
| Security: per-call Ed25519 job tokens, tool allow-list, mTLS with separate device and bridge CAs, hostname-pinned gateway identity, cross-client denial | Done, tested |
| Bank CSV/XLSX: column/date detection with required confirmation, Indian number formats, running-balance check, duplicates | Done |
| Matching: reference+amount, amount+date window, one-to-many | Mutual uniqueness and conservative ambiguity handling; permutation and search-limit regressions tested. |
| Drafts: ledger suggestions with evidence, edit creates a new version and voids approvals | Done |
| Approval: signed envelope over the exact payload hash; maker-checker, high-value threshold | Done |
| Posting: server and local write switches, local SQLite journal, one writer per connector, company re-check, ledger validation, read-back verification | Done |
| Recovery: lost Tally response is resolved by correlation reference (no blind retry); unknown results block further writes until reconciled | Done, failure-injection tested |
| Portal: login, client switcher, Ask, Reconciliation, Jobs/audit trail, Device Health (separate statuses), staff/roles, policy, evidence export | Done, browser-tested |
| SSH: sshd config, per-key `permitlisten`, AuthorizedKeysCommand from the DB, kill-on-revoke, verification script | Loopback/per-key policy corrected; actual isolated sshd integration checks added to CI. Deployment validation still required. |
| Windows: connector CLI, NSSM service installer, PyInstaller build script | Frozen entrypoint fixed; local Windows build and EXE startup passed. Signed installer/service/reboot validation pending. |

**Not yet validated against real TallyPrime.** All Tally request and response shapes are exercised against `raj/tally/fake_server.py`, which mimics Tally's XML collection export and IMPORTDATA. Before any client use, run the read checks and posting tests on a test company. Read [docs/OPERATIONS.md](docs/OPERATIONS.md), section "Go-live gates", first.

## Quick start (local demo, no Tally or SSH needed)

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
pytest -q                       # 27 tests: unit and end-to-end (portal API -> mTLS -> gateway -> fake Tally)
raj-demo --reset                # http://127.0.0.1:8080   admin@demo.local / demo-password-1
```

In the demo:
1. Use **Ask the Books** and choose "Show receivables older than 90 days".
2. In **Bank Reconciliation**, upload `fixtures/hdfc_oct_2026.csv` with bank ledger `HDFC Bank`, preview, then confirm.
3. To post, enable posting in **Device Health**, then run `raj-connector --data var/demo/connector enable-writes`.
4. Select the rows, approve, then post.

To use the DeepSeek model, set `DEEPSEEK_API_KEY` (and optionally `RAJ_MODEL` and `DEEPSEEK_BASE_URL`). Without a key, a rule-based planner picks the skill. Report calculations run in `raj/calculations.py`. The model selects skills; published financial answers use deterministic report summaries. Completion without a successful report is rejected and model-authored final figures are not published.

## Layout

```
raj/contracts.py        canonical JSON, signed job tokens and execution envelopes
raj/calculations.py     Decimal maths: TB, ageing, voucher validation, statement parsing, matching, suggestions
raj/pki.py              device CA + bridge CA, CSR signing
raj/tally/              Tally XML client (client PC only) + fake Tally for dev/tests
raj/gateway/            local gateway (FastAPI over mTLS) + durable journal
raj/connector/          raj-connector CLI: pair, run (gateway + ssh -R supervisor), status, pause, write switch
raj/server/             portal API, auth/roles, broker+bridge, harness (skills + model loop), jobs/approvals/posting
raj/server/static/      portal (dependency-free HTML/JS, responsive, light/dark)
infra/ssh|deploy|windows  sshd config, key command, systemd, Caddy, backups, Windows service install
docs/                   product plan, implementation plan, operations runbook
```
