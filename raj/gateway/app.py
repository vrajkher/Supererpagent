"""RAJ Local Gateway: the only thing reachable through the SSH reverse tunnel.

Runs on the client PC behind mTLS (client certs from the bridge CA only). Every request carries a
server-signed job token; writes additionally carry a signed execution envelope for the exact
payload. The model never talks to this process directly and cannot change its policy.
"""
from __future__ import annotations

import json
import threading
import time
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse

from .. import calculations as calc
from ..contracts import (MAX_VOUCHER_PERIOD_DAYS, READ_TOOLS, ContractError, load_public_key, payload_hash,
                         verify_object)
from ..tally.xml_client import TallyClient, TallyError, TallyResponseLost, TallyUnreachable
from .journal import Journal

MAX_PERIOD_DAYS = MAX_VOUCHER_PERIOD_DAYS
MAX_VOUCHERS = 5000


@dataclass
class GatewayConfig:
    device_id: str
    org_id: str
    client_id: str
    company_name: str
    company_guid: str
    server_signing_pub: str
    tally_url: str = "http://127.0.0.1:9000"
    listen_host: str = "127.0.0.1"
    listen_port: int = 8765
    write_enabled_local: bool = False     # local administrator ceiling (both this and server must allow)
    paused: bool = False
    data_dir: str = "."
    tls_cert: str = ""
    tls_key: str = ""
    bridge_ca: str = ""
    ssh: dict = field(default_factory=dict)
    server_url: str = ""

    @classmethod
    def load(cls, path: Path) -> "GatewayConfig":
        d = json.loads(Path(path).read_text("utf-8"))
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})

    def save(self, path: Path) -> None:
        Path(path).write_text(json.dumps(asdict(self), indent=2, ensure_ascii=False), "utf-8")


