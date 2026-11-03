# RAJ Finance Agent — Operations

## 1. Central server (Ubuntu/Debian)

```bash
sudo useradd -r -m -d /opt/raj raj && sudo useradd -r raj-keys && sudo useradd -m -s /usr/sbin/nologin rajtunnel
sudo -u raj git clone <this repo> /opt/raj && cd /opt/raj
sudo -u raj python3 -m venv .venv && sudo -u raj .venv/bin/pip install -e ".[agent]"   # DeepSeek Harness runtime
sudo mkdir -p /var/lib/raj/server /etc/raj && sudo chown -R raj /var/lib/raj
sudo cp infra/deploy/server.env.example /etc/raj/server.env && sudo chmod 600 /etc/raj/server.env   # edit values
sudo -u raj .venv/bin/raj-server init --org "Kher & Co" --email you@firm.in --password '<strong>' --data /var/lib/raj/server
sudo cp infra/deploy/raj-server.service /etc/systemd/system/ && sudo systemctl enable --now raj-server
# HTTPS: install Caddy, copy infra/deploy/Caddyfile (set your domain)
```

SSH ingress:

```bash
sudo cp infra/ssh/sshd_raj_tunnel.conf /etc/ssh/sshd_config.d/50-raj-tunnel.conf
# sshd and sudo run these as root: install root-owned copies OUTSIDE the raj-owned checkout
# (a raj-writable script reachable through sudo or AuthorizedKeysCommand would be a root escalation)
sudo install -d -m 755 -o root -g root /usr/local/lib/raj
sudo install -m 755 -o root -g root infra/ssh/raj-authorized-keys infra/ssh/raj-kill-tunnel /usr/local/lib/raj/
sudo setfacl -m u:raj-keys:r /var/lib/raj/server/raj.sqlite3 && sudo setfacl -m u:raj-keys:x /var/lib/raj /var/lib/raj/server
echo 'raj ALL=(root) NOPASSWD: /usr/local/lib/raj/raj-kill-tunnel' | sudo tee /etc/sudoers.d/raj-kill
sudo sshd -t && sudo systemctl reload ssh
ssh-keyscan -t ed25519 <public host>     # put the line in RAJ_SSH_KNOWN_HOSTS
```

Before go-live, run `sudo infra/ssh/ci-sshd-test.sh` on a staging machine with the same OpenSSH version as production. It starts its own throwaway sshd and leaves the host's own SSH configuration alone. Every line must say PASS, and the script exits non-zero otherwise.

The server does not proxy the portal through SSH. Workers reach tunnels only through the bridge, which pins each gateway's identity (`<device_id>.devices.raj`, signed by the device CA). In production, run the API as a dedicated user and do not give other local users access to the loopback tunnel ports.

The finance agent runs on DeepSeek Harness when `DEEPSEEK_API_KEY` is set (`RAJ_AGENT_RUNTIME=auto|dsh`).

- **Session homes.** Each question is one Harness session. Each client gets its own Harness home under `/var/lib/raj/server/dsh-home/<client>`, so no client sees another's history.
- **Per-job files.** These are under `agent-jobs/<job>`: the private job file, whose token is wiped after the run, the Cordis patch, and the saved reports.
- **Pinned version.** The Harness is a developer preview and is pinned exactly (`deepseek-harness-sdk==0.2.1rc2`). Upgrade it only after `pytest -q` passes on the new version.

## 2. Client PC (Windows)

**Installer.** `RAJ-Connector-Setup-<version>.exe` installs everything the client PC needs.
- **Build it** on a Windows build machine with `infra/windows/build-installer.ps1`, then sign it.
- **CI.** Each PR builds an unsigned test copy (artifact `raj-connector-setup-unsigned-test-build`). CI also installs that copy silently, checks it and uninstalls it on `windows-latest`.

What the installer does:
- **Connector.** Installs `raj-connector.exe` to `Program Files\RAJ Connector`. The exe bundles RAJTALLYFINLMCP.
- **Data folder.** Creates `C:\ProgramData\RAJ` for keys, config, journal and log. Only SYSTEM and Administrators can open it.
- **OpenSSH.** Installs the Windows OpenSSH client if it is missing. The tunnel needs it.
- **Pairing.** Asks for the firm server address and pairing code, then pairs the PC. The person at the PC confirms the Tally company in a console window.
- **Background task.** Registers the Task Scheduler task **RAJ Connector**. It runs as SYSTEM at boot and restarts every minute after a failure.
- **Start menu.** Adds these shortcuts: Pair this PC, Connector status, Pause, Resume, Allow posting, Block posting, Open connector log, Read me.

