import asyncio
import logging
import math
import re
from contextlib import suppress

from fastapi import APIRouter, Depends, File, HTTPException, Request, Response, UploadFile
from pydantic import BaseModel, Field

from .. import db, runtime
from ..config import cfg
from ..constants import (DEFAULT_CHANNEL_SETTINGS, DEFAULT_SAFETY, DEFAULT_SERVICE_CONFIG, PAGE_SIZE_ADMIN,
                         SERVICE_ORDER, SERVICES)
from ..roles import add_admin, is_owner, list_admins_all, remove_admin
from ..security import check_pw, hash_pw, sign
from ..services import store
from ..utils import fmt_days_left, now, parse_proxy, spawn
from . import auth

log = logging.getLogger("web")
router = APIRouter(prefix="/api")
admin_dep = [Depends(auth.require_admin)]


def page_of(items: list, page: int, size: int = PAGE_SIZE_ADMIN):
    pages = max(1, math.ceil(len(items) / size))
    page = min(max(1, page), pages)
    return items[(page - 1) * size: page * size], page, pages


def _log(action: str, detail: str = "", channel_id: str | None = None) -> None:
    store.log_bg("panel", cfg.owner_id, "owner", action, detail, channel_id)


# ───────────── input validation ─────────────
MAX_UPLOAD = 5 * 1024 * 1024
_CHANNEL_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

# service -> config key -> (type, min, max). Anything not listed is dropped, so the panel can never write
# arbitrary keys/values (huge numbers, nested junk, Firebase-illegal keys) into the engine's config.
SVC_FIELDS: dict[str, dict[str, tuple]] = {
    "subscribers": {"interval_seconds": (int, 1, 86400), "target_count": (int, 0, 10_000_000)},
    "views": {"qty": (int, 1, 200_000), "speed_seconds": (float, 0.5, 3600), "random_enabled": (bool,),
              "random_range": (int, 0, 200_000)},
    "reactions": {"qty": (int, 1, 200_000), "speed_seconds": (float, 0.5, 3600), "skip_poster": (bool,), "mix": (dict,)},
    "mute": {"daily_actions": (int, 1, 1000), "speed_seconds": (float, 0.5, 3600)},
    "poll": {"speed_seconds": (float, 0.5, 3600), "max_votes": (int, 0, 1_000_000)},
    "stream": {"max_members": (int, 1, 500), "join_speed": (float, 0.3, 60), "hand_rise_pct": (int, 0, 100),
               "paused": (bool,)},
}
SAFETY_LIMITS: dict[str, tuple] = {
    "actions_per_hour": (1, 1000), "joins_per_day": (0, 500), "cooldown_sec": (0, 86400), "max_clients": (1, 20),
    "batch_max": (1, 100), "jitter_min": (0, 3600), "jitter_max": (0, 3600), "stream_cap": (0, 500),
    "stream_max_minutes": (1, 1440), "stream_wait_minutes": (0, 120), "stream_concurrency": (1, 50),
}


def _bad(msg: str):
    raise HTTPException(400, msg)


def _num(name: str, v, typ, lo, hi):
    if isinstance(v, bool) or not isinstance(v, (int, float)) or v != v or v in (float("inf"), float("-inf")):
        _bad(f"{name} must be a number")
    if not (lo <= v <= hi):
        _bad(f"{name} must be between {lo:g} and {hi:g}")
    return int(round(v)) if typ is int else round(float(v), 2)


def _clean_mix(mix: dict) -> dict:
    if len(mix) > 40:
        _bad("Emoji mix has too many entries")
    out = {}
    for k, v in mix.items():
        if not isinstance(k, str) or not (1 <= len(k) <= 8) or re.search(r"[./$#\[\]\s]", k):
            _bad("Invalid emoji in mix")
        out[k] = _num(f"mix[{k}]", v, float, 0, 100)
    return out


