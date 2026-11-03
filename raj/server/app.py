"""Central server: portal API, enrollment, jobs. Run: ``raj-server serve`` (see README)."""
from __future__ import annotations

import base64
import json
import logging
import os
import re
import secrets
import subprocess
import time
from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .. import calculations as calc
from ..contracts import new_id
from ..pki import ServerPKI, fingerprint
from .auth import (can_access_client, check_password, current_user, hash_password, new_session,
                   require_role, sha)
from .bridge import Bridge, BridgeError
from .db import DB
from .harness import Harness, llm_from_env
from .service import Service

STATIC = Path(__file__).parent / "static"
ENROLL_TTL = 24 * 3600
MAX_UPLOAD = 10 * 1024 * 1024


class Settings:
    def __init__(self, data_dir: str | Path | None = None):
        self.data_dir = Path(data_dir or os.environ.get("RAJ_DATA_DIR", "./var/server"))
        self.ssh_host = os.environ.get("RAJ_SSH_HOST", "")
        self.ssh_port = int(os.environ.get("RAJ_SSH_PORT", "22"))
        self.ssh_user = os.environ.get("RAJ_SSH_USER", "rajtunnel")
        self.ssh_known_hosts = os.environ.get("RAJ_SSH_KNOWN_HOSTS", "")
        self.tunnel_port_base = int(os.environ.get("RAJ_TUNNEL_PORT_BASE", "20000"))
        self.allow_direct = os.environ.get("RAJ_ALLOW_DIRECT_GATEWAY", "0") == "1"  # dev/test only
        self.kill_cmd = os.environ.get("RAJ_SSH_KILL_CMD", "")  # e.g. "sudo /opt/raj/infra/ssh/raj-kill-tunnel {port}"


class LoginIn(BaseModel):
    email: str
    password: str


class ClientIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)


class UserIn(BaseModel):
    email: str
    name: str
    password: str = Field(min_length=10)
    role: str
    client_ids: list[str] = []


class EnrollIn(BaseModel):
    code: str
    hostname: str = ""
    company: dict
    csr_pem: str
    ssh_pubkey: str = ""
    direct_url: str | None = None


class AskIn(BaseModel):
    question: str = Field(min_length=2, max_length=2000)
    as_of: str | None = None


class RowEdit(BaseModel):
    ledger: str | None = None
    include: bool | None = None


class ApproveIn(BaseModel):
    version: int
    row_ids: list[str]


