import asyncio
import hmac
import logging
import secrets
import time

from fastapi import HTTPException, Request

from .. import db
from ..config import cfg
from ..security import check_pw, hash_pw, sign, unsign

log = logging.getLogger("auth")
COOKIE = "smm_token"
TTL = 7 * 24 * 3600
WINDOW = 900            # seconds a failed attempt counts for
MAX_PER_IP = 5          # failed logins per IP per window
MAX_GLOBAL = 40         # failed logins from everyone per window (distributed brute force)
_fails: dict[str, list[float]] = {}
_global_fails: list[float] = []


async def ensure_admin() -> None:
    """Create the admin account on first run (password from ADMIN_PASSWORD or generated + logged once)."""
    if await db.get("settings/admin"):
        return
    if cfg.admin_password and len(cfg.admin_password) < 8:
        log.warning("ADMIN_PASSWORD is shorter than 8 characters - choose a longer one")
    pw = cfg.admin_password or secrets.token_urlsafe(12)
    h, salt = await asyncio.to_thread(hash_pw, pw)
    await db.put("settings/admin", {"username": cfg.admin_username, "hash": h, "salt": salt, "ver": 1})
    if not cfg.admin_password:
        log.warning("=" * 60 + "\nADMIN PANEL LOGIN -> username: %s  password: %s\n(change it in Settings)\n" + "=" * 60,
                    cfg.admin_username, pw)


def client_ip(r: Request) -> str:
    """Best-effort real client IP that a caller cannot forge. The *left-most* X-Forwarded-For entry is
    attacker-controlled (they can send their own header), so it must never be used as the rate-limit key:
    prefer the edge-set CF-Connecting-IP / True-Client-IP, else the right-most XFF hop (appended by our proxy)."""
    for h in ("cf-connecting-ip", "true-client-ip"):
        v = r.headers.get(h)
        if v:
            return v.strip()[:64]
    xff = r.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[-1].strip()[:64]
    return r.client.host if r.client else "?"


def locked(ip: str) -> bool:
    now = time.time()
    _fails[ip] = [t for t in _fails.get(ip, []) if now - t < WINDOW]
    if not _fails[ip]:
        _fails.pop(ip, None)
    _global_fails[:] = [t for t in _global_fails if now - t < WINDOW]
    return len(_fails.get(ip, [])) >= MAX_PER_IP or len(_global_fails) >= MAX_GLOBAL


def record_fail(ip: str) -> None:
    if len(_fails) > 5000:  # bound memory under a distributed flood
        _fails.clear()
    _fails.setdefault(ip, []).append(time.time())
    _global_fails.append(time.time())


async def login(username: str, password: str, ip: str) -> str:
    if locked(ip):
        raise HTTPException(429, "Too many attempts. Try again in 15 minutes.")
    a = await db.get("settings/admin") or {}
    # always run the (slow) password hash and compare in constant time, so response time reveals nothing
    # about whether the username exists; scrypt runs in a thread so it never blocks the event loop
    pw_ok = await asyncio.to_thread(check_pw, password, a.get("hash", "00"), a.get("salt", "00")) if a else False
    user_ok = hmac.compare_digest(username.encode(), str(a.get("username", "")).encode())
    if not (a and pw_ok and user_ok):
        record_fail(ip)
        await db.push("logs/admin", {"ts": int(time.time()), "uid": None, "action": "login_failed",
                                     "detail": f"{username[:60]!r} from {ip}"})
        raise HTTPException(401, "Wrong username or password")
    _fails.pop(ip, None)
    await db.push("logs/admin", {"ts": int(time.time()), "uid": None, "action": "login", "detail": ip})
    return sign({"u": a["username"], "v": a["ver"]}, TTL)


async def require_admin(request: Request) -> dict:
    tok = unsign(request.cookies.get(COOKIE))
    if not tok:
        raise HTTPException(401, "Not logged in")
    a = await db.cached_get("settings/admin", 30) or {}
    if tok.get("v") != a.get("ver") or tok.get("u") != a.get("username"):
        raise HTTPException(401, "Session expired")
    if request.method not in ("GET", "HEAD") and request.headers.get("x-requested-with") != "smm":
        raise HTTPException(403, "Bad request origin")
    return tok
