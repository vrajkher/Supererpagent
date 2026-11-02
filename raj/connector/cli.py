"""RAJ Windows connector CLI (also runs on Linux/macOS for testing).

  raj-connector companies --tally http://127.0.0.1:9000
  raj-connector pair --server https://raj.example.in --code RAJ-XXXX-... [--company "Name"]
  raj-connector run            # gateway (mTLS) + SSH reverse tunnel supervisor
  raj-connector status | pause | resume | enable-writes | disable-writes
"""
from __future__ import annotations

import argparse
import json
import os
import random
import shutil
import socket
import ssl
import subprocess
import sys
import threading
import time
from pathlib import Path

import httpx

from .. import pki
from ..gateway.app import Gateway, GatewayConfig, create_app
from ..tally.xml_client import TallyClient, TallyError


def default_data_dir() -> Path:
    base = os.environ.get("RAJ_CONNECTOR_DIR") or (
        Path(os.environ.get("PROGRAMDATA", "C:/ProgramData")) / "RAJ" if os.name == "nt" else Path.home() / ".raj-connector")
    return Path(base)


def _protect(path: Path) -> None:
    """Restrict a secret file to the current account (best effort on Windows)."""
    if os.name == "nt":
        user = os.environ.get("USERNAME", "")
        subprocess.run(["icacls", str(path), "/inheritance:r", "/grant:r", f"{user}:F", "/grant:r", "SYSTEM:F"],
                       check=False, capture_output=True)
    else:
        path.chmod(0o600)


def choose_company(tally: TallyClient, name: str | None) -> dict:
    comps = tally.open_companies()
    if not comps:
        raise SystemExit("No company is open in Tally. Open the client's company and retry.")
    if name:
        m = [c for c in comps if c["name"] == name]
        if not m:
            raise SystemExit(f"Company '{name}' is not open. Open companies: {[c['name'] for c in comps]}")
        return m[0]
    if len(comps) == 1:
        return comps[0]
    for i, c in enumerate(comps, 1):
        print(f"  {i}. {c['name']}  (books from {c['books_from']})")
    k = int(input("Select the company this PC is allowed to serve: "))
    return comps[k - 1]


def pair(server: str, code: str, data: Path, tally_url: str, company: str | None, listen_port: int,
         direct_url: str | None = None, http: httpx.Client | None = None, server_ca: str | None = None,
         assume_yes: bool = False) -> GatewayConfig:
    data.mkdir(parents=True, exist_ok=True)
    tally = TallyClient(tally_url)
    comp = choose_company(tally, company)
    print(f"Company: {comp['name']}  GUID {comp['guid']}")
    if not assume_yes and input("Confirm this is the only company RAJ may access [y/N]: ").strip().lower() != "y":
        raise SystemExit("cancelled")

    key = pki.new_key()
    (data / "gateway.key").write_bytes(pki.key_pem(key))
    _protect(data / "gateway.key")
    ssh_pub = ""
    ssh_keygen = shutil.which("ssh-keygen")
    if ssh_keygen and not (data / "ssh_key").exists():
        subprocess.run([ssh_keygen, "-q", "-t", "ed25519", "-N", "", "-C", "raj-connector", "-f", str(data / "ssh_key")], check=True)
    if (data / "ssh_key.pub").exists():
        ssh_pub = (data / "ssh_key.pub").read_text().strip()
        _protect(data / "ssh_key")

    body = {"code": code, "hostname": socket.gethostname(), "company": comp,
            "csr_pem": pki.make_csr(key, "raj-gateway").decode(), "ssh_pubkey": ssh_pub, "direct_url": direct_url}
    client = http or httpx.Client(base_url=server, timeout=30, verify=server_ca or True)
    r = client.post("/api/enroll", json=body)
    if r.status_code != 200:
        raise SystemExit(f"Pairing failed: {r.status_code} {r.text}")
    res = r.json()
    (data / "gateway.crt").write_text(res["device_cert_pem"])
    (data / "bridge_ca.crt").write_text(res["bridge_ca_pem"])
    ssh = res.get("ssh") or {}
    if ssh.get("known_hosts"):
        (data / "known_hosts").write_text(ssh["known_hosts"].strip() + "\n")
    cfg = GatewayConfig(device_id=res["device_id"], org_id=res["org_id"], client_id=res["client_id"],
                        company_name=comp["name"], company_guid=comp["guid"], server_signing_pub=res["server_signing_pub"],
                        tally_url=tally_url, listen_port=listen_port, data_dir=str(data),
                        tls_cert=str(data / "gateway.crt"), tls_key=str(data / "gateway.key"),
                        bridge_ca=str(data / "bridge_ca.crt"), ssh=ssh, server_url=server)
    cfg.save(data / "config.json")
    print(f"Paired. Device {cfg.device_id}. Writes are disabled until enabled by the firm admin and locally.")
    return cfg