def clean_service_config(svc: str, cfg_in: dict) -> dict:
    spec, out = SVC_FIELDS[svc], {}
    for k, v in (cfg_in or {}).items():
        t = spec.get(k)
        if not t:
            continue
        if t[0] is bool:
            if not isinstance(v, bool):
                _bad(f"{k} must be true/false")
            out[k] = v
        elif t[0] is dict:
            if not isinstance(v, dict):
                _bad(f"{k} must be an object")
            out[k] = _clean_mix(v)
        else:
            out[k] = _num(k, v, t[0], t[1], t[2])
    return out


async def _need_channel(cid: str) -> None:
    if not _CHANNEL_ID.match(cid) or not await store.channel_exists(cid):
        raise HTTPException(404, "Channel not found")


def _secure_cookie(request: Request) -> bool:
    return cfg.base_url.startswith("https") or request.url.scheme == "https" \
        or request.headers.get("x-forwarded-proto") == "https"


# ───────────── auth ─────────────
class LoginIn(BaseModel):
    username: str = Field(max_length=100)
    password: str = Field(max_length=256)


@router.post("/login")
async def login(body: LoginIn, request: Request, response: Response):
    tok = await auth.login(body.username.strip(), body.password, auth.client_ip(request))
    response.set_cookie(auth.COOKIE, tok, max_age=auth.TTL, httponly=True, samesite="strict",
                        secure=_secure_cookie(request), path="/")
    return {"ok": True}


@router.post("/logout")
async def logout(response: Response):
    response.delete_cookie(auth.COOKIE, path="/")
    return {"ok": True}


@router.get("/me", dependencies=admin_dep)
async def me(tok: dict = Depends(auth.require_admin)):
    return {"username": tok["u"], "tz": cfg.timezone, "owner_id": cfg.owner_id}


# ───────────── dashboard ─────────────
@router.get("/dashboard", dependencies=admin_dep)
async def dashboard():
    channels = await store.list_channels()
    all_svcs = await asyncio.gather(*(store.get_all_services(c["id"]) for c in channels)) if channels else []
    active_services = sum(1 for svcs in all_svcs for s in svcs.values() if s["enabled"])
    per_service = {s: sum(1 for svcs in all_svcs if svcs[s]["enabled"]) for s in SERVICE_ORDER}
    eng = runtime.engine.info() if runtime.engine else {"sessions": {}, "stream": {}, "watching_channels": 0}
    raw_logs = await db.query_last("logs/actions", 12) or {}
    recent = [v for _, v in sorted(raw_logs.items(), key=lambda kv: kv[0], reverse=True)]
    admins = await list_admins_all()
    return {"channels": {"total": len(channels), "active_services": active_services, "per_service": per_service},
            "engine": eng, "recent": recent, "admins": {"active": sum(1 for a in admins if not a.get("hidden")),
                                                         "total": len(admins)}}


# ───────────── channels ─────────────
def view_channel(c: dict, svcs: dict | None = None) -> dict:
    out = {"id": c["id"], "title": c.get("title"), "link": c.get("link"), "type": c.get("type"),
           "chat_id": c.get("chat_id"), "members": c.get("members"), "added_at": c.get("added_at"),
           "hidden": bool(c.get("hidden")), "expires_at": c.get("expires_at"), "rate_note": c.get("rate_note", ""),
           "days_left": fmt_days_left(c.get("expires_at"))}
    if svcs is not None:
        out["services"] = {s: {"enabled": svcs[s]["enabled"], "expires_at": svcs[s]["expires_at"],
                               "days_left": fmt_days_left(svcs[s]["expires_at"]) if svcs[s]["enabled"] else None}
                           for s in SERVICE_ORDER}
        out["active"] = any(svcs[s]["enabled"] for s in SERVICE_ORDER)
    return out


@router.get("/channels", dependencies=admin_dep)
async def channels(page: int = 1, include_hidden: bool = False, q: str = ""):
    items = await store.list_channels(include_hidden=include_hidden)
    if q.strip():
        ql = q.strip().lower()
        items = [c for c in items if ql in (c.get("title") or "").lower() or ql in str(c.get("chat_id") or "")]
    svcs_list = await asyncio.gather(*(store.get_all_services(c["id"]) for c in items)) if items else []
    views = [view_channel(c, s) for c, s in zip(items, svcs_list)]
    chunk, page, pages = page_of(views, page)
    return {"items": chunk, "page": page, "pages": pages, "total": len(views)}


