# RAJ Finance Agent — ordered delivery and validation roadmap

Updated: 2026-11-03 (India time). This is a work plan with acceptance gates, not a declaration that the work is complete.

## સૂચિત આગળની ક્રમવાર કામગીરી

1. ઉપરની ખામીઓ સુધારી regression tests ઉમેરો.
2. Windows EXE startup અને actual SSH tunnel tests CI માં ઉમેરો.
3. Real Tallyની **test company** પર પહેલા read-only reports સરખાવો.
4. પછી posting, connection-loss અને restart recovery તપાસો.
5. ત્યારબાદ નાનો client pilot શરૂ કરો.

**દરેક પગલું તેના tests અને પુરાવા સાથે પૂરું થયા પછી જ આગળ વધવું.** હાલના runtime flow અને planned RAJTALLYFINLMCP integration માટે [WORKFLOW.md](WORKFLOW.md) વાંચો.

## Baseline and evidence boundary

Reviewed source: commit `457a90e93c4c706d5abfc4cbf2fcf5ea23c1cbf6`, tree `2f7a8e58a03a5b8cbdad6fb4996729a162059206`. Main merge `a15bfa2b8fa5bb101d6ec051d507092d34f44a26` has the same tree.

Review observations on 2026-11-03:

- The original 27 tests passed on Windows / Python 3.12.14; they use fake Tally and direct loopback mTLS.
- Five additional local regression probes reproduced the matching, ageing, date-range, model-evidence and enrollment-retry defects below. Those probes are review evidence, not tests already committed to the repository.
- Reproducing the documented PyInstaller entry/flags with PyInstaller 6.22.3 built an EXE successfully, but the resulting EXE failed on `--help` with a relative-import error.
- GitHub CI was successful for the reviewed source tree; it currently covers Ubuntu tests, not Windows packaging or actual sshd.
- Real TallyPrime, live DeepSeek inference, Windows service/reboot operation and actual SSH forwarding were not validated.
- RAJTALLYFINLMCP is referenced in plans, but the current runtime uses the custom Tally XML adapter.

Review results must be rerun when code/dependency revisions change. The items below remain open until a fixing change and its acceptance evidence are recorded.

## Step 1 — fix defects and commit regression tests

| ID | Priority | Code boundary / defect | Required acceptance check |
|---|---|---|---|
| R1 | P1 | `infra/windows/build-connector.ps1` builds `raj/connector/__main__.py`, whose relative import fails as a frozen entry | Use a package-aware absolute-import launcher; build the EXE and execute `--help` successfully on a clean Windows runner |
| R2 | P1 | `infra/ssh/sshd_raj_tunnel.conf`: `PermitListen none` denies the intended remote listener; per-key allowance does not override administrative denial | Effective sshd configuration admits the assigned loopback port and denies every other requested listener |
| R3 | P1 | `raj/calculations.py:match_statement`: marking a rival ambiguous makes the next row appear uniquely matched | Two competing bank rows / one voucher remain ambiguous; results are stable under row order permutations |
| R4 | P1 | `raj/server/harness.py`: free-text model figures can be accepted without any tool-backed report | A scripted unsupported numeric answer cannot become a verified financial result; displayed figures bind to report data |
| R5 | P2 | `raj/calculations.py:ageing`: default min_days=0 excludes outstanding bills whose due date is still in the future | Unfiltered ageing includes a not-due bucket and correct total; explicitly overdue-only filters still work |
| R6 | P2 | `Service.reconcile` and ledger statements fetch from books inception while Gateway limits each request to 400 days | Reconcile one month for multi-year books using bounded paging or a verified period-opening balance; totals remain correct |
| R7 | P2 | `/api/enroll` consumes the enrollment code before CSR validation succeeds | Invalid CSR followed by a valid retry works; two concurrent successful enrollments still cannot redeem one code twice |

Additional focused regression coverage:

- Bad signatures, expired tokens, wrong organization/client/device/company, altered payloads and replayed envelopes.
- Concurrent edits/approvals/posting and durable claims; invalidation must survive process restarts.
- Statement ambiguity and missing/invalid fields, partial batch results, late Tally responses and bounded matching searches.
- A no-result model completion, a fabricated amount after a valid tool result, malformed tool arguments and exhausted step/tool budgets.
- A central crash immediately before/after approval consumption and immediately after Gateway verification.

Do not simply raise the 400-day limit or truncate older transactions without preserving the correct opening balance.

**Gate:** fixes reviewed; original suite plus committed regressions pass; README claims match demonstrated behavior. No real-client posting yet.

## Step 2 — Windows EXE and actual SSH integration CI

### Windows job

