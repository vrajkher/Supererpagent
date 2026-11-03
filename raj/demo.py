"""One-command local demo: fake Tally + server + paired connector (direct mTLS, no SSH).

    python -m raj.demo            # then open http://127.0.0.1:8080  (admin@demo.local / demo-password-1)
"""
from __future__ import annotations

import argparse
import os
import shutil
import socket
import threading
import time
from pathlib import Path

import httpx
import uvicorn


def _free() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--dir", default="./var/demo")
    ap.add_argument("--reset", action="store_true")
    a = ap.parse_args()
    root = Path(a.dir)
    if a.reset and root.exists():
        shutil.rmtree(root)
    os.environ["RAJ_ALLOW_DIRECT_GATEWAY"] = "1"

    from .connector.cli import gateway_server, pair
    from .server.app import Settings, create_app, init_org
    from .tally.fake_server import FakeTally, serve

    tally_srv, tport = serve(FakeTally())
    tally_url = f"http://127.0.0.1:{tport}"
    first = not (root / "server" / "raj.sqlite3").exists()
    (root / "server").mkdir(parents=True, exist_ok=True)
    if first:
        init_org(root / "server", "Demo CA Firm", "admin@demo.local", "demo-password-1")
    app = create_app(Settings(root / "server"))
    srv = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=a.port, log_level="warning"))
    threading.Thread(target=srv.run, daemon=True).start()
    while not srv.started:
        time.sleep(0.05)
    base = f"http://127.0.0.1:{a.port}"

    conn = root / "connector"
    if first:
        with httpx.Client(base_url=base, timeout=30) as h:
            tok = h.post("/api/login", json={"email": "admin@demo.local", "password": "demo-password-1"}).json()["token"]
            hd = {"Authorization": f"Bearer {tok}"}
            cid = h.post("/api/clients", json={"name": "Demo Traders (Client A)"}, headers=hd).json()["id"]
            code = h.post(f"/api/clients/{cid}/enrollment", headers=hd).json()["code"]
            gport = _free()
            pair(base, code, conn, tally_url, "Demo Traders Pvt Ltd", gport, direct_url=f"https://127.0.0.1:{gport}",
                 http=h, assume_yes=True)
    from .gateway.app import GatewayConfig
    cfg = GatewayConfig.load(conn / "config.json")
    cfg.tally_url = tally_url
    cfg.save(conn / "config.json")
    _, gsrv = gateway_server(cfg)
    threading.Thread(target=gsrv.run, daemon=True).start()
    print(f"\nRAJ demo running: {base}\n  login: admin@demo.local / demo-password-1\n"
          f"  fake Tally: {tally_url}  (NOT real Tally)\n  sample statement: fixtures/hdfc_oct_2026.csv (bank ledger: HDFC Bank)\n"
          f"  enable posting: Device Health -> Enable posting, and `raj-connector --data {conn} enable-writes`\n")
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        tally_srv.shutdown()


if __name__ == "__main__":
    main()