@router.get("/channels/{cid}", dependencies=admin_dep)
async def channel_detail(cid: str):
    c = await store.get_channel(cid, include_hidden=True)
    if not c:
        raise HTTPException(404, "Channel not found")
    svcs = await store.get_all_services(cid)
    settings = await store.get_channel_settings(cid)
    return {"channel": view_channel(c, svcs), "services": svcs, "settings": settings}


class RenewIn(BaseModel):
    days: int = Field(ge=1, le=3650)


@router.post("/channels/{cid}/renew", dependencies=admin_dep)
async def renew_channel(cid: str, body: RenewIn):
    c = await store.get_channel(cid, include_hidden=True)
    if not c:
        raise HTTPException(404, "Channel not found")
    if body.days < 1 or body.days > 3650:
        raise HTTPException(400, "Days must be between 1 and 3650")
    base = c["expires_at"] if c.get("expires_at") and c["expires_at"] > now() else now()
    new_exp = base + body.days * 86400
    await store.update_channel(cid, {"expires_at": new_exp, "days_total": (c.get("days_total") or 0) + body.days})
    _log("channel_renewed", f"+{body.days}d", cid)
    return {"expires_at": new_exp}


@router.post("/channels/{cid}/hide", dependencies=admin_dep)
async def hide_channel(cid: str):
    if not await store.hide_channel(cid, cfg.owner_id):
        raise HTTPException(404, "Channel not found")
    _log("channel_removed", "", cid)
    return {"ok": True}


@router.post("/channels/{cid}/restore", dependencies=admin_dep)
async def restore_channel(cid: str):
    if not await store.unhide_channel(cid, cfg.owner_id):
        raise HTTPException(404, "Channel not found")
    _log("channel_restored", "", cid)
    return {"ok": True}


@router.post("/channels/{cid}/revoke_all", dependencies=admin_dep)
async def revoke_all(cid: str):
    await _need_channel(cid)
    done = await store.revoke_all_services(cid, cfg.owner_id)
    _log("revoke_all", ",".join(done), cid)
    return {"revoked": done}


# ───────────── services ─────────────
def _svc(svc: str) -> str:
    if svc not in SERVICE_ORDER:
        raise HTTPException(404, "Unknown service")
    return svc


class EnableIn(BaseModel):
    days: int = Field(ge=1, le=3650)
    rate_note: str = Field(default="", max_length=200)
    config: dict = {}


@router.post("/channels/{cid}/services/{svc}/enable", dependencies=admin_dep)
async def enable_service(cid: str, svc: str, body: EnableIn):
    _svc(svc)
    await _need_channel(cid)
    await store.enable_service(cid, svc, body.days, clean_service_config(svc, body.config), body.rate_note.strip()[:200],
                               cfg.owner_id)
    _log("service_enabled", f"{svc} {body.days}d", cid)
    return {"ok": True}


class ConfigIn(BaseModel):
    config: dict


@router.put("/channels/{cid}/services/{svc}/config", dependencies=admin_dep)
async def set_service_config(cid: str, svc: str, body: ConfigIn):
    _svc(svc)
    await _need_channel(cid)
    patch = clean_service_config(svc, body.config)
    await store.update_service_config(cid, svc, patch)
    _log("config_set", f"{svc} {patch}", cid)
    return {"ok": True}


class ExtendIn(BaseModel):
    days: int = Field(ge=1, le=3650)


@router.post("/channels/{cid}/services/{svc}/extend", dependencies=admin_dep)
async def extend_service(cid: str, svc: str, body: ExtendIn):
    _svc(svc)
    await _need_channel(cid)
    new_exp = await store.extend_service(cid, svc, body.days, cfg.owner_id)
    _log("service_extended", f"{svc} +{body.days}d", cid)
    return {"expires_at": new_exp}