def gateway_server(cfg: GatewayConfig, tally: TallyClient | None = None):
    import uvicorn
    gw = Gateway(cfg, tally, Path(cfg.data_dir) / "config.json")
    app = create_app(gw)
    config = uvicorn.Config(app, host=cfg.listen_host, port=cfg.listen_port, log_level="warning",
                            ssl_certfile=cfg.tls_cert, ssl_keyfile=cfg.tls_key, ssl_ca_certs=cfg.bridge_ca,
                            ssl_cert_reqs=ssl.CERT_REQUIRED)
    return gw, uvicorn.Server(config)


class SshSupervisor(threading.Thread):
    """Keeps ``ssh -N -R 127.0.0.1:<remote>:127.0.0.1:<local>`` alive with backoff + jitter."""

    def __init__(self, cfg: GatewayConfig):
        super().__init__(daemon=True)
        self.cfg = cfg
        self.stop = threading.Event()
        self.state = "starting"

    def command(self) -> list[str]:
        s, d = self.cfg.ssh, Path(self.cfg.data_dir)
        return [shutil.which("ssh") or "ssh", "-N", "-T",
                "-o", "ExitOnForwardFailure=yes", "-o", "ServerAliveInterval=15", "-o", "ServerAliveCountMax=3",
                "-o", "StrictHostKeyChecking=yes", "-o", f"UserKnownHostsFile={d / 'known_hosts'}",
                "-o", "IdentitiesOnly=yes", "-o", "BatchMode=yes", "-o", "ForwardAgent=no", "-o", "ForwardX11=no",
                "-i", str(d / "ssh_key"), "-p", str(s["port"]),
                "-R", f"127.0.0.1:{s['remote_port']}:127.0.0.1:{self.cfg.listen_port}",
                f"{s['user']}@{s['host']}"]

    def run(self) -> None:
        delay = 2.0
        while not self.stop.is_set():
            self.state = "connecting"
            started = time.time()
            p = subprocess.Popen(self.command(), stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            self.state = "connected"
            _, err = p.communicate()
            self.state = f"disconnected: {err.decode(errors='replace').strip()[-200:]}"
            if time.time() - started > 60:
                delay = 2.0
            time.sleep(delay + random.uniform(0, delay / 2))
            delay = min(delay * 2, 300)


def run(data: Path) -> None:
    cfg = GatewayConfig.load(data / "config.json")
    gw, server = gateway_server(cfg)
    sup = None
    if cfg.ssh.get("host"):
        sup = SshSupervisor(cfg)
        sup.start()
    else:
        print("No SSH endpoint configured; gateway reachable only directly (development mode).")
    print(f"RAJ gateway on {cfg.listen_host}:{cfg.listen_port} for company '{cfg.company_name}'")
    print(json.dumps(gw.health(), indent=2))
    server.run()
    if sup:
        sup.stop.set()


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="raj-connector")
    p.add_argument("--data", type=Path, default=default_data_dir())
    sub = p.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("companies")
    c.add_argument("--tally", default="http://127.0.0.1:9000")
    pr = sub.add_parser("pair")
    pr.add_argument("--server", required=True)
    pr.add_argument("--code", required=True)
    pr.add_argument("--tally", default="http://127.0.0.1:9000")
    pr.add_argument("--company")
    pr.add_argument("--listen-port", type=int, default=8765)
    pr.add_argument("--direct-url", help="development only: gateway URL reachable by the server without SSH")
    pr.add_argument("--server-ca", help="CA bundle for a private server certificate")
    pr.add_argument("--yes", action="store_true")
    sub.add_parser("run")
    sub.add_parser("status")
    for name in ("pause", "resume", "enable-writes", "disable-writes"):
        sub.add_parser(name)
    a = p.parse_args(argv)

    if a.cmd == "companies":
        try:
            for comp in TallyClient(a.tally).open_companies():
                print(f"{comp['name']}\t{comp['guid']}\t{comp['books_from']}")
        except TallyError as e:
            sys.exit(f"Tally not reachable: {e}")
    elif a.cmd == "pair":
        pair(a.server, a.code, a.data, a.tally, a.company, a.listen_port, a.direct_url, server_ca=a.server_ca, assume_yes=a.yes)
    elif a.cmd == "run":
        run(a.data)
    else:
        path = a.data / "config.json"
        cfg = GatewayConfig.load(path)
        if a.cmd == "status":
            gw = Gateway(cfg)
            h = gw.health()
            h["ssh_configured"] = bool(cfg.ssh.get("host"))
            h["recent_writes"] = [{k: j[k] for k in ("row_id", "ref", "state", "tally_id")} for j in gw.journal.recent(10)]
            print(json.dumps(h, indent=2, ensure_ascii=False))
            return
        if a.cmd in ("pause", "resume"):
            cfg.paused = a.cmd == "pause"
        else:
            cfg.write_enabled_local = a.cmd == "enable-writes"
        cfg.save(path)
        print(f"{a.cmd}: saved (applies to the next request).")


if __name__ == "__main__":
    main()
