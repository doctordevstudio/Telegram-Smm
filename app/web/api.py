import asyncio
import io
import logging
import math
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from aiogram.types import BufferedInputFile
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, Response, UploadFile
from pydantic import BaseModel

from .. import db, runtime
from ..config import cfg
from ..constants import DEFAULT_SAFETY, DEFAULT_SPEED, SERVICE_ORDER
from ..security import check_pw, hash_pw, sign
from ..services import broadcast, forcejoin, notify, store
from ..utils import calc_price, day_key, esc, money, now, spawn
from . import auth

log = logging.getLogger("web")
PAGE = 20
router = APIRouter(prefix="/api")
admin_dep = [Depends(auth.require_admin)]


def _admin_log(action: str, detail: str = "") -> None:
    store.log_bg("admin", None, action, detail)


# ───────────── helpers ─────────────
def days_for(rng: str, frm: str | None, to: str | None) -> list[str] | None:
    """None = all time."""
    tz = ZoneInfo(cfg.timezone)
    today = datetime.now(tz).date()
    if rng == "today":
        return [today.isoformat()]
    if rng == "yesterday":
        return [(today - timedelta(days=1)).isoformat()]
    if rng == "custom":
        try:
            a, b = date.fromisoformat(frm or ""), date.fromisoformat(to or frm or "")
        except ValueError:
            raise HTTPException(400, "Pick a valid date range")
        if b < a:
            a, b = b, a
        if (b - a).days > 366:
            raise HTTPException(400, "Range too large (max 1 year)")
        return [(a + timedelta(days=i)).isoformat() for i in range((b - a).days + 1)]
    return None


def page_of(ids: list, page: int):
    pages = max(1, math.ceil(len(ids) / PAGE))
    page = min(max(1, page), pages)
    return ids[(page - 1) * PAGE: page * PAGE], page, pages


async def ids_for_range(index: str, root: str, days: list[str] | None) -> list[str]:
    if days is None:
        ids = await db.keys(root)
    else:
        parts = []
        for i in range(0, len(days), 30):
            parts += await asyncio.gather(*(db.keys(f"idx/{index}/{d}") for d in days[i:i + 30]))
        ids = [x for p in parts for x in p]
    return sorted(ids, reverse=True)


async def fetch_many(root: str, ids: list[str]) -> list[dict]:
    res = await asyncio.gather(*(db.get(f"{root}/{i}") for i in ids))
    return [r for r in res if r]


def view_order(o: dict) -> dict:
    return {"id": o["id"], "n": int(o["id"]), "service": o["service"], "qty": o["qty"], "price": o["price"],
            "status": o["status"], "delivered": o.get("delivered", 0), "plan_id": o.get("plan_id", "custom"),
            "created_at": o["created_at"], "user": {"id": o["user_id"], **(o.get("user") or {})}, "note": o.get("note", ""),
            "target": (o.get("target") or {}).get("title", ""), "link": (o.get("target") or {}).get("link", "")}


async def orders_page(ids: list[str], page: int, service: str = "", status: str = "") -> dict:
    if service or status:  # filters need the documents: look at the newest 1500
        items = [o for o in await fetch_many("orders", ids[:1500])
                 if (not service or o["service"] == service) and (not status or o["status"] == status)]
        chunk, page, pages = page_of(items, page)
        return {"items": [view_order(o) for o in chunk], "page": page, "pages": pages, "total": len(items)}
    chunk, page, pages = page_of(ids, page)
    return {"items": [view_order(o) for o in await fetch_many("orders", chunk)], "page": page, "pages": pages, "total": len(ids)}


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
    return {"username": tok["u"], "tz": cfg.timezone}


# ───────────── dashboard ─────────────
@router.get("/dashboard", dependencies=admin_dep)
async def dashboard(range: str = "today", frm: str | None = None, to: str | None = None):
    days = days_for(range, frm, to)
    daily = await db.cached_get("stats/daily", 15) or {}
    totals = await db.get("stats/totals", {})
    if days is None:
        agg = {k: v for k, v in totals.items()}
    else:
        agg: dict = {}
        for d in days:
            for k, v in (daily.get(d) or {}).items():
                agg[k] = agg.get(k, 0) + v
    tz = ZoneInfo(cfg.timezone)
    today = datetime.now(tz).date()
    chart = []
    for i in range(13, -1, -1):
        d = (today - timedelta(days=i)).isoformat()
        x = daily.get(d) or {}
        chart.append({"day": d, "earning": x.get("earning", 0), "orders": x.get("orders", 0), "users": x.get("users", 0)})
    eng = runtime.engine.info() if runtime.engine else {}
    return {"users": int(totals.get("users", 0)), "agg": {k: round(agg.get(k, 0), 2) for k in
            ("earning", "spent", "orders", "deposits", "users", *[f"orders_{s}" for s in SERVICE_ORDER])},
            "chart": chart, "engine": eng}


