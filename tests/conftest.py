import socket
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from raj.connector.cli import gateway_server, pair
from raj.server.app import Settings, create_app, init_org
from raj.tally.fake_server import FakeTally, serve
from raj.tally.xml_client import TallyClient

ADMIN = ("admin@firm.test", "correct-horse-battery")


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


class Env:
    pass


def login(client, email, pw):
    r = client.post("/api/login", json={"email": email, "password": pw})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


def start_gateway(cfg):
    gw, srv = gateway_server(cfg, TallyClient(cfg.tally_url, timeout=5))
    t = threading.Thread(target=srv.run, daemon=True)
    t.start()
    for _ in range(100):
        if srv.started:
            break
        time.sleep(0.05)
    return gw, srv


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("RAJ_ALLOW_DIRECT_GATEWAY", "1")
    e = Env()
    e.tally = FakeTally()
    e.tally_srv, tport = serve(e.tally)
    e.tally_url = f"http://127.0.0.1:{tport}"
    sdir = tmp_path / "server"
    sdir.mkdir()
    init_org(sdir, "Kher & Co", *ADMIN)
    e.app = create_app(Settings(sdir), llm=None)
    e.http = TestClient(e.app)
    e.admin = login(e.http, *ADMIN)
    e.gateways = []

    def add_client(name, company="Demo Traders Pvt Ltd"):
        cid = e.http.post("/api/clients", json={"name": name}, headers=e.admin).json()["id"]
        code = e.http.post(f"/api/clients/{cid}/enrollment", headers=e.admin).json()["code"]
        port = free_port()
        cfg = pair("http://testserver", code, tmp_path / f"conn-{cid[:8]}", e.tally_url, company, port,
                   direct_url=f"https://127.0.0.1:{port}", http=e.http, assume_yes=True)
        gw, srv = start_gateway(cfg)
        e.gateways.append(srv)
        return cid, cfg, gw

    e.add_client = add_client
    yield e
    for s in e.gateways:
        s.should_exit = True
    e.tally_srv.shutdown()