@router.post("/channels/{cid}/services/{svc}/revoke", dependencies=admin_dep)
async def revoke_service(cid: str, svc: str):
    _svc(svc)
    await _need_channel(cid)
    await store.revoke_service(cid, svc, cfg.owner_id)
    _log("service_revoked", svc, cid)
    return {"ok": True}


class ChannelSettingsIn(BaseModel):
    delay_seconds: int | None = None
    night_mode: dict | None = None
    wave_mode: dict | None = None


@router.put("/channels/{cid}/settings", dependencies=admin_dep)
async def set_channel_settings(cid: str, body: ChannelSettingsIn):
    await _need_channel(cid)
    cur = await store.get_channel_settings(cid)
    patch: dict = {}
    if body.delay_seconds is not None:
        patch["delay_seconds"] = _num("delay_seconds", body.delay_seconds, int, 0, 21600)
    if body.night_mode is not None:
        nm = body.night_mode
        patch["night_mode"] = {**cur["night_mode"],
                               "enabled": bool(nm.get("enabled", cur["night_mode"]["enabled"])),
                               "start_hour": _num("start_hour", nm.get("start_hour", cur["night_mode"]["start_hour"]), int, 0, 23),
                               "end_hour": _num("end_hour", nm.get("end_hour", cur["night_mode"]["end_hour"]), int, 0, 23),
                               "multiplier": _num("multiplier", nm.get("multiplier", cur["night_mode"]["multiplier"]), float, 1, 10)}
    if body.wave_mode is not None:
        patch["wave_mode"] = {**cur["wave_mode"], "enabled": bool(body.wave_mode.get("enabled", False))}
    if not patch:
        return {"ok": True}
    await store.update_channel_settings(cid, patch)
    _log("channel_settings", str(patch), cid)
    return {"ok": True}


# ───────────── sessions ─────────────
def _eng():
    if not runtime.engine:
        raise HTTPException(503, "Engine not ready")
    return runtime.engine.sessions


def view_session(m: dict) -> dict:
    t = now()
    st = m.get("status", "new")
    if st == "active" and m.get("flood_until", 0) > t:
        st = "flood"
    return {"id": m["id"], "label": m.get("label", ""), "phone": m.get("phone", ""), "username": m.get("username", ""),
            "status": st, "api_id": m.get("api_id"), "added_at": m.get("added_at"), "last_check": m.get("last_check"),
            "note": m.get("note", ""), "proxy": bool(m.get("proxy")), "flood_until": m.get("flood_until", 0)}


@router.get("/sessions", dependencies=admin_dep)
async def sessions(page: int = 1, status: str = ""):
    s = _eng()
    rows = [view_session(m) for m in s.meta.values()]
    rows.sort(key=lambda r: r["added_at"] or 0, reverse=True)
    if status:
        rows = [r for r in rows if r["status"] == status]
    chunk, page, pages = page_of(rows, page)
    return {"items": chunk, "counts": s.counts(), "page": page, "pages": pages, "total": len(rows), "validation": s.validation}


@router.post("/sessions/upload", dependencies=admin_dep)
async def upload_session(file: UploadFile = File(...)):
    raw = await file.read(MAX_UPLOAD + 1)  # bounded read: never pull an arbitrarily large body into RAM
    if len(raw) > MAX_UPLOAD:
        return {"ok": False, "error": "File too large (max 5 MB)"}
    name = (file.filename or "session").replace("\n", " ").replace("\r", " ")[:80]
    r = await _eng().import_file(name, raw)
    _log("session_import", f"{name}: {'ok' if r['ok'] else r['error']}")
    return r


class StringsIn(BaseModel):
    strings: list[str] = Field(max_length=500)


@router.post("/sessions/string", dependencies=admin_dep)
async def add_strings(body: StringsIn):
    todo = [x.strip() for x in body.strings if x.strip() and len(x) < 2000][:200]  # cap BEFORE connecting
    out = [await _eng().import_string(s) for s in todo]
    _log("session_import_string", f"{sum(1 for o in out if o['ok'])}/{len(out)} ok")
    return {"results": out}