Steps:
1. Confirm TallyPrime's XML/HTTP server is on port 9000 and the client's company is open.
2. In the portal, select the client, go to **Device Health**, and choose **Create pairing code**.
3. On the client PC, run `RAJ-Connector-Setup-<version>.exe`. Enter the server address and code, then confirm the company. For an IT rollout, use `/VERYSILENT /SERVER=https://... /CODE=RAJ-...`.
4. Device Health should then show PC online → SSH connected → Gateway ok → Tally running → Company ready.
5. **Uninstall:** use Settings → Apps → RAJ Connector. Then revoke the device in the portal.
   - Uninstall removes the background task.
   - It keeps `C:\ProgramData\RAJ` unless the person chooses to delete it.

To use the minimal built-in XML adapter instead, pair from the command line with `--backend xml`.

Local controls on the PC (Start menu shortcuts, or the commands):

| Command | Effect |
|---|---|
| `raj-connector status` | Health and recent writes |
| `raj-connector pause` / `resume` | Stops or allows all new work; applies to the next request |
| `raj-connector enable-writes` / `disable-writes` | Local posting switch. Posting needs this **and** the server switch |
| `raj-connector service status / start / stop` | The background task |

## 3. Daily work

| Task | Where |
|---|---|
| Questions and reports | **Ask the Books**. Every report shows company, period, extraction time and tie-out |
| Bank reconciliation | Upload → confirm columns and date format → review matched, unmatched, ambiguous and duplicate rows → choose ledgers → approve exact vouchers → post. Each posted row shows the Tally voucher id after read-back |
| Evidence | Each job has **Download evidence** (events, approvals with payload hashes, attempts, source file hash) |

## 4. Incident runbook

| Symptom | Action |
|---|---|
| Device Health: SSH down | The PC is off or offline, or the service is stopped. Check `connector.log` on the PC. Jobs fail with "offline"; nothing is fabricated |
| Company "not open" | Ask the client to open the bound company in Tally. RAJ never switches companies |
| A row is `unknown` / job `RESULT_UNKNOWN` | Choose **Reconcile unknown results**. The gateway searches Tally by the `RAJ-…` reference. Found and matching means posted. Not found means `not_posted` and needs a fresh approval. More than one found means manual review in Tally. Do **not** re-enter the voucher by hand until this is resolved |
| "earlier write(s) have unknown result" | Same as above. The connector blocks all writes until unknown results are resolved |
| Revoke a PC | Device Health → **Revoke**. This voids pending approvals, removes the key from the AuthorizedKeysCommand output and closes the live tunnel |
| Lost or stolen PC | Revoke, then pair a new device with a new code. Old certificates are rejected because the bridge pins the device id |

## 5. Go-live gates (must pass before real clients)

1. **Live Tally reads.** On a test company in the pinned TallyPrime release:
   - `raj-connector companies` lists the company with its GUID;
   - the trial balance, ageing and ledger statement match Tally's own reports, including rounding;
   - Gujarati ledger names display correctly.
2. **Live Tally writes.** On the test company:
   - post a Payment, a Receipt and a Contra;
   - confirm the `REFERENCE` field (or the narration tag `[RAJ-…]`) survives so `find_by_ref` works;
   - kill the network mid-post and confirm recovery finds the voucher.
3. **Bill export.** The `Bill` collection fields (`BillDate`, `ClosingBalance`, `Parent`) behave as assumed. If they don't, adjust `raj/tally/xml_client.py`.
4. **SSH.** `ci-sshd-test.sh` passes on the production OpenSSH version. Revocation closes a live tunnel.
5. **Windows.** Build and sign the installer. Install it on a real client-type PC, reboot, and confirm the task starts and reconnects. Test with Tally running in the logged-in user session. CI covers silent install, verify and uninstall; it does not cover reboot or a real Tally.
6. **Backups.** `infra/deploy/backup.sh` runs, and a restore drill includes pending approvals and unknown writes.
7. **Pilot.** Two monthly cycles with 3–5 clients, accepted by the finance reviewer.

## 6. Data handling

- Accounting data leaves the client PC to the firm server for reports and drafts. If `DEEPSEEK_API_KEY` is set, tool results also go to DeepSeek as model context: at most 40 rows per RAJ report section, and at most 24,000 characters per RAJTALLYFINLMCP result.
- Do not describe this to clients as "data never leaves your PC".
- Bank statements are stored under `RAJ_DATA_DIR/sources/<client>/<sha256>`. Set retention to match your engagement letters.
