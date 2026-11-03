This repository moves accounting entries into clients' books. These are hard limits for autonomous work; a change that needs to cross one is a design question for vrajkher, not a ticket to finish:
- Do not widen what can write to Tally: no new write tool for the model, no path from a read job token to a write, no bypass of the signed execution envelope, the payload hash, the nonce, the local journal or read-back verification.
- Do not weaken isolation: the job token's client/device/company binding, the bridge's device-hostname check, the two separate CAs, the gateway's company GUID check, and RAJTALLYFINLMCP's locked profiles stay as strict or stricter.
- Money stays Decimal end to end; never introduce float arithmetic for amounts.
- Never change the Harness worker patch (raj/server/dsh_worker.py PATCH) to re-enable shell, file or working-directory tools.
- Changes to RAJTALLYFINLMCP belong in that repository; here only bump its pinned version.
Inside those limits, small focused fixes, tests, docs and refactors local to one module are in scope.