class IdsIn(BaseModel):
    ids: list[str] = Field(default=[], max_length=5000)


@router.post("/sessions/validate", dependencies=admin_dep)
async def validate_sessions(body: IdsIn):
    s = _eng()
    if s.validation["running"]:
        raise HTTPException(409, "A validation run is already in progress")
    ids = [i for i in (body.ids or list(s.meta)) if i in s.meta]
    spawn(s.validate_many(ids))
    _log("session_validate", f"{len(ids)} sessions")
    return {"started": len(ids)}


@router.post("/sessions/delete", dependencies=admin_dep)
async def delete_sessions(body: IdsIn):
    s = _eng()
    n = sum([1 for sid in body.ids if await s.delete(sid, cfg.owner_id)])
    _log("session_delete", f"{n} sessions (soft)")
    return {"deleted": n}


class ProxyIn(BaseModel):
    proxy: str = Field(default="", max_length=300)


@router.put("/sessions/{sid}/proxy", dependencies=admin_dep)
async def session_proxy(sid: str, body: ProxyIn):
    s = _eng()
    if sid not in s.meta:
        raise HTTPException(404)
    p = body.proxy.strip()
    if p and not parse_proxy(p):
        raise HTTPException(400, "Proxy must look like socks5://user:pass@host:port (or socks4:// / http://)")
    s.mark(sid, proxy=p or None)
    if not p:
        await db.delete(f"sessions/{sid}/proxy")
    _log("session_proxy", sid)
    return {"ok": True}


# ───────────── admins ─────────────
@router.get("/admins", dependencies=admin_dep)
async def admins_list():
    return {"owner_id": cfg.owner_id, "items": await list_admins_all()}


class AddAdminIn(BaseModel):
    id: int = Field(gt=0, lt=10**14)
    name: str = Field(default="", max_length=100)
    username: str = Field(default="", max_length=64)


@router.post("/admins", dependencies=admin_dep)
async def admins_add(body: AddAdminIn):
    if is_owner(body.id):
        raise HTTPException(400, "That's already the owner")
    ok = await add_admin(body.id, body.name.strip(), body.username.strip().lstrip("@"), cfg.owner_id)
    if not ok:
        raise HTTPException(409, "Already has access")
    _log("admin_added", str(body.id))
    if runtime.bot:
        with suppress(Exception):
            await runtime.bot.send_message(body.id, "👑 You've been granted access to the Telegram SMM control bot. Send /start.")
    return {"ok": True}


@router.post("/admins/{uid}/remove", dependencies=admin_dep)
async def admins_remove(uid: int):
    if not await remove_admin(uid, cfg.owner_id):
        raise HTTPException(404, "Not found / already removed")
    _log("admin_removed", str(uid))
    return {"ok": True}


# ───────────── logs (never trimmed) ─────────────
@router.get("/logs", dependencies=admin_dep)
async def logs(page: int = 1, source: str = "", action: str = "", channel_id: str = ""):
    page = max(1, min(page, 500))
    raw = await db.query_last("logs/actions", page * PAGE_SIZE_ADMIN + 200)
    rows = [v for _, v in sorted(raw.items(), reverse=True)]
    if source:
        rows = [r for r in rows if r.get("source") == source]
    if action:
        rows = [r for r in rows if action.lower() in (r.get("action") or "").lower()]
    if channel_id:
        rows = [r for r in rows if r.get("channel_id") == channel_id]
    chunk, page, pages = page_of(rows, page)
    return {"items": chunk, "page": page, "pages": pages, "total": len(rows)}


# ───────────── settings ─────────────
@router.get("/settings", dependencies=admin_dep)
async def get_settings(tok: dict = Depends(auth.require_admin)):
    s = await db.get("settings", {})
    meta = _eng().meta if runtime.engine else {}
    apis = [{"id": int(k), "hash": v["hash"][:4] + "…" + v["hash"][-3:],
            "sessions": sum(1 for m in meta.values() if m.get("api_id") == int(k))}
           for k, v in (s.get("apis") or {}).items() if str(k).isdigit() and isinstance(v, dict) and v.get("hash")]
    proxy = s.get("proxy") or {}
    return {"admin_username": tok["u"], "apis": apis, "proxy": {"enabled": bool(proxy.get("enabled")),
            "url": proxy.get("url", "")}, "safety": {**DEFAULT_SAFETY, **(s.get("safety") or {})}}


