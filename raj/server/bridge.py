"""Connector broker + MCP bridge.

The broker resolves a job to exactly one device route (loopback tunnel port, or an explicit
gateway URL in development). The bridge opens mTLS to that route, verifies the gateway's
certificate against the device CA **and the expected device hostname**, then sends the request with
the job token. A wrong or forged tunnel endpoint fails the TLS handshake before any tool runs.
"""
from __future__ import annotations

import ssl
from typing import Any

import httpx

from ..pki import device_hostname
from .db import DB


class BridgeError(Exception):
    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status = status
        self.detail = detail


class Bridge:
    def __init__(self, db: DB, *, device_ca_path: str, cert_path: str, key_path: str, timeout: float = 60.0):
        self.db = db
        self.ctx = ssl.create_default_context(ssl.Purpose.SERVER_AUTH, cafile=device_ca_path)
        self.ctx.load_cert_chain(cert_path, key_path)
        self.ctx.check_hostname = True
        self.timeout = timeout

    def route(self, device_id: str) -> str:
        d = self.db.one("SELECT * FROM devices WHERE id=?", (device_id,))
        if not d or d["status"] != "active":
            raise BridgeError(409, "device is not active")
        if d["gateway_url"]:
            return d["gateway_url"]
        return f"https://127.0.0.1:{d['tunnel_port']}"

    def call(self, device_id: str, method: str, path: str, *, token: str | None = None,
             json: dict | None = None) -> Any:
        url = self.route(device_id) + path
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        try:
            with httpx.Client(verify=self.ctx, timeout=self.timeout, trust_env=False) as c:
                r = c.request(method, url, json=json, headers=headers,
                              extensions={"sni_hostname": device_hostname(device_id)})
        except httpx.ConnectError as e:
            if "CERTIFICATE" in str(e).upper() or "hostname" in str(e).lower():
                raise BridgeError(495, "gateway identity verification failed") from e
            raise BridgeError(503, "client connector is offline (no tunnel)") from e
        except httpx.TimeoutException as e:
            raise BridgeError(504, "client connector timed out") from e
        except (httpx.ReadError, httpx.WriteError, httpx.RemoteProtocolError) as e:
            # the request may have been delivered: callers must treat writes as RESULT_UNKNOWN
            raise BridgeError(504, f"connection to client connector lost: {type(e).__name__}") from e
        if r.status_code >= 400:
            try:
                detail = r.json().get("detail", r.text)
            except ValueError:
                detail = r.text
            raise BridgeError(r.status_code, str(detail))
        return r.json()
