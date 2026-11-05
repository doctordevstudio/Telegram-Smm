import asyncio
import io
import logging
import math

from fastapi import APIRouter, Depends, File, HTTPException, Request, Response, UploadFile
from pydantic import BaseModel

from .. import db, runtime
from ..config import cfg
from ..constants import (DEFAULT_CHANNEL_SETTINGS, DEFAULT_SAFETY, DEFAULT_SERVICE_CONFIG, PAGE_SIZE_ADMIN,
                         SERVICE_ORDER, SERVICES)
from ..roles import add_admin, is_owner, list_admins_all, remove_admin
from ..security import check_pw, hash_pw, sign
from ..services import store
from ..utils import esc, fmt_days_left, now
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


# ───────────── auth ─────────────
class LoginIn(BaseModel):
    username: str
    password: str


@router.post("/login")
async def login(body: LoginIn, request: Request, response: Response):
    tok = await auth.login(body.username.strip(), body.password, auth.client_ip(request))
    response.set_cookie(auth.COOKIE, tok, max_age=auth.TTL, httponly=True, samesite="strict",
                        secure=request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https")
    return {"ok": True}


@router.post("/logout")
async def logout(response: Response):
    response.delete_cookie(auth.COOKIE)
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
    days: int


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
    done = await store.revoke_all_services(cid, cfg.owner_id)
    _log("revoke_all", ",".join(done), cid)
    return {"revoked": done}


# ───────────── services ─────────────
def _svc(svc: str) -> str:
    if svc not in SERVICE_ORDER:
        raise HTTPException(404, "Unknown service")
    return svc


class EnableIn(BaseModel):
    days: int
    rate_note: str = ""
    config: dict = {}


@router.post("/channels/{cid}/services/{svc}/enable", dependencies=admin_dep)
async def enable_service(cid: str, svc: str, body: EnableIn):
    _svc(svc)
    if not await store.get_channel(cid, include_hidden=True):
        raise HTTPException(404, "Channel not found")
    if body.days < 1 or body.days > 3650:
        raise HTTPException(400, "Days must be between 1 and 3650")
    await store.enable_service(cid, svc, body.days, body.config or {}, body.rate_note.strip()[:200], cfg.owner_id)
    _log("service_enabled", f"{svc} {body.days}d", cid)
    return {"ok": True}


class ConfigIn(BaseModel):
    config: dict


@router.put("/channels/{cid}/services/{svc}/config", dependencies=admin_dep)
async def set_service_config(cid: str, svc: str, body: ConfigIn):
    _svc(svc)
    await store.update_service_config(cid, svc, body.config)
    _log("config_set", f"{svc} {body.config}", cid)
    return {"ok": True}


class ExtendIn(BaseModel):
    days: int


@router.post("/channels/{cid}/services/{svc}/extend", dependencies=admin_dep)
async def extend_service(cid: str, svc: str, body: ExtendIn):
    _svc(svc)
    if body.days < 1 or body.days > 3650:
        raise HTTPException(400, "Days must be between 1 and 3650")
    new_exp = await store.extend_service(cid, svc, body.days, cfg.owner_id)
    _log("service_extended", f"{svc} +{body.days}d", cid)
    return {"expires_at": new_exp}


@router.post("/channels/{cid}/services/{svc}/revoke", dependencies=admin_dep)
async def revoke_service(cid: str, svc: str):
    _svc(svc)
    await store.revoke_service(cid, svc, cfg.owner_id)
    _log("service_revoked", svc, cid)
    return {"ok": True}


class ChannelSettingsIn(BaseModel):
    delay_seconds: int | None = None
    night_mode: dict | None = None
    wave_mode: dict | None = None


@router.put("/channels/{cid}/settings", dependencies=admin_dep)
async def set_channel_settings(cid: str, body: ChannelSettingsIn):
    patch = {k: v for k, v in body.model_dump().items() if v is not None}
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
    raw = await file.read()
    if len(raw) > 5 * 1024 * 1024:
        return {"ok": False, "error": "File too large"}
    r = await _eng().import_file(file.filename or "session", raw)
    _log("session_import", f"{file.filename}: {'ok' if r['ok'] else r['error']}")
    return r


class StringsIn(BaseModel):
    strings: list[str]


@router.post("/sessions/string", dependencies=admin_dep)
async def add_strings(body: StringsIn):
    out = [await _eng().import_string(s) for s in [x.strip() for x in body.strings] if s][:200]
    _log("session_import_string", f"{sum(1 for o in out if o['ok'])}/{len(out)} ok")
    return {"results": out}


class IdsIn(BaseModel):
    ids: list[str] = []


@router.post("/sessions/validate", dependencies=admin_dep)
async def validate_sessions(body: IdsIn):
    s = _eng()
    if s.validation["running"]:
        raise HTTPException(409, "A validation run is already in progress")
    ids = [i for i in (body.ids or list(s.meta)) if i in s.meta]
    from ..utils import spawn
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
    proxy: str = ""


@router.put("/sessions/{sid}/proxy", dependencies=admin_dep)
async def session_proxy(sid: str, body: ProxyIn):
    s = _eng()
    if sid not in s.meta:
        raise HTTPException(404)
    p = body.proxy.strip()
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
    id: int
    name: str = ""
    username: str = ""


@router.post("/admins", dependencies=admin_dep)
async def admins_add(body: AddAdminIn):
    if is_owner(body.id):
        raise HTTPException(400, "That's already the owner")
    ok = await add_admin(body.id, body.name, body.username, cfg.owner_id)
    if not ok:
        raise HTTPException(409, "Already has access")
    _log("admin_added", str(body.id))
    if runtime.bot:
        from contextlib import suppress
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
           for k, v in (s.get("apis") or {}).items()]
    proxy = s.get("proxy") or {}
    return {"admin_username": tok["u"], "apis": apis, "proxy": {"enabled": bool(proxy.get("enabled")),
            "url": proxy.get("url", "")}, "safety": {**DEFAULT_SAFETY, **(s.get("safety") or {})}}


