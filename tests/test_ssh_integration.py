"""Opt-in actual sshd tests; only run as root on a disposable Linux CI runner."""
from __future__ import annotations
import os
from pathlib import Path
import signal
import socket
import sqlite3
import subprocess
import sys
import time
import pytest
from infra.ssh.verify_restrictions import free_port, reverse_forward, run_checks, ssh_options

pytestmark = pytest.mark.skipif(
    sys.platform != "linux" or os.environ.get("RAJ_RUN_SSH_INTEGRATION") != "1"
    or getattr(os, "geteuid", lambda: -1)() != 0,
    reason="requires explicit opt-in and root on a disposable Linux sshd runner")
ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def sshd(tmp_path):
    import pwd
    import shlex
    from types import SimpleNamespace
    for name in ("rajtunnel", "raj-keys"):
        try:
            pwd.getpwnam(name)
        except KeyError:
            pass
        else:
            pytest.skip(f"existing account {name}; use a disposable runner")
    created, daemon, log, auth = [], None, None, None
    try:
        for name in ("rajtunnel", "raj-keys"):
            subprocess.run(["useradd", "-M", "-s", "/bin/sh", "-p", "x", name], check=True)
            created.append(name)
        # sshd requires every AuthorizedKeysCommand parent to be root-owned and non-writable.
        auth = Path("/run") / f"raj-ssh-{os.getpid()}-{tmp_path.name}"
        auth.mkdir(mode=0o755)
        auth.chmod(0o755)
        key, hostkey = auth / "device", auth / "host"
        for target in (key, hostkey):
            subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(target)], check=True)
        assigned, port = free_port(), free_port()
        database = auth / "keys.sqlite3"
        with sqlite3.connect(database) as connection:
            connection.execute("CREATE TABLE devices (ssh_pubkey TEXT, tunnel_port INTEGER, status TEXT)")
            connection.execute("INSERT INTO devices VALUES (?,?,'active')", (key.with_suffix('.pub').read_text().strip(), assigned))
        database.chmod(0o644)
        program = auth / "keys-program.py"
        program.write_bytes((ROOT / "infra/ssh/raj-authorized-keys").read_bytes())
        program.chmod(0o644)
        command = auth / "authorized-keys"
        command.write_text("#!/bin/sh\nRAJ_DB=" + shlex.quote(str(database))
                           + " RAJ_SSH_USER=rajtunnel exec /usr/bin/python3 "
                           + shlex.quote(str(program)) + ' "$@"\n')
        command.chmod(0o755)
        control = subprocess.run(["runuser", "-u", "raj-keys", "--", str(command), "rajtunnel"],
                                 capture_output=True, text=True)
        assert control.returncode == 0 and "permitlisten=" in control.stdout, control.stderr
        policy = (ROOT / "infra/ssh/sshd_raj_tunnel.conf").read_text().replace(
            "/opt/raj/infra/ssh/raj-authorized-keys", str(command))
        config = auth / "sshd_config"
        config.write_text(f"Port {port}\nListenAddress 127.0.0.1\nHostKey {hostkey}\n"
                          f"PidFile {auth / 'pid'}\nUsePAM no\nLogLevel VERBOSE\n" + policy)
        Path("/run/sshd").mkdir(exist_ok=True)
        subprocess.run(["/usr/sbin/sshd", "-t", "-f", str(config)], check=True)
        effective = subprocess.check_output(["/usr/sbin/sshd", "-T", "-f", str(config), "-C",
            f"user=rajtunnel,host=localhost,addr=127.0.0.1,laddr=127.0.0.1,lport={port}"], text=True)
        assert "allowagentforwarding no" in effective and "maxsessions 0" in effective
        log = (auth / "sshd.log").open("w+")
        daemon = subprocess.Popen(["/usr/sbin/sshd", "-D", "-e", "-f", str(config)],
                                  stdout=log, stderr=log, start_new_session=True)
        deadline = time.monotonic() + 5
        while True:
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                    break
            except OSError:
                if daemon.poll() is not None or time.monotonic() >= deadline:
                    raise AssertionError((auth / "sshd.log").read_text())
                time.sleep(0.05)
        known = auth / "known_hosts"
        known.write_text(f"[127.0.0.1]:{port} " + " ".join(hostkey.with_suffix('.pub').read_text().split()[:2]) + "\n")
        yield SimpleNamespace(key=key, port=port, assigned=assigned, known=known, database=database, auth=auth)
    finally:
        if daemon is not None:
            try:
                os.killpg(daemon.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            daemon.wait(timeout=10)
        if log:
            log.close()
            print("Isolated sshd log:\n" + (auth / "sshd.log").read_text()[-12000:])
        for name in reversed(created):
            subprocess.run(["userdel", name], check=False, capture_output=True)
        if auth is not None:
            import shutil
            shutil.rmtree(auth)


def test_actual_ssh_restrictions(sshd):
    run_checks("127.0.0.1", str(sshd.key), sshd.assigned, sshd.port, known_hosts=str(sshd.known))
    bad_known = sshd.auth / "wrong_known_hosts"
    bad_known.write_text(f"[127.0.0.1]:{sshd.port} " + " ".join(sshd.key.with_suffix('.pub').read_text().split()[:2]) + "\n")
    options, destination = ssh_options("127.0.0.1", str(sshd.key), sshd.port, known_hosts=str(bad_known))
    result = subprocess.run(options + ["-N", destination], capture_output=True, text=True, timeout=8)
    assert result.returncode != 0 and "host key verification failed" in result.stderr.lower()


def test_portal_mtls_read_over_ssh_and_live_revoke(sshd, env):
    cid, cfg, gw = env.add_client("SSH client")
    env.app.state.db.x("UPDATE devices SET gateway_url=NULL, tunnel_port=? WHERE id=?", (sshd.assigned, cfg.device_id))
    options, destination = ssh_options("127.0.0.1", str(sshd.key), sshd.port, known_hosts=str(sshd.known))
    with reverse_forward(options, destination, sshd.assigned, cfg.listen_port) as tunnel:
        response = env.http.post(f"/api/clients/{cid}/ask", headers=env.admin,
                                 json={"question": "trial balance", "as_of": "2026-10-31"})
        assert response.status_code == 200
        assert response.json()["reports"][0]["meta"]["tie_out"] == "balanced", response.text
        with sqlite3.connect(sshd.database) as connection:
            connection.execute("UPDATE devices SET status='revoked'")
        denied = subprocess.run(options + ["-N", destination], capture_output=True, text=True, timeout=8)
        assert denied.returncode != 0 and "permission denied" in denied.stderr.lower()
        subprocess.run(["sh", str(ROOT / "infra/ssh/raj-kill-tunnel"), str(sshd.assigned)], check=True, timeout=10)
        tunnel.wait(timeout=8)
        with pytest.raises(OSError):
            socket.create_connection(("127.0.0.1", sshd.assigned), timeout=1)
