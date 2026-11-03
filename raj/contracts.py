"""Shared contracts: canonical JSON, Ed25519 signed job tokens and execution envelopes.

Anything that is hashed or signed goes through ``canonical_json`` so that the
server, the bridge and the client gateway compute byte-identical input.
"""
from __future__ import annotations

import base64
import hashlib
import json
import secrets
import time
import uuid
from decimal import Decimal
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

JOB_STATES = (
    "RECEIVED", "VALIDATED", "WAITING_FOR_DEVICE", "READING", "DRAFTED",
    "WAITING_FOR_APPROVAL", "POSTING", "VERIFYING", "COMPLETED", "REJECTED",
    "CANCELLED_BEFORE_POSTING", "PARTIALLY_COMPLETED", "RESULT_UNKNOWN", "FAILED",
)

READ_TOOLS = (
    "company_info", "list_ledgers", "trial_balance", "ledger_vouchers",
    "bills_outstanding",
)
WRITE_TOOLS = ("create_voucher",)


class ContractError(Exception):
    """Raised when a token/envelope fails validation. Message is safe to show."""


def _default(o: Any) -> Any:
    if isinstance(o, Decimal):
        return str(o)
    raise TypeError(f"not serializable: {type(o).__name__}")


def canonical_json(obj: Any) -> bytes:
    """Deterministic JSON (sorted keys, no whitespace, UTF-8). Decimals as strings.

    Floats are rejected: money must be Decimal/str so hashes are stable.
    """
    def check(o: Any) -> None:
        if isinstance(o, float):
            raise ContractError("floats are not allowed in signed payloads")
        if isinstance(o, dict):
            for v in o.values():
                check(v)
        elif isinstance(o, (list, tuple)):
            for v in o:
                check(v)
    check(obj)
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      default=_default).encode("utf-8")


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def payload_hash(payload: dict) -> str:
    return sha256_hex(canonical_json(payload))


def b64u(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def b64u_dec(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def new_id() -> str:
    return str(uuid.uuid4())


def new_nonce() -> str:
    return secrets.token_urlsafe(18)


# ---------------------------------------------------------------- keys

def generate_signing_key() -> Ed25519PrivateKey:
    return Ed25519PrivateKey.generate()


def private_key_pem(key: Ed25519PrivateKey) -> bytes:
    return key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                             serialization.NoEncryption())


def public_key_pem(key: Ed25519PrivateKey | Ed25519PublicKey) -> bytes:
    pub = key.public_key() if isinstance(key, Ed25519PrivateKey) else key
    return pub.public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)


def load_private_key(pem: bytes) -> Ed25519PrivateKey:
    k = serialization.load_pem_private_key(pem, password=None)
    assert isinstance(k, Ed25519PrivateKey)
    return k


def load_public_key(pem: bytes) -> Ed25519PublicKey:
    k = serialization.load_pem_public_key(pem)
    assert isinstance(k, Ed25519PublicKey)
    return k


# ---------------------------------------------------------------- signed objects

def sign_object(key: Ed25519PrivateKey, kind: str, body: dict) -> str:
    """Compact signed token: b64u(canonical body) . b64u(sig). ``kind`` is bound in."""
    body = dict(body, kind=kind)
    raw = canonical_json(body)
    sig = key.sign(raw)
    return f"{b64u(raw)}.{b64u(sig)}"


def verify_object(pub: Ed25519PublicKey, kind: str, token: str, now: float | None = None) -> dict:
    try:
        raw_b64, sig_b64 = token.split(".")
        raw, sig = b64u_dec(raw_b64), b64u_dec(sig_b64)
    except Exception as e:  # noqa: BLE001
        raise ContractError("malformed token") from e
    try:
        pub.verify(sig, raw)
    except InvalidSignature as e:
        raise ContractError("bad signature") from e
    body = json.loads(raw)
    if body.get("kind") != kind:
        raise ContractError(f"expected {kind} token")
    now = time.time() if now is None else now
    if body.get("exp", 0) < now:
        raise ContractError("token expired")
    if body.get("iat", now) > now + 60:
        raise ContractError("token issued in the future")
    return body


def make_job_token(key: Ed25519PrivateKey, *, job_id: str, org_id: str, client_id: str,
                   device_id: str, company_guid: str, capability: str,
                   allowed_tools: list[str], ttl: int = 300) -> str:
    if capability not in ("read", "write"):
        raise ContractError("bad capability")
    now = int(time.time())
    return sign_object(key, "job_token", {
        "job_id": job_id, "org_id": org_id, "client_id": client_id, "device_id": device_id,
        "company_guid": company_guid, "capability": capability,
        "allowed_tools": sorted(allowed_tools), "iat": now, "exp": now + ttl, "nonce": new_nonce(),
    })


def make_execution_envelope(key: Ed25519PrivateKey, *, job_id: str, approval_id: str,
                            draft_id: str, draft_version: int, row_id: str, payload: dict,
                            company_guid: str, device_id: str, reviewer_id: str,
                            ttl: int = 900) -> str:
    now = int(time.time())
    return sign_object(key, "execution_envelope", {
        "job_id": job_id, "approval_id": approval_id, "draft_id": draft_id,
        "draft_version": draft_version, "row_id": row_id,
        "payload_sha256": payload_hash(payload), "company_guid": company_guid,
        "device_id": device_id, "operation": "create_voucher", "reviewer_id": reviewer_id,
        "iat": now, "exp": now + ttl, "nonce": new_nonce(),
    })