def create_app(settings: Settings | None = None, llm=None) -> FastAPI:
    s = settings or Settings()
    s.data_dir.mkdir(parents=True, exist_ok=True)
    db = DB(s.data_dir / "raj.sqlite3")
    pki = ServerPKI(s.data_dir / "pki")
    bridge = Bridge(db, device_ca_path=pki.device_ca_path, cert_path=pki.bridge_cert_path, key_path=pki.bridge_key_path)
    harness = Harness(db, bridge, pki.signing_key, llm if llm is not None else llm_from_env())
    svc = Service(db, bridge, pki, harness, s.data_dir)
    user_dep = current_user(db)

    app = FastAPI(title="RAJ Finance Agent", docs_url="/api/docs", openapi_url="/api/openapi.json")
    app.state.db, app.state.pki, app.state.bridge, app.state.svc, app.state.settings = db, pki, bridge, svc, s

    # ------------------------------------------------------------ auth
    @app.post("/api/login")
    def login(body: LoginIn):
        u = db.one("SELECT * FROM users WHERE email=? AND active=1", (body.email.lower(),))
        if not u or not check_password(body.password, u["pw_hash"]):
            time.sleep(0.3)
            raise HTTPException(401, "invalid email or password")
        return {"token": new_session(db, u["id"]), "user": {k: u[k] for k in ("id", "email", "name", "role")}}

    @app.post("/api/logout")
    def logout(request: Request, user=Depends(user_dep)):
        db.x("DELETE FROM sessions WHERE token_hash=?", (sha(request.headers["authorization"][7:]),))
        return {"ok": True}

    @app.get("/api/me")
    def me(user=Depends(user_dep)):
        org = db.one("SELECT * FROM orgs WHERE id=?", (user["org_id"],))
        return {"user": user, "org": {"id": org["id"], "name": org["name"], "settings": json.loads(org["settings"])},
                "model": "deepseek" if harness.llm else "rules (no DEEPSEEK_API_KEY)"}

    # ------------------------------------------------------------ admin
    @app.get("/api/clients")
    def clients(user=Depends(user_dep)):
        if user["role"] == "admin":
            rows = db.all("SELECT * FROM clients WHERE org_id=? ORDER BY name", (user["org_id"],))
        else:
            rows = db.all("SELECT c.* FROM clients c JOIN client_access a ON a.client_id=c.id WHERE a.user_id=? ORDER BY c.name",
                          (user["id"],))
        for c in rows:
            d = db.one("SELECT id, company_name, books_from, status, write_enabled, last_seen, last_health FROM devices "
                       "WHERE client_id=? AND status='active' ORDER BY created_at DESC LIMIT 1", (c["id"],))
            if d and d["last_health"]:
                d["last_health"] = json.loads(d["last_health"])
            c["device"] = d
        return rows

    @app.post("/api/clients")
    def create_client(body: ClientIn, user=Depends(user_dep)):
        require_role(user, "admin")
        cid = new_id()
        db.x("INSERT INTO clients VALUES (?,?,?,?)", (cid, user["org_id"], body.name.strip(), time.time()))
        return {"id": cid, "name": body.name.strip()}

    @app.get("/api/users")
    def users(user=Depends(user_dep)):
        require_role(user, "admin")
        out = db.all("SELECT id, email, name, role, active FROM users WHERE org_id=? ORDER BY email", (user["org_id"],))
        for u in out:
            u["client_ids"] = [r["client_id"] for r in db.all("SELECT client_id FROM client_access WHERE user_id=?", (u["id"],))]
        return out

    @app.post("/api/users")
    def create_user(body: UserIn, user=Depends(user_dep)):
        require_role(user, "admin")
        if body.role not in ("admin", "preparer", "reviewer", "viewer"):
            raise HTTPException(422, "bad role")
        if db.one("SELECT 1 FROM users WHERE email=?", (body.email.lower(),)):
            raise HTTPException(409, "email already exists")
        uid = new_id()
        db.x("INSERT INTO users VALUES (?,?,?,?,?,?,?,?)", (uid, user["org_id"], body.email.lower(), body.name,
                                                           hash_password(body.password), body.role, 1, time.time()))
        for cid in body.client_ids:
            can_access_client(db, user, cid)
            db.x("INSERT OR IGNORE INTO client_access VALUES (?,?)", (uid, cid))
        return {"id": uid}

    @app.patch("/api/org/settings")
    def org_settings(body: dict, user=Depends(user_dep)):
        require_role(user, "admin")
        allowed = {"maker_checker", "high_value_threshold"}
        cur = json.loads(db.one("SELECT settings FROM orgs WHERE id=?", (user["org_id"],))["settings"])
        cur.update({k: v for k, v in body.items() if k in allowed})
        db.x("UPDATE orgs SET settings=? WHERE id=?", (json.dumps(cur), user["org_id"]))
        return cur

    # ------------------------------------------------------------ enrollment
    @app.post("/api/clients/{client_id}/enrollment")
    def enrollment_code(client_id: str, user=Depends(user_dep)):
        require_role(user, "admin")
        can_access_client(db, user, client_id)
        raw = base64.b32encode(secrets.token_bytes(10)).decode().rstrip("=")
        code = f"RAJ-{raw[:4]}-{raw[4:8]}-{raw[8:12]}-{raw[12:16]}"
        db.x("INSERT INTO enrollments VALUES (?,?,?,?,?,?,?)",
             (sha(code), user["org_id"], client_id, user["id"], time.time() + ENROLL_TTL, None, None))
        return {"code": code, "expires_in_hours": ENROLL_TTL // 3600}

    @app.post("/api/enroll")
    def enroll(body: EnrollIn):
        if not body.company.get("guid") or not body.company.get("name"):
            raise HTTPException(422, "company name and guid required")
        if body.ssh_pubkey and not re.fullmatch(r"(ssh-ed25519|ecdsa-sha2-nistp256) [A-Za-z0-9+/=]+( [\w@.\-]+)?", body.ssh_pubkey.strip()):
            raise HTTPException(422, "unsupported SSH public key")
        with db.tx() as t:
            e = t.conn.execute("SELECT * FROM enrollments WHERE code_hash=?", (sha(body.code.strip().upper()),)).fetchone()
            if not e or e["used_at"] or e["expires_at"] < time.time():
                raise HTTPException(403, "enrollment code invalid, used or expired")
            device_id = new_id()
            try:
                cert = pki.issue_device_cert(device_id, body.csr_pem.encode())
            except Exception as ex:  # noqa: BLE001
                raise HTTPException(422, f"bad CSR: {ex}") from ex
            comp = body.company
            port = s.tunnel_port_base + (t.one("SELECT COUNT(*) n FROM devices")["n"] or 0) + 1
            while t.one("SELECT 1 FROM devices WHERE tunnel_port=?", (port,)):
                port += 1
            if not 1024 <= port <= 65535:
                raise HTTPException(503, "no connector tunnel port available")
            direct = body.direct_url if (s.allow_direct and body.direct_url) else None
            old_devices = t.all("SELECT id, tunnel_port FROM devices WHERE client_id=? AND status='active'", (e["client_id"],))
            for old in old_devices:
                _retire_device(old)
            t.x("INSERT INTO devices (id, org_id, client_id, hostname, company_name, company_guid, books_from, ssh_pubkey, "
                "tunnel_port, gateway_url, cert_fingerprint, write_enabled, status, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (device_id, e["org_id"], e["client_id"], body.hostname[:100], comp["name"], comp["guid"], comp.get("books_from"),
                 body.ssh_pubkey.strip(), port, direct, fingerprint(cert), 0, "active", time.time()))
            # Certificate, device/port allocation and single-use redemption commit together.
            t.conn.execute("UPDATE enrollments SET used_at=?, device_id=? WHERE code_hash=?", (time.time(), device_id, e["code_hash"]))
        for old in old_devices:
            _kill_tunnel(old)
        return {"device_id": device_id, "org_id": e["org_id"], "client_id": e["client_id"],
                "device_cert_pem": cert.decode(), "bridge_ca_pem": pki.bridge_ca_pem,
                "server_signing_pub": pki.signing_pub_pem,
                "ssh": {"host": s.ssh_host, "port": s.ssh_port, "user": s.ssh_user, "remote_port": port,
                        "known_hosts": s.ssh_known_hosts} if s.ssh_host else {}}

    def _retire_device(dev: dict) -> None:
        db.x("UPDATE devices SET status='revoked' WHERE id=?", (dev["id"],))
        for j in db.all("SELECT id FROM jobs WHERE device_id=?", (dev["id"],)):
            db.x("UPDATE approvals SET revoked=1 WHERE job_id=? AND used_at IS NULL", (j["id"],))
    def _kill_tunnel(dev: dict) -> None:
        if s.kill_cmd and dev.get("tunnel_port"):
            try:
                subprocess.run(s.kill_cmd.format(port=int(dev["tunnel_port"])).split(), check=True, timeout=20)
            except (OSError, subprocess.SubprocessError):
                # Revocation remains committed; cleanup failure must not burn a new pairing response.
                logging.getLogger(__name__).exception("SSH cleanup failed for revoked device %s", dev["id"])

    def _revoke(dev: dict) -> None:
        with db.tx():
            _retire_device(dev)
        _kill_tunnel(dev)

    @app.get("/api/clients/{client_id}/devices")
    def devices(client_id: str, user=Depends(user_dep)):
        can_access_client(db, user, client_id)
        return db.all("SELECT id, hostname, company_name, company_guid, books_from, tunnel_port, write_enabled, status, "
                      "created_at, last_seen FROM devices WHERE client_id=? ORDER BY created_at DESC", (client_id,))

    def _device(device_id: str, user: dict) -> dict:
        d = db.one("SELECT * FROM devices WHERE id=? AND org_id=?", (device_id, user["org_id"]))
        if not d:
            raise HTTPException(404, "device not found")
        can_access_client(db, user, d["client_id"])
        return d

    @app.get("/api/devices/{device_id}/health")
    def device_health(device_id: str, user=Depends(user_dep)):
        d = _device(device_id, user)
        status = {"pc_online": False, "ssh_connected": False, "gateway": "unknown", "tally": "unknown", "company": "unknown"}
        try:
            h = bridge.call(d["id"], "GET", "/health")
            status.update(pc_online=True, ssh_connected=True, gateway="ok", tally=h["tally"], company=h["company"],
                          paused=h["paused"], unresolved_writes=h["unresolved_writes"],
                          write_enabled_local=h["write_enabled_local"], open_companies=h.get("open_companies"))
            db.x("UPDATE devices SET last_seen=? WHERE id=?", (time.time(), d["id"]))
        except BridgeError as e:
            status["error"] = e.detail
            if e.status == 495:
                status.update(ssh_connected=True, gateway="IDENTITY MISMATCH")
        status["write_enabled_server"] = bool(d["write_enabled"])
        db.x("UPDATE devices SET last_health=? WHERE id=?", (json.dumps(status), d["id"]))
        return status

    @app.post("/api/devices/{device_id}/writes")
    def device_writes(device_id: str, body: dict, user=Depends(user_dep)):
        require_role(user, "admin")
        d = _device(device_id, user)
        db.x("UPDATE devices SET write_enabled=? WHERE id=?", (1 if body.get("enabled") else 0, d["id"]))
        return {"write_enabled": bool(body.get("enabled"))}

    @app.post("/api/devices/{device_id}/revoke")
    def device_revoke(device_id: str, user=Depends(user_dep)):
        require_role(user, "admin")
        _revoke(_device(device_id, user))
        return {"status": "revoked"}

    # ------------------------------------------------------------ work
    @app.post("/api/clients/{client_id}/ask")
    def ask(client_id: str, body: AskIn, user=Depends(user_dep)):
        c = can_access_client(db, user, client_id)
        return svc.ask(user, c, body.question, body.as_of)

    @app.post("/api/clients/{client_id}/statement/preview")
    async def preview(client_id: str, file: UploadFile = File(...), mapping: str | None = Form(None),
                      date_format: str | None = Form(None), user=Depends(user_dep)):
        can_access_client(db, user, client_id)
        content = await _read_upload(file)
        st = calc.parse_statement(content, file.filename or "upload.csv", json.loads(mapping) if mapping else None, date_format)
        return {"headers": st.headers, "mapping": st.mapping, "date_format": st.date_format, "rows": st.rows[:200],
                "row_count": len(st.rows), "warnings": st.warnings, "errors": st.errors,
                "needs_confirmation": st.needs_confirmation, "opening_balance": st.opening_balance,
                "closing_balance": st.closing_balance}

    @app.post("/api/clients/{client_id}/reconcile")
    async def reconcile(client_id: str, file: UploadFile = File(...), bank_ledger: str = Form(...),
                        mapping: str = Form(...), date_format: str = Form(...), user=Depends(user_dep)):
        """mapping/date_format are required: the user must confirm the column interpretation."""
        c = can_access_client(db, user, client_id)
        require_role(user, "admin", "preparer", "reviewer")
        content = await _read_upload(file)
        return svc.reconcile(user, c, content, file.filename or "upload.csv", bank_ledger,
                             {k: int(v) for k, v in json.loads(mapping).items()}, date_format)

    @app.get("/api/clients/{client_id}/jobs")
    def jobs(client_id: str, user=Depends(user_dep)):
        can_access_client(db, user, client_id)
        return db.all("SELECT id, type, state, request, created_at, updated_at FROM jobs WHERE client_id=? "
                      "ORDER BY created_at DESC LIMIT 100", (client_id,))

    def _job(job_id: str, user: dict) -> dict:
        j = db.one("SELECT * FROM jobs WHERE id=? AND org_id=?", (job_id, user["org_id"]))
        if not j:
            raise HTTPException(404, "job not found")
        can_access_client(db, user, j["client_id"])
        return j

    @app.get("/api/jobs/{job_id}")
    def job(job_id: str, user=Depends(user_dep)):
        _job(job_id, user)
        return svc.job_view(job_id)

    @app.patch("/api/drafts/{draft_id}/rows/{row_id}")
    def edit_row(draft_id: str, row_id: str, body: RowEdit, user=Depends(user_dep)):
        require_role(user, "admin", "preparer", "reviewer")
        d = db.one("SELECT job_id FROM drafts WHERE id=?", (draft_id,))
        if not d:
            raise HTTPException(404, "draft not found")
        _job(d["job_id"], user)
        return svc.edit_row(user, draft_id, row_id, body.ledger, body.include)

    @app.post("/api/drafts/{draft_id}/approve")
    def approve(draft_id: str, body: ApproveIn, user=Depends(user_dep)):
        d = db.one("SELECT job_id FROM drafts WHERE id=?", (draft_id,))
        if not d:
            raise HTTPException(404, "draft not found")
        _job(d["job_id"], user)
        return svc.approve(user, draft_id, body.version, body.row_ids)

    @app.post("/api/jobs/{job_id}/post")
    def post(job_id: str, user=Depends(user_dep)):
        _job(job_id, user)
        return svc.post(user, job_id)

    @app.post("/api/jobs/{job_id}/resolve")
    def resolve(job_id: str, user=Depends(user_dep)):
        _job(job_id, user)
        require_role(user, "admin", "reviewer")
        return svc.resolve(user, job_id)

    @app.get("/api/jobs/{job_id}/evidence")
    def evidence(job_id: str, user=Depends(user_dep)):
        _job(job_id, user)
        v = svc.job_view(job_id)
        v["approvals"] = db.all("SELECT id, row_id, draft_version, payload_sha256, reviewer_id, created_at, used_at, revoked "
                                "FROM approvals WHERE job_id=?", (job_id,))
        v["attempts"] = db.all("SELECT * FROM execution_attempts WHERE job_id=?", (job_id,))
        v["sources"] = db.all("SELECT filename, sha256, created_at FROM sources WHERE job_id=?", (job_id,))
        return JSONResponse(v, headers={"Content-Disposition": f'attachment; filename="raj-evidence-{job_id}.json"'})

    async def _read_upload(f: UploadFile) -> bytes:
        data = await f.read(MAX_UPLOAD + 1)
        if len(data) > MAX_UPLOAD:
            raise HTTPException(413, "file too large (10 MB max)")
        name = (f.filename or "").lower()
        if not name.endswith((".csv", ".xlsx")):
            raise HTTPException(415, "only CSV or XLSX statements are supported in this release")
        return data

    # ------------------------------------------------------------ portal
    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    @app.get("/")
    def index():
        return FileResponse(STATIC / "index.html")

    return app