@router.get("/orders", dependencies=admin_dep)
async def orders(range: str = "all", frm: str | None = None, to: str | None = None, service: str = "", status: str = "",
                 page: int = 1):
    ids = await ids_for_range("orders_by_day", "orders", days_for(range, frm, to))
    return await orders_page(ids, page, service, status)


@router.post("/orders/{oid}/cancel", dependencies=admin_dep)
async def cancel_order(oid: str):
    if not runtime.engine or not await runtime.engine.runner.cancel(oid):
        raise HTTPException(400, "Order can't be cancelled (already finished?)")
    _admin_log("order_cancel", oid)
    return {"ok": True}


# ───────────── users ─────────────
@router.get("/users", dependencies=admin_dep)
async def users(page: int = 1, q: str = ""):
    if q.strip().isdigit():
        u = await store.get_user(int(q.strip()))
        items = [u] if u else []
        total, page, pages = len(items), 1, 1
    else:
        idx = await db.get("idx/users", {})
        ids = [k for k, _ in sorted(idx.items(), key=lambda kv: kv[1], reverse=True)]
        chunk, page, pages = page_of(ids, page)
        items, total = await fetch_many("users", chunk), len(ids)
    bals = await asyncio.gather(*(store.get_balance(u["id"]) for u in items))
    return {"items": [{"id": u["id"], "name": u.get("name", ""), "username": u.get("username", ""),
                       "joined_at": u.get("joined_at", 0), "balance": b, "banned": u["id"] in store.BANNED}
                      for u, b in zip(items, bals)], "page": page, "pages": pages, "total": total}


@router.get("/users/{uid}", dependencies=admin_dep)
async def user_detail(uid: int):
    u = await store.get_user(uid)
    if not u:
        raise HTTPException(404, "User not found")
    ids = (await store.user_order_ids(uid))[:8]
    dids = (await store.user_deposit_ids(uid))[:8]
    return {"user": u, "balance": await store.get_balance(uid), "banned": uid in store.BANNED,
            "channel": await store.get_channel(uid), "orders": [view_order(o) for o in await store.get_orders(ids)],
            "deposits": await store.get_deposits(dids)}


class BanIn(BaseModel):
    banned: bool


@router.post("/users/{uid}/ban", dependencies=admin_dep)
async def ban_user(uid: int, body: BanIn):
    if uid in cfg.admin_ids:
        raise HTTPException(400, "Can't ban an admin")
    await store.set_banned(uid, body.banned)
    _admin_log("ban" if body.banned else "unban", str(uid))
    await notify.notify_user(runtime.bot, uid, "🚫 You have been banned from using this bot." if body.banned
                             else "✅ You have been unbanned. Send /start to continue.")
    return {"ok": True}


class BalanceIn(BaseModel):
    amount: float
    note: str = ""


@router.post("/users/{uid}/balance", dependencies=admin_dep)
async def adjust_balance(uid: int, body: BalanceIn):
    if not body.amount or abs(body.amount) > 1_000_000:
        raise HTTPException(400, "Enter a non-zero amount")
    try:
        bal = await (store.credit(uid, body.amount) if body.amount > 0 else store.debit(uid, -body.amount))
    except store.InsufficientFunds:
        raise HTTPException(400, "User doesn't have that much balance")
    _admin_log("balance_adjust", f"{uid} {body.amount:+g} {body.note}")
    await notify.notify_user(runtime.bot, uid, f"💰 Your wallet was {'credited' if body.amount > 0 else 'debited'} "
                                               f"<b>{money(abs(body.amount))}</b> by admin.\n👛 Balance: <b>{money(bal)}</b>")
    return {"balance": bal}


class MsgIn(BaseModel):
    text: str


@router.post("/users/{uid}/message", dependencies=admin_dep)
async def message_user(uid: int, body: MsgIn):
    ok = await notify.notify_user(runtime.bot, uid, esc(body.text))
    if not ok:
        raise HTTPException(400, "Couldn't deliver (user blocked the bot?)")
    _admin_log("message_user", str(uid))
    return {"ok": True}


