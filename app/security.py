from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time

from cryptography.fernet import Fernet

from .config import cfg

_f: Fernet | None = None


def _fernet() -> Fernet:
    global _f
    if _f is None:
        _f = Fernet(base64.urlsafe_b64encode(hashlib.sha256(("sess:" + cfg.session_key).encode()).digest()))
    return _f


def enc(s: str) -> str:
    return _fernet().encrypt(s.encode()).decode()


def dec(t: str) -> str:
    return _fernet().decrypt(t.encode()).decode()


def hash_pw(pw: str, salt: bytes | None = None) -> tuple[str, str]:
    salt = salt or os.urandom(16)
    return hashlib.scrypt(pw.encode(), salt=salt, n=2 ** 14, r=8, p=1, dklen=32).hex(), salt.hex()


def check_pw(pw: str, h: str, salt: str) -> bool:
    return hmac.compare_digest(hash_pw(pw, bytes.fromhex(salt))[0], h)


def sign(data: dict, ttl: int) -> str:
    body = base64.urlsafe_b64encode(json.dumps({**data, "exp": int(time.time()) + ttl}).encode()).decode().rstrip("=")
    sig = hmac.new(cfg.secret_key.encode(), body.encode(), hashlib.sha256).hexdigest()
    return f"{body}.{sig}"


def unsign(tok: str | None) -> dict | None:
    try:
        body, sig = (tok or "").split(".", 1)
        good = hmac.new(cfg.secret_key.encode(), body.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, good):
            return None
        data = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
        return data if data.get("exp", 0) > time.time() else None
    except Exception:  # noqa: BLE001
        return None
