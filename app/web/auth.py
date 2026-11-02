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
_fails: dict[str, list[float]] = {}


async def ensure_admin() -> None:
    """Create the admin account on first run (password from ADMIN_PASSWORD or generated + logged once)."""
    if await db.get("settings/admin"):
        return
    pw = cfg.admin_password or secrets.token_urlsafe(9)
    h, salt = hash_pw(pw)
    await db.put("settings/admin", {"username": cfg.admin_username, "hash": h, "salt": salt, "ver": 1})
    if not cfg.admin_password:
        log.warning("=" * 60 + "\nADMIN PANEL LOGIN -> username: %s  password: %s\n(change it in Settings)\n" + "=" * 60,
                    cfg.admin_username, pw)


def client_ip(r: Request) -> str:
    return (r.headers.get("x-forwarded-for") or (r.client.host if r.client else "?")).split(",")[0].strip()


def locked(ip: str) -> bool:
    now = time.time()
    _fails[ip] = [t for t in _fails.get(ip, []) if now - t < 900]
    return len(_fails[ip]) >= 5


def record_fail(ip: str) -> None:
    _fails.setdefault(ip, []).append(time.time())


async def login(username: str, password: str, ip: str) -> str:
    if locked(ip):
        raise HTTPException(429, "Too many attempts. Try again in 15 minutes.")
    a = await db.get("settings/admin") or {}
    ok = a and username == a.get("username") and check_pw(password, a["hash"], a["salt"])
    if not ok:
        record_fail(ip)
        await db.push("logs/admin", {"ts": int(time.time()), "uid": None, "action": "login_failed", "detail": f"{username} from {ip}"})
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