# ───────────── plans ─────────────
async def _plans_payload() -> dict:
    out = {}
    for svc in SERVICE_ORDER:
        raw = await db.get(f"plans/{svc}", {})
        pr = await store.get_pricing(svc)
        counts = await asyncio.gather(*(db.keys(f"idx/plan_orders/{svc}/{pid}") for pid in [*raw.keys(), "custom"]))
        cmap = dict(zip([*raw.keys(), "custom"], [len(c) for c in counts]))
        out[svc] = {"pricing": pr, "custom_purchases": cmap["custom"],
                    "plans": sorted([{**v, "id": k, "purchases": cmap[k]} for k, v in raw.items()], key=lambda p: p["qty"])}
    return out


@router.get("/plans", dependencies=admin_dep)
async def plans():
    return await _plans_payload()


class PlanIn(BaseModel):
    qty: int
    price: float
    active: bool = True


def _svc(svc: str) -> str:
    if svc not in SERVICE_ORDER:
        raise HTTPException(404, "Unknown service")
    return svc


@router.post("/plans/{svc}", dependencies=admin_dep)
async def add_plan(svc: str, body: PlanIn):
    _svc(svc)
    if body.qty < 1 or body.price < 0:
        raise HTTPException(400, "Quantity must be ≥ 1 and price ≥ 0")
    pid = f"p{await db.next_id('plan_' + svc)}x"
    await db.put(f"plans/{svc}/{pid}", {"qty": body.qty, "price": round(body.price, 2), "active": body.active})
    db.invalidate("plans")
    _admin_log("plan_add", f"{svc} {body.qty}={body.price}")
    return {"id": pid}


@router.put("/plans/{svc}/{pid}", dependencies=admin_dep)
async def edit_plan(svc: str, pid: str, body: PlanIn):
    _svc(svc)
    if not await db.get(f"plans/{svc}/{pid}"):
        raise HTTPException(404, "Plan not found")
    await db.put(f"plans/{svc}/{pid}", {"qty": body.qty, "price": round(body.price, 2), "active": body.active})
    db.invalidate("plans")
    _admin_log("plan_edit", f"{svc}/{pid} {body.qty}={body.price} active={body.active}")
    return {"ok": True}


@router.delete("/plans/{svc}/{pid}", dependencies=admin_dep)
async def del_plan(svc: str, pid: str):
    _svc(svc)
    await db.delete(f"plans/{svc}/{pid}")
    db.invalidate("plans")
    _admin_log("plan_delete", f"{svc}/{pid}")
    return {"ok": True}


class PricingIn(BaseModel):
    unit: float
    min: int
    max: int
    recalc: bool = False


@router.put("/pricing/{svc}", dependencies=admin_dep)
async def set_pricing(svc: str, body: PricingIn):
    _svc(svc)
    if body.unit < 0 or body.min < 1 or body.max < body.min:
        raise HTTPException(400, "Check the values: min ≥ 1 and max ≥ min")
    up = {f"settings/pricing/{svc}": {"unit": body.unit, "min": body.min, "max": body.max}}
    if body.recalc:
        for pid, p in (await db.get(f"plans/{svc}", {})).items():
            up[f"plans/{svc}/{pid}/price"] = calc_price(p["qty"], body.unit)
    await db.multi_update(up)
    db.invalidate("plans", "settings")
    _admin_log("pricing", f"{svc} {body.unit} recalc={body.recalc}")
    return {"ok": True}


@router.get("/plans/{svc}/{pid}/orders", dependencies=admin_dep)
async def plan_orders(svc: str, pid: str, page: int = 1):
    _svc(svc)
    ids = sorted(await db.keys(f"idx/plan_orders/{svc}/{pid}"), reverse=True)
    return await orders_page(ids, page)


# ───────────── payments ─────────────
def view_dep(d: dict) -> dict:
    return {k: d.get(k) for k in ("id", "user_id", "name", "username", "amount", "status", "created_at", "reason", "reviewed_at")} | {"n": int(d["id"])}