class PasswordIn(BaseModel):
    current: str = Field(max_length=256)
    new: str = Field(max_length=256)
    username: str = Field(default="", max_length=100)


@router.post("/settings/password", dependencies=admin_dep)
async def change_password(body: PasswordIn, request: Request, response: Response):
    ip = auth.client_ip(request)
    if auth.locked(ip):  # a stolen cookie must not be able to brute-force the current password
        raise HTTPException(429, "Too many attempts. Try again in 15 minutes.")
    a = await db.get("settings/admin")
    if not a or not await asyncio.to_thread(check_pw, body.current, a["hash"], a["salt"]):
        auth.record_fail(ip)
        raise HTTPException(400, "Current password is wrong")
    if len(body.new) < 8:
        raise HTTPException(400, "New password must be at least 8 characters")
    h, salt = await asyncio.to_thread(hash_pw, body.new)
    a.update(hash=h, salt=salt, ver=a["ver"] + 1)
    if body.username.strip():
        a["username"] = body.username.strip()
    await db.put("settings/admin", a)
    db.invalidate("settings/admin")
    _log("password_changed")
    response.set_cookie(auth.COOKIE, sign({"u": a["username"], "v": a["ver"]}, auth.TTL), max_age=auth.TTL, httponly=True,
                        samesite="strict", secure=_secure_cookie(request), path="/")
    return {"ok": True}


class ApiItem(BaseModel):
    id: int = Field(gt=0, lt=10**10)
    hash: str = Field(default="", max_length=64)


class ApisIn(BaseModel):
    apis: list[ApiItem] = Field(max_length=50)


@router.put("/settings/apis", dependencies=admin_dep)
async def set_apis(body: ApisIn):
    old = await db.get("settings/apis", {}) or {}
    new = {}
    for a in body.apis:
        h = a.hash.strip() or (old.get(str(a.id)) or {}).get("hash", "")
        if not h or len(h) < 16 or not re.fullmatch(r"[A-Za-z0-9]+", h):
            raise HTTPException(400, f"API hash for {a.id} is missing/invalid")
        new[str(a.id)] = {"hash": h}
    await db.put("settings/apis", new or None)
    db.invalidate("settings")
    _log("settings_apis", ",".join(new))
    return {"ok": True}


class ProxySettingIn(BaseModel):
    enabled: bool
    url: str = Field(default="", max_length=300)


@router.put("/settings/proxy", dependencies=admin_dep)
async def set_proxy(body: ProxySettingIn):
    if (body.enabled or body.url.strip()) and not parse_proxy(body.url):
        raise HTTPException(400, "Proxy must look like socks5://user:pass@host:port (or socks4:// / http://)")
    await store.set_setting("proxy", {"enabled": body.enabled, "url": body.url.strip()})
    _log("settings_proxy", f"enabled={body.enabled}")
    return {"ok": True}


class EngineIn(BaseModel):
    safety: dict = {}


@router.put("/settings/engine", dependencies=admin_dep)
async def set_engine(body: EngineIn):
    safety = {}
    for k, v in body.safety.items():
        if k in DEFAULT_SAFETY:
            lo, hi = SAFETY_LIMITS[k]
            safety[k] = _num(k, v, type(DEFAULT_SAFETY[k]), lo, hi)
    merged = {**DEFAULT_SAFETY, **(await store.get_safety()), **safety}
    if merged["jitter_min"] > merged["jitter_max"]:
        raise HTTPException(400, "Random delay min can't be above max")
    await db.put("settings/safety", merged)
    db.invalidate("settings")
    if runtime.engine:
        runtime.engine.apply_settings(merged)
    _log("settings_engine")
    return {"ok": True}
