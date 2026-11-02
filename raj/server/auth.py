from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import time

from fastapi import Header, HTTPException

SESSION_TTL = 12 * 3600


def hash_password(pw: str, salt: bytes | None = None) -> str:
    salt = salt or os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", pw.encode(), salt, 310_000)
    return f"pbkdf2${salt.hex()}${dk.hex()}"


def check_password(pw: str, stored: str) -> bool:
    try:
        _, salt, dk = stored.split("$")
    except ValueError:
        return False
    return hmac.compare_digest(hash_password(pw, bytes.fromhex(salt)), stored)


def sha(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


def new_session(db, user_id: str) -> str:
    tok = secrets.token_urlsafe(32)
    db.x("INSERT INTO sessions VALUES (?,?,?)", (sha(tok), user_id, time.time() + SESSION_TTL))
    return tok


def current_user(db):
    def dep(authorization: str | None = Header(None)) -> dict:
        if not authorization or not authorization.startswith("Bearer "):
            raise HTTPException(401, "login required")
        s = db.one("SELECT * FROM sessions WHERE token_hash=?", (sha(authorization[7:]),))
        if not s or s["expires_at"] < time.time():
            raise HTTPException(401, "session expired")
        u = db.one("SELECT * FROM users WHERE id=? AND active=1", (s["user_id"],))
        if not u:
            raise HTTPException(401, "user disabled")
        u.pop("pw_hash", None)
        return u
    return dep


def can_access_client(db, user: dict, client_id: str) -> dict:
    c = db.one("SELECT * FROM clients WHERE id=? AND org_id=?", (client_id, user["org_id"]))
    if not c:
        raise HTTPException(404, "client not found")
    if user["role"] != "admin" and not db.one("SELECT 1 FROM client_access WHERE user_id=? AND client_id=?",
                                              (user["id"], client_id)):
        raise HTTPException(403, "no access to this client")
    return c


def require_role(user: dict, *roles: str) -> None:
    if user["role"] not in roles:
        raise HTTPException(403, f"requires role: {', '.join(roles)}")