@router.get("/payments", dependencies=admin_dep)
async def payments(range: str = "all", frm: str | None = None, to: str | None = None, status: str = "", page: int = 1):
    ids = await ids_for_range("deposits_by_day", "deposits", days_for(range, frm, to))
    items = await fetch_many("deposits", ids[:2000])
    summary = {s: {"n": 0, "sum": 0} for s in ("pending", "approved", "rejected")}
    for d in items:
        summary[d["status"]]["n"] += 1
        summary[d["status"]]["sum"] += d["amount"]
    if status:
        items = [d for d in items if d["status"] == status]
    chunk, page, pages = page_of(items, page)
    return {"items": [view_dep(d) for d in chunk], "summary": summary, "page": page, "pages": pages, "total": len(items)}


@router.get("/payments/{did}/proof", dependencies=admin_dep)
async def payment_proof(did: str):
    d = await store.get_deposit(did)
    if not d:
        raise HTTPException(404)
    buf = io.BytesIO()
    f = await runtime.bot.get_file(d["proof"])
    await runtime.bot.download_file(f.file_path, buf)
    return Response(buf.getvalue(), media_type="image/jpeg", headers={"Cache-Control": "private, max-age=3600"})


class RejectIn(BaseModel):
    reason: str = ""


@router.post("/payments/{did}/approve", dependencies=admin_dep)
async def approve_payment(did: str):
    try:
        dep, bal = await store.resolve_deposit(did, True, cfg.admin_ids[0])
    except store.AlreadyProcessed:
        raise HTTPException(409, "Already processed")
    _admin_log("deposit_approved", f"#{did} {dep['amount']}")
    await notify.notify_user(runtime.bot, dep["user_id"], (
        f"✅ <b>Payment Approved!</b>\n\n💰 {money(dep['amount'])} has been added to your wallet.\n"
        f"👛 New balance: <b>{money(bal)}</b>\n\nReady to grow your channel? 🚀"))
    return {"ok": True}


@router.post("/payments/{did}/reject", dependencies=admin_dep)
async def reject_payment(did: str, body: RejectIn):
    reason = body.reason.strip()[:300] or "Payment could not be verified"
    try:
        dep, _ = await store.resolve_deposit(did, False, cfg.admin_ids[0], reason)
    except store.AlreadyProcessed:
        raise HTTPException(409, "Already processed")
    _admin_log("deposit_rejected", f"#{did} {reason}")
    await notify.notify_user(runtime.bot, dep["user_id"], (
        f"❌ <b>Payment Failed</b>\n\nYour deposit of <b>{money(dep['amount'])}</b> (request #{int(dep['id'])}) could not "
        f"be verified.\n📝 Reason: {esc(reason)}\n\nIf you think this is a mistake, please contact support."))
    return {"ok": True}


# ───────────── broadcasts ─────────────
@router.get("/broadcasts", dependencies=admin_dep)
async def broadcasts(range: str = "all", frm: str | None = None, to: str | None = None, page: int = 1):
    ids = await ids_for_range("broadcasts_by_day", "broadcasts", days_for(range, frm, to))
    chunk, page, pages = page_of(ids, page)
    return {"items": await fetch_many("broadcasts", chunk), "page": page, "pages": pages, "total": len(ids)}


@router.post("/broadcasts", dependencies=admin_dep)
async def new_broadcast(text: str = Form(""), file: UploadFile | None = File(None)):
    bot, admin = runtime.bot, cfg.admin_ids[0]
    text = text.strip()
    data = await file.read() if file and file.filename else b""
    if len(data) > 20 * 1024 * 1024:
        raise HTTPException(400, "File too large (max 20 MB)")
    if not text and not data:
        raise HTTPException(400, "Write a message or attach a file")
    try:
        if data:
            ct, f = (file.content_type or ""), BufferedInputFile(data, file.filename)
            if ct.startswith("image/"):
                m = await bot.send_photo(admin, f, caption=text or None, disable_notification=True)
            elif ct.startswith("video/"):
                m = await bot.send_video(admin, f, caption=text or None, disable_notification=True)
            else:
                m = await bot.send_document(admin, f, caption=text or None, disable_notification=True)
        else:
            m = await bot.send_message(admin, text, disable_notification=True)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(400, f"Telegram rejected the message: {str(e)[:150]} (check the HTML tags)")
    kind, preview = broadcast.describe(m)
    bid = await broadcast.create(bot, admin, admin, m.message_id, kind, preview)
    _admin_log("broadcast_create", f"#{bid} {kind}")
    return {"id": bid}