class PasswordIn(BaseModel):
    current: str
    new: str
    username: str = ""


@router.post("/settings/password", dependencies=admin_dep)
async def change_password(body: PasswordIn, request: Request, response: Response):
    a = await db.get("settings/admin")
    if not check_pw(body.current, a["hash"], a["salt"]):
        raise HTTPException(400, "Current password is wrong")
    if len(body.new) < 8:
        raise HTTPException(400, "New password must be at least 8 characters")
    h, salt = hash_pw(body.new)
    a.update(hash=h, salt=salt, ver=a["ver"] + 1)
    if body.username.strip():
        a["username"] = body.username.strip()
    await db.put("settings/admin", a)
    db.invalidate("settings/admin")
    _log("password_changed")
    response.set_cookie(auth.COOKIE, sign({"u": a["username"], "v": a["ver"]}, auth.TTL), max_age=auth.TTL, httponly=True,
                        samesite="strict", secure=request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https")
    return {"ok": True}


class ApiItem(BaseModel):
    id: int
    hash: str = ""


class ApisIn(BaseModel):
    apis: list[ApiItem]


@router.put("/settings/apis", dependencies=admin_dep)
async def set_apis(body: ApisIn):
    old = await db.get("settings/apis", {})
    new = {}
    for a in body.apis:
        h = a.hash.strip() or (old.get(str(a.id)) or {}).get("hash", "")
        if not h or len(h) < 16:
            raise HTTPException(400, f"API hash for {a.id} is missing/invalid")
        new[str(a.id)] = {"hash": h}
    await db.put("settings/apis", new or None)
    db.invalidate("settings")
    _log("settings_apis", ",".join(new))
    return {"ok": True}


class ProxySettingIn(BaseModel):
    enabled: bool
    url: str = ""


@router.put("/settings/proxy", dependencies=admin_dep)
async def set_proxy(body: ProxySettingIn):
    if body.enabled and not body.url.strip().startswith(("socks5://", "socks4://", "http://")):
        raise HTTPException(400, "URL must start with socks5://, socks4:// or http://")
    await store.set_setting("proxy", {"enabled": body.enabled, "url": body.url.strip()})
    _log("settings_proxy", f"enabled={body.enabled}")
    return {"ok": True}


class EngineIn(BaseModel):
    safety: dict = {}


@router.put("/settings/engine", dependencies=admin_dep)
async def set_engine(body: EngineIn):
    safety = {k: (float(v) if "." in str(v) else int(v)) for k, v in body.safety.items() if k in DEFAULT_SAFETY}
    if any(v < 0 for v in safety.values()):
        raise HTTPException(400, "Values must be positive")
    if safety.get("max_clients", 1) < 1 or safety.get("max_clients", 1) > 20:
        raise HTTPException(400, "Max connections must be 1-20 (RAM!)")
    await db.put("settings/safety", {**DEFAULT_SAFETY, **safety})
    db.invalidate("settings")
    if runtime.engine:
        runtime.engine.apply_settings({**DEFAULT_SAFETY, **safety})
    _log("settings_engine")
    return {"ok": True}