def init_org(data_dir: str | Path, org_name: str, email: str, password: str, name: str = "Administrator") -> dict:
    db = DB(Path(data_dir) / "raj.sqlite3")
    if db.one("SELECT 1 FROM users WHERE email=?", (email.lower(),)):
        raise SystemExit("user already exists")
    org_id = new_id()
    db.x("INSERT INTO orgs VALUES (?,?,?)", (org_id, org_name, json.dumps({"maker_checker": False})))
    uid = new_id()
    db.x("INSERT INTO users VALUES (?,?,?,?,?,?,?,?)", (uid, org_id, email.lower(), name, hash_password(password),
                                                       "admin", 1, time.time()))
    return {"org_id": org_id, "admin_id": uid}


def main() -> None:
    import argparse

    import uvicorn
    p = argparse.ArgumentParser(prog="raj-server")
    sub = p.add_subparsers(dest="cmd", required=True)
    i = sub.add_parser("init", help="create firm organisation and first admin")
    i.add_argument("--org", required=True)
    i.add_argument("--email", required=True)
    i.add_argument("--password", required=True)
    i.add_argument("--data", default=os.environ.get("RAJ_DATA_DIR", "./var/server"))
    sv = sub.add_parser("serve")
    sv.add_argument("--host", default="127.0.0.1")
    sv.add_argument("--port", type=int, default=8080)
    sv.add_argument("--data", default=os.environ.get("RAJ_DATA_DIR", "./var/server"))
    a = p.parse_args()
    if a.cmd == "init":
        Path(a.data).mkdir(parents=True, exist_ok=True)
        print(json.dumps(init_org(a.data, a.org, a.email, a.password)))
    else:
        uvicorn.run(create_app(Settings(a.data)), host=a.host, port=a.port, proxy_headers=True)


if __name__ == "__main__":
    main()