class Gateway:
    def __init__(self, cfg: GatewayConfig, tally: TallyClient | None = None, config_path: Path | None = None):
        self.cfg = cfg
        self.config_path = config_path
        self.tally = tally or TallyClient(cfg.tally_url)
        self.pub = load_public_key(cfg.server_signing_pub.encode())
        self.journal = Journal(Path(cfg.data_dir) / "journal.sqlite3")
        self.write_lock = threading.Lock()   # one writer per connector

    # ---------------------------------------------------------------- policy
    def _refresh(self) -> None:
        """Pick up local pause / write-ceiling changes made with the CLI without a restart."""
        if self.config_path and Path(self.config_path).exists():
            try:
                d = json.loads(Path(self.config_path).read_text("utf-8"))
                self.cfg.paused = bool(d.get("paused", self.cfg.paused))
                self.cfg.write_enabled_local = bool(d.get("write_enabled_local", self.cfg.write_enabled_local))
            except (OSError, ValueError):
                self.cfg.paused = True  # fail closed on an unreadable config

    def check_token(self, token: str, *, tool: str | None = None, capability: str = "read") -> dict:
        self._refresh()
        if self.cfg.paused:
            raise HTTPException(423, "connector paused by local administrator")
        try:
            t = verify_object(self.pub, "job_token", token)
        except ContractError as e:
            self.journal.audit("token_rejected", reason=str(e))
            raise HTTPException(401, f"job token rejected: {e}") from e
        for k, mine in (("device_id", self.cfg.device_id), ("client_id", self.cfg.client_id),
                        ("org_id", self.cfg.org_id), ("company_guid", self.cfg.company_guid)):
            if t.get(k) != mine:
                self.journal.audit("token_rejected", reason=f"{k} mismatch", job_id=t.get("job_id"))
                raise HTTPException(403, f"job token not valid for this connector ({k})")
        if capability == "write" and t["capability"] != "write":
            raise HTTPException(403, "job token is read-only")
        if tool and tool not in t["allowed_tools"]:
            raise HTTPException(403, f"tool not permitted for this job: {tool}")
        return t

    def check_company(self) -> dict:
        try:
            comps = self.tally.open_companies()
        except TallyUnreachable as e:
            raise HTTPException(503, "Tally is not running or not reachable") from e
        match = [c for c in comps if c["guid"] == self.cfg.company_guid]
        if not match:
            names = [c["name"] for c in comps]
            raise HTTPException(409, f"bound company is not open in Tally (open: {names})")
        if match[0]["name"] != self.cfg.company_name:
            raise HTTPException(409, "company GUID matches but name differs; re-confirm binding")
        return match[0]

    # ---------------------------------------------------------------- reads
    def run_tool(self, tool: str, args: dict) -> Any:
        if tool not in READ_TOOLS:
            raise HTTPException(400, f"unknown read tool {tool}")
        comp = self.check_company()
        name = self.cfg.company_name
        try:
            if tool == "company_info":
                return comp
            if tool in ("list_ledgers", "trial_balance"):
                led = self.tally.ledgers(name)
                s = (args.get("search") or "").lower()
                return [l for l in led if s in l["name"].lower()] if s else led
            if tool == "ledger_vouchers":
                fd, td = args.get("from_date"), args.get("to_date")
                try:
                    f, t = date.fromisoformat(fd), date.fromisoformat(td)
                except (TypeError, ValueError) as e:
                    raise HTTPException(400, "from_date/to_date must be ISO dates") from e
                if t < f or (t - f).days > MAX_PERIOD_DAYS:
                    raise HTTPException(400, f"period must be 0..{MAX_PERIOD_DAYS} days")
                vs = self.tally.vouchers(name, fd, td)
                ledger = args.get("ledger")
                if ledger:
                    vs = [v for v in vs if any(e["ledger"] == ledger for e in v["entries"])]
                if len(vs) > MAX_VOUCHERS:
                    raise HTTPException(413, "too many vouchers; narrow the period")
                return vs
            if tool == "bills_outstanding":
                return self.tally.bills(name)
        except TallyUnreachable as e:
            raise HTTPException(503, "Tally is not reachable") from e
        except TallyError as e:
            raise HTTPException(502, str(e)) from e
        raise HTTPException(400, "unsupported")

    # ---------------------------------------------------------------- writes
    def post_voucher(self, token: dict, envelope_raw: str, payload: dict) -> dict:
        try:
            env = verify_object(self.pub, "execution_envelope", envelope_raw)
        except ContractError as e:
            self.journal.audit("envelope_rejected", reason=str(e))
            raise HTTPException(401, f"approval rejected: {e}") from e
        if env["job_id"] != token["job_id"] or env["device_id"] != self.cfg.device_id \
                or env["company_guid"] != self.cfg.company_guid or env["operation"] != "create_voucher":
            raise HTTPException(403, "approval does not match this job/connector/company")
        if payload_hash(payload) != env["payload_sha256"]:
            self.journal.audit("envelope_rejected", reason="payload hash mismatch", job_id=env["job_id"])
            raise HTTPException(409, "payload differs from what was approved; re-approval required")
        if not self.cfg.write_enabled_local:
            raise HTTPException(403, "writes are disabled on this connector by the local administrator")

        with self.write_lock:
            if self.journal.nonce_used(env["nonce"]):
                raise HTTPException(409, "approval already used")
            done = self.journal.row_done(env["row_id"])
            if done:
                return {"state": "VERIFIED", "tally_id": done["tally_id"], "detail": "already posted (idempotent)"}
            blocking = self.journal.blocking()
            if blocking:
                raise HTTPException(423, f"{len(blocking)} earlier write(s) have unknown result; reconcile first")
            self.check_company()
            ledgers = {l["name"] for l in self.tally.ledgers(self.cfg.company_name)}
            errs = calc.validate_voucher(payload, ledgers)
            if errs:
                raise HTTPException(422, "; ".join(errs))
            # stop if a voucher with this reference already exists (e.g. posted outside RAJ)
            if self.tally.find_by_ref(self.cfg.company_name, payload["ref"], payload["date"]):
                raise HTTPException(409, "a voucher with this reference already exists in Tally; reconcile")

            nonce = env["nonce"]
            self.journal.intent(nonce=nonce, job_id=env["job_id"], row_id=env["row_id"], approval_id=env["approval_id"],
                                ref=payload["ref"], voucher_date=payload["date"], payload=payload,
                                payload_sha256=env["payload_sha256"])
            self.journal.set(nonce, "SENT")
            try:
                res = self.tally.create_voucher(self.cfg.company_name, payload)
            except TallyResponseLost as e:
                self.journal.set(nonce, "UNKNOWN", detail=f"response lost: {e}")
                return self.reconcile_one(nonce)
            except TallyUnreachable as e:
                # connection refused before sending: nothing reached Tally
                self.journal.set(nonce, "NOT_POSTED", detail="Tally unreachable before send")
                raise HTTPException(503, "Tally is not reachable") from e
            except TallyError as e:
                self.journal.set(nonce, "UNKNOWN", detail=str(e))
                return self.reconcile_one(nonce)
            if res["errors"] or not res["created"]:
                self.journal.set(nonce, "REJECTED", detail=res["error_text"] or "Tally did not create the voucher")
                return {"state": "REJECTED", "detail": res["error_text"]}
            return self._verify(nonce, payload)

    def _verify(self, nonce: str, payload: dict) -> dict:
        found = self.tally.find_by_ref(self.cfg.company_name, payload["ref"], payload["date"])
        if len(found) != 1:
            st = "UNKNOWN" if not found else "MISMATCH"
            self.journal.set(nonce, st, detail=f"read-back found {len(found)} vouchers")
            return {"state": st, "detail": f"read-back found {len(found)} vouchers with ref"}
        diffs = calc.voucher_matches(payload, found[0])
        if diffs:
            self.journal.set(nonce, "MISMATCH", tally_id=found[0]["voucher_id"], detail="; ".join(diffs))
            return {"state": "MISMATCH", "tally_id": found[0]["voucher_id"], "detail": diffs}
        self.journal.set(nonce, "VERIFIED", tally_id=found[0]["voucher_id"], detail="read-back matched")
        return {"state": "VERIFIED", "tally_id": found[0]["voucher_id"], "readback": found[0]}

    def reconcile_one(self, nonce: str) -> dict:
        j = self.journal.get(nonce)
        if not j:
            raise HTTPException(404, "unknown journal entry")
        if j["state"] not in ("UNKNOWN", "SENT", "INTENT"):
            return {"state": j["state"], "tally_id": j["tally_id"], "detail": j["detail"]}
        try:
            found = self.tally.find_by_ref(self.cfg.company_name, j["ref"], j["voucher_date"])
        except TallyError as e:
            return {"state": "UNKNOWN", "detail": f"cannot reach Tally to reconcile: {e}"}
        if not found:
            self.journal.set(nonce, "NOT_POSTED", detail="reconciled: no voucher with this reference in Tally")
            return {"state": "NOT_POSTED", "detail": "not in Tally; needs fresh approval to post"}
        return self._verify(nonce, j["payload"])

    def reconcile_all(self) -> list[dict]:
        out = []
        for j in self.journal.blocking():
            if j["state"] == "MISMATCH":
                out.append({"row_id": j["row_id"], "state": "MISMATCH", "detail": j["detail"]})
                continue
            out.append({"row_id": j["row_id"], **self.reconcile_one(j["nonce"])})
        return out

    def health(self) -> dict:
        h = {"gateway": "ok", "device_id": self.cfg.device_id, "paused": self.cfg.paused,
             "write_enabled_local": self.cfg.write_enabled_local, "tally": "unknown", "company": "unknown",
             "unresolved_writes": len(self.journal.blocking()), "time": time.time()}
        try:
            comps = self.tally.open_companies()
            h["tally"] = "running"
            m = [c for c in comps if c["guid"] == self.cfg.company_guid]
            h["company"] = "ready" if m and m[0]["name"] == self.cfg.company_name else "not open"
            h["open_companies"] = [c["name"] for c in comps]
        except TallyError:
            h["tally"] = "not reachable"
        return h


