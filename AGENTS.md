# AGENTS.md: rules for coding agents (DeepSeek Super-agent workers included)

RAJ Finance Agent reads and posts accounting entries in clients' Tally books. Correctness and isolation matter more than speed. Read `README.md` and `docs/IMPLEMENTATION_PLAN.md` first.

## Layout

- `raj/contracts.py`: signing and canonical JSON
- `raj/calculations.py`: Decimal maths
- `raj/gateway/`: client-PC gateway and journal
- `raj/tally/`: Tally adapters: built-in XML, RAJTALLYFINLMCP over MCP, and a fake Tally
- `raj/connector/`: connector CLI
- `raj/server/`: API, portal, bridge, harness skills, DeepSeek Harness worker (`dsh_worker.py`), RAJ MCP bridge (`agent_mcp.py`)
- `infra/`: deployment

## Before you push

```sh
python3 -m venv .venv && .venv/bin/pip install -e '.[dev,agent,tally]'
.venv/bin/pytest -q          # all must pass; the DeepSeek Harness and MCP tests skip only if their extras are missing
```

For a bug, add a test that fails before the fix. Never skip, xfail, loosen or delete a test to get green.

## Hard rules (do not cross without the maintainer's explicit approval in the issue)

1. **No new write path.** Only the gateway's `post_voucher` writes to Tally, and only with:
   - a valid server-signed execution envelope;
   - a matching payload hash and an unused nonce;
   - the local write switch on;
   - a journal entry written first;
   - a read-back afterwards.

   The model gets no write tool. A read job token never becomes a write.
2. **No weaker isolation.** Keep all of these:
   - the job token's org, client, device and company binding;
   - the bridge's device-hostname and CA check;
   - separate device and bridge CAs;
   - the gateway's company GUID check;
   - RAJTALLYFINLMCP launched with `--lock` and the `read` / `post` profiles.
3. **Harness worker stays tool-poor.** `raj/server/dsh_worker.py` `PATCH` keeps shell, file and working-directory tools disabled and mounts only the `raj` MCP server.
4. **Money is Decimal.** No float arithmetic on amounts. Convert foreign floats at the boundary with `calculations.money(repr(x))`.
5. **Unknown means unknown.** After a lost Tally response, reconcile by correlation reference. Never blindly retry a write.
6. **No secrets or client data in the repo.** Fixtures are synthetic (`fixtures/`, `tests/`).
7. **RAJTALLYFINLMCP changes go to its own repository.** Here, only bump the pinned commit in `pyproject.toml`.

## Style

- Python 3.11+, standard library first, small functions, type hints on public functions.
- Keep comments for *why*, not *what*.
- User-facing text is plain English. Short Gujarati summaries are welcome in the portal.