@router.post("/broadcasts/{bid}/rebroadcast", dependencies=admin_dep)
async def rebroadcast(bid: str):
    nb = await broadcast.rebroadcast(runtime.bot, cfg.admin_ids[0], bid)
    if not nb:
        raise HTTPException(404, "Broadcast not found or deleted")
    _admin_log("broadcast_resend", f"#{bid} -> #{nb}")
    return {"id": nb}


class TextIn(BaseModel):
    text: str


@router.put("/broadcasts/{bid}", dependencies=admin_dep)
async def edit_broadcast(bid: str, body: TextIn):
    if not await broadcast.edit(runtime.bot, bid, body.text.strip()):
        raise HTTPException(400, "This broadcast can't be edited (no text/caption)")
    _admin_log("broadcast_edit", f"#{bid}")
    return {"ok": True}


@router.delete("/broadcasts/{bid}", dependencies=admin_dep)
async def delete_broadcast(bid: str):
    if not await broadcast.delete(runtime.bot, bid):
        raise HTTPException(404, "Broadcast not found")
    _admin_log("broadcast_delete", f"#{bid}")
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
    a = [x for x in (m.get("use") or {}).get("a", []) if t - x < 3600]
    return {"id": m["id"], "label": m.get("label", ""), "phone": m.get("phone", ""), "username": m.get("username", ""),
            "user_id": m.get("user_id"), "status": st, "api_id": m.get("api_id"), "added_at": m.get("added_at"),
            "last_check": m.get("last_check"), "note": m.get("note", ""), "proxy": bool(m.get("proxy")),
            "flood_until": m.get("flood_until", 0), "actions_hr": len(a)}


@router.get("/sessions", dependencies=admin_dep)
async def sessions(page: int = 1, status: str = ""):
    s = _eng()
    rows = [view_session(m) for m in s.meta.values()]
    rows.sort(key=lambda r: r["added_at"] or 0, reverse=True)
    for r in rows:  # live usage numbers from memory
        r["actions_hr"] = len([x for x in s.usage(r["id"])["a"] if now() - x < 3600])
    if status:
        rows = [r for r in rows if r["status"] == status]
    chunk, page, pages = page_of(rows, page)
    return {"items": chunk, "counts": s.counts(), "page": page, "pages": pages, "total": len(rows),
            "validation": s.validation}


@router.post("/sessions/upload", dependencies=admin_dep)
async def upload_session(file: UploadFile = File(...)):
    raw = await file.read()
    if len(raw) > 5 * 1024 * 1024:
        return {"ok": False, "error": "File too large"}
    r = await _eng().import_file(file.filename or "session", raw)
    _admin_log("session_import", f"{file.filename}: {'ok' if r['ok'] else r['error']}")
    return r


class StringsIn(BaseModel):
    strings: list[str]


@router.post("/sessions/string", dependencies=admin_dep)
async def add_strings(body: StringsIn):
    out = []
    for s in [x for x in (t.strip() for t in body.strings) if x][:200]:
        out.append(await _eng().import_string(s))
    _admin_log("session_import_string", f"{sum(1 for o in out if o['ok'])}/{len(out)} ok")
    return {"results": out}


class IdsIn(BaseModel):
    ids: list[str] = []


@router.post("/sessions/validate", dependencies=admin_dep)
async def validate_sessions(body: IdsIn):
    s = _eng()
    if s.validation["running"]:
        raise HTTPException(409, "A validation run is already in progress")
    ids = [i for i in (body.ids or list(s.meta)) if i in s.meta]
    spawn(s.validate_many(ids))
    _admin_log("session_validate", f"{len(ids)} sessions")
    return {"started": len(ids)}


@router.post("/sessions/delete", dependencies=admin_dep)
async def delete_sessions(body: IdsIn):
    s, n = _eng(), 0
    for sid in body.ids:
        n += 1 if await s.delete(sid) else 0
    _admin_log("session_delete", f"{n} sessions")
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
    return {"ok": True}


# ───────────── logs ─────────────
@router.get("/logs", dependencies=admin_dep)
async def logs(source: str = "bot", page: int = 1):
    if source not in ("bot", "admin"):
        raise HTTPException(400)
    page = max(1, min(page, 250))
    raw = await db.query_last(f"logs/{source}", page * PAGE + 1)
    rows = [v for _, v in sorted(raw.items(), reverse=True)]
    chunk = rows[(page - 1) * PAGE: page * PAGE]
    return {"items": chunk, "page": page, "has_next": len(rows) > page * PAGE}