def create_app(gw: Gateway) -> FastAPI:
    app = FastAPI(title="RAJ Local Gateway", docs_url=None, redoc_url=None, openapi_url=None)

    def bearer(authorization: str | None) -> str:
        if not authorization or not authorization.startswith("Bearer "):
            raise HTTPException(401, "missing job token")
        return authorization[7:]

    @app.get("/health")
    def health():
        return gw.health()

    @app.post("/tools/call")
    async def call(req: Request, authorization: str | None = Header(None)):
        body = await req.json()
        tool, args = body.get("tool"), body.get("args") or {}
        t = gw.check_token(bearer(authorization), tool=tool)
        gw.journal.audit("tool_call", job_id=t["job_id"], tool=tool)
        return {"tool": tool, "company": gw.cfg.company_name, "extracted_at": time.time(),
                "result": gw.run_tool(tool, args)}

    @app.post("/tools/post_voucher")
    async def post(req: Request, authorization: str | None = Header(None)):
        body = await req.json()
        t = gw.check_token(bearer(authorization), tool="create_voucher", capability="write")
        return gw.post_voucher(t, body["envelope"], body["payload"])

    @app.post("/tools/reconcile")
    async def reconcile(authorization: str | None = Header(None)):
        gw.check_token(bearer(authorization), capability="read")
        return {"results": gw.reconcile_all()}

    @app.exception_handler(Exception)
    async def unhandled(_req, exc):  # never leak internals
        return JSONResponse({"detail": f"gateway error: {type(exc).__name__}"}, status_code=500)

    return app


def default_window(days: int = 30) -> tuple[str, str]:
    t = date.today()
    return (t - timedelta(days=days)).isoformat(), t.isoformat()