- Pin a supported Python/build-tool set and install project dependencies.
- Build the connector using the same entry/flags intended for release.
- Execute the frozen EXE `--help` and offline CLI checks.
- Exercise pairing/Gateway mTLS against synthetic services where feasible.
- Publish build logs and test results; do not label an unsigned test artifact as a signed production release.
- Validate signed installer/service installation, reboot, user/session interaction and reconnect on a controlled Windows machine before release.

### Linux sshd job

Use an isolated test sshd with synthetic device keys and a synthetic Gateway. Do not change a host's production SSH configuration.

Prove all of the following:

1. Valid device authentication and assigned loopback reverse forward succeed.
2. A different remote port and non-loopback listener are denied.
3. Shell, command session, PTY, SFTP, local/dynamic forwarding and agent forwarding are denied as intended.
4. The local-forward negative test opens a connection through the listener; creating the local listener alone does not test server destination denial.
5. Host-key mismatch, wrong-device mTLS identity and missing bridge certificate fail.
6. Revoke an active device: reject new authentication/routes and terminate the established tunnel.
7. Network loss reconnects with bounded backoff; shutdown terminates owned child processes.

Replace the current verification script's “any failure means restriction worked” assumptions with explicit authentication/control checks, and return a nonzero exit status if any assertion fails.

OpenSSH reference: [PermitListen](https://man.openbsd.org/sshd_config#PermitListen).

**Gate:** Windows startup and actual sshd allowed/denied tests are required CI checks. Passing ordinary pytest alone does not satisfy this step.

## Step 3 — real Tally test-company read-only comparisons

Required input: an authorized disposable test company, its TallyPrime release/build and representative synthetic or anonymized data. Record the Windows, Tally, connector and adapter revisions in a compatibility matrix.

- Start with server/local writes disabled and a fixed endpoint/company.
- Compare company name/GUID/books-from and multiple-open-company handling.
- Compare trial balance, receivables/payables ageing and ledger statements with native Tally reports.
- Include due/not-due/future dates, financial-year boundaries, multi-year books, opening balances, debit/credit signs and rounding.
- Include Gujarati ledger names, bill fields, closed company, unavailable Tally and constrained date ranges.
- Record requested period, extraction time, native report totals, application totals and explained differences.

If RAJTALLYFINLMCP is required for the release, first pin/integrate it and run these comparisons through its real MCP initialization/tool-call path. Tests of the current custom XML adapter do not establish MCP compatibility.

**Gate:** finance reviewer accepts reproducible read-only tie-outs for the pinned release. Unexplained differences block posting tests.

## Step 4 — controlled posting, connection loss and restart recovery

Use the disposable test company only, with backup/restore available.

- Post representative Payment, Receipt and Contra vouchers.
- Verify exact approved company/date/ledgers/amounts and the correlation reference after read-back.
- Check both write switches, maker-checker and high-value rules, approval expiry, edits and replay rejection.
- Lose the Tally response after import; never blindly retry.
- Test delayed import/read visibility, no matches, multiple matches and mismatching accounting fields.
- Kill SSH, Gateway and central process at INTENT, SENT, approval-consumed and read-back boundaries.
- Restart and reconcile: retain uncertainty until the outcome is established.
- Test partial batches, idempotency, revocation mid-job and pending-write backup/restore.
- Ensure every attempted posting links source hash → draft version → approval → local journal → Tally result.

The current “not found → NOT_POSTED” behavior must be checked against real Tally's timing. A transient missing result must not authorize a duplicate later import.

**Gate:** no unexplained duplicate or untraceable accepted write; unresolved outcomes block further relevant writes and require review.

## Step 5 — small client pilot

Start only after Steps 1–4 pass.

- Pilot with 3–5 explicitly selected clients and named finance reviewers.
- Begin read-only; enable controlled posting per client only after reviewed acceptance.
- Confirm operator training, permissions, provider data-sharing choices and retention.
- Monitor separate PC/tunnel/Gateway/Tally/company states, unknown writes, verification failures and model cost.
- Test support diagnostics, encrypted off-host backups, restore and device revocation.
- Review two monthly cycles before widening rollout, consistent with [OPERATIONS.md](OPERATIONS.md).
- Record issues and a pause/rollback plan; do not silently widen tool or company permissions.

**Gate:** reviewer sign-off, agreed reliability evidence and no unresolved posting-integrity issue.

## Completion record

For each step, record a PR/commit, exact component versions, test command/environment, artifacts, outcome, reviewer and remaining limitations. Update status only after the gate is actually met.

These documentation changes do not implement the fixes, wire RAJTALLYFINLMCP, run live accounting operations or start a pilot.