# ───────────── settings ─────────────
@router.get("/settings", dependencies=admin_dep)
async def get_settings(tok: dict = Depends(auth.require_admin)):
    s = await db.get("settings", {})
    meta = _eng().meta if runtime.engine else {}
    apis = [{"id": int(k), "hash": v["hash"][:4] + "…" + v["hash"][-3:], "sessions": sum(1 for m in meta.values() if m.get("api_id") == int(k))}
            for k, v in (s.get("apis") or {}).items()]
    return {"upi_id": s.get("upi_id", ""), "support_username": s.get("support_username", ""), "has_qr": bool(s.get("qr_file_id")),
            "force_channel": s.get("force_channel"), "apis": apis, "admin_username": tok["u"],
            "safety": {**DEFAULT_SAFETY, **(s.get("safety") or {})}, "speed": {**DEFAULT_SPEED, **(s.get("speed") or {})}}


class GeneralIn(BaseModel):
    upi_id: str = ""
    support_username: str = ""


@router.put("/settings/general", dependencies=admin_dep)
async def set_general(body: GeneralIn):
    if body.upi_id and "@" not in body.upi_id:
        raise HTTPException(400, "UPI ID looks like name@bank")
    await db.multi_update({"settings/upi_id": body.upi_id.strip() or None,
                           "settings/support_username": body.support_username.strip().lstrip("@") or None})
    db.invalidate("settings")
    _admin_log("settings_general", body.upi_id)
    return {"ok": True}


@router.post("/settings/qr", dependencies=admin_dep)
async def upload_qr(file: UploadFile = File(...)):
    data = await file.read()
    if not (file.content_type or "").startswith("image/") or len(data) > 10 * 1024 * 1024:
        raise HTTPException(400, "Upload an image (max 10 MB)")
    try:  # the image is stored inside Telegram; only its file_id is kept
        m = await runtime.bot.send_photo(cfg.admin_ids[0], BufferedInputFile(data, "qr.png"), caption="✅ Payment QR updated",
                                         disable_notification=True)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(400, f"Telegram rejected the image: {str(e)[:120]}")
    await store.set_setting("qr_file_id", m.photo[-1].file_id)
    _admin_log("settings_qr")
    return {"ok": True}


@router.get("/settings/qr", dependencies=admin_dep)
async def get_qr():
    fid = (await store.get_settings()).get("qr_file_id")
    if not fid:
        raise HTTPException(404)
    buf = io.BytesIO()
    await runtime.bot.download_file((await runtime.bot.get_file(fid)).file_path, buf)
    return Response(buf.getvalue(), media_type="image/jpeg", headers={"Cache-Control": "no-store"})


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
    _admin_log("password_changed")
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
    _admin_log("settings_apis", ",".join(new))
    return {"ok": True}


class ForceIn(BaseModel):
    channel: str = ""


@router.put("/settings/force", dependencies=admin_dep)
async def set_force(body: ForceIn):
    ok, msg = await forcejoin.set_force_channel(runtime.bot, body.channel)
    if not ok:
        raise HTTPException(400, msg.replace("<b>", "").replace("</b>", ""))
    _admin_log("settings_force", body.channel or "off")
    return {"ok": True, "message": msg.replace("<b>", "").replace("</b>", "")}


class EngineIn(BaseModel):
    safety: dict = {}
    speed: dict = {}


@router.put("/settings/engine", dependencies=admin_dep)
async def set_engine(body: EngineIn):
    safety = {k: float(v) if "." in str(v) else int(v) for k, v in body.safety.items() if k in DEFAULT_SAFETY}
    speed = {k: float(v) for k, v in body.speed.items() if k in DEFAULT_SPEED}
    if any(v < 0 for v in [*safety.values(), *speed.values()]):
        raise HTTPException(400, "Values must be positive")
    if safety.get("max_clients", 1) < 1 or safety.get("max_clients", 1) > 12:
        raise HTTPException(400, "Max connections must be 1–12 (RAM!)")
    await db.multi_update({"settings/safety": {**DEFAULT_SAFETY, **safety}, "settings/speed": {**DEFAULT_SPEED, **speed}})
    db.invalidate("settings")
    if runtime.engine:
        runtime.engine.apply_settings({**DEFAULT_SAFETY, **safety})
    _admin_log("settings_engine")
    return {"ok": True}
