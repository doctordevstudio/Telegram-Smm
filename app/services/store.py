"""All Firebase data access in one place (users, wallet, orders, deposits, plans...).

Layout (flat, paginated via small index nodes):
  users/{uid}  channels/{uid}  wallets/{uid}
  orders/{id}  deposits/{id}   idx/user_orders/{uid}/{id}  idx/user_deposits/{uid}/{id}
  idx/pending/{uid}  idx/banned/{uid}  plans/{svc}/{pid}  settings/*
  stats/totals  stats/daily/{YYYY-MM-DD}  logs/{bot|admin}  counters/*  targets/*
"""
from __future__ import annotations

import asyncio
import logging

from .. import db
from ..config import cfg
from ..constants import DEFAULT_PLAN_QTYS, DEFAULT_PRICING, SERVICE_ORDER
from ..utils import calc_price, day_key, now, spawn

log = logging.getLogger("store")

BANNED: set[int] = set()


class InsufficientFunds(Exception):
    pass


class AlreadyProcessed(Exception):
    pass


def _safe(key: str) -> str:
    if not key or not key.replace("_", "").replace("-", "").isalnum():
        raise ValueError("bad key")
    return key


# ───────────── settings / pricing / plans ─────────────
async def get_settings() -> dict:
    return await db.cached_get("settings", ttl=20) or {}


async def set_setting(key: str, value) -> None:
    await db.put(f"settings/{key}", value)
    db.invalidate("settings")


async def seed_defaults() -> None:
    s = await db.get("settings") or {}
    up: dict = {}
    if "pricing" not in s:
        up["settings/pricing"] = DEFAULT_PRICING
    if not s.get("upi_id") and cfg.upi_id:
        up["settings/upi_id"] = cfg.upi_id
    if not s.get("support_username") and cfg.support_username:
        up["settings/support_username"] = cfg.support_username
    plans = await db.get("plans") or {}
    pricing = s.get("pricing") or DEFAULT_PRICING
    for svc in SERVICE_ORDER:
        if svc not in plans:
            for i, q in enumerate(DEFAULT_PLAN_QTYS[svc], 1):
                up[f"plans/{svc}/p{i}"] = {"qty": q, "price": calc_price(q, pricing[svc]["unit"]), "active": True}
    if up:
        await db.multi_update(up)
        db.invalidate()
        log.info("seeded defaults: %d paths", len(up))


async def get_pricing(svc: str) -> dict:
    s = await get_settings()
    p = {**DEFAULT_PRICING[svc], **((s.get("pricing") or {}).get(svc) or {})}
    return {"unit": float(p["unit"]), "min": int(p["min"]), "max": int(p["max"])}


async def get_plans(svc: str) -> list[dict]:
    raw = await db.cached_get(f"plans/{svc}", ttl=20) or {}
    plans = [{**v, "id": k} for k, v in raw.items() if isinstance(v, dict) and v.get("active", True)]
    return sorted(plans, key=lambda p: p["qty"])


async def get_plan(svc: str, pid: str) -> dict | None:
    p = await db.cached_get(f"plans/{svc}/{_safe(pid)}", ttl=20)
    return {**p, "id": pid} if isinstance(p, dict) and p.get("active", True) else None


# ───────────── users ─────────────
async def get_user(uid: int) -> dict | None:
    return await db.get(f"users/{uid}")


async def update_user(uid: int, patch: dict) -> None:
    await db.update(f"users/{uid}", patch)


async def total_users() -> int:
    return int(await db.get("stats/totals/users", 0))


async def upsert_user(bot, tg) -> bool:
    """Store/refresh user. Returns True if this is a brand-new user."""
    path = f"users/{tg.id}"
    existing = await db.get(path)
    name, uname = tg.full_name, tg.username or ""
    if existing:
        patch = {"last_seen": now()}
        if existing.get("name") != name:
            patch["name"] = name
        if existing.get("username", "") != uname:
            patch["username"] = uname
        await db.update(path, patch)
        return False
    photo = None
    try:  # profile picture is stored as a Telegram file_id (nothing is saved in Firebase)
        photos = await bot.get_user_profile_photos(tg.id, limit=1)
        if photos.total_count and photos.photos:
            photo = photos.photos[0][-1].file_id
    except Exception as e:  # noqa: BLE001
        log.info("no profile photo for %s: %s", tg.id, e)
    await db.multi_update({path: {"id": tg.id, "name": name, "username": uname, "photo": photo, "joined_at": now(),
                               "last_seen": now(), "lang": tg.language_code},
                           f"idx/users/{tg.id}": now()})
    spawn(bump_stats(users=1))
    return True


async def set_banned(uid: int, banned: bool) -> None:
    if banned:
        await db.put(f"idx/banned/{uid}", True)
        BANNED.add(uid)
    else:
        await db.delete(f"idx/banned/{uid}")
        BANNED.discard(uid)


# ───────────── channel ─────────────
async def get_channel(uid: int) -> dict | None:
    return await db.get(f"channels/{uid}")


async def save_channel(uid: int, data: dict) -> None:
    await db.put(f"channels/{uid}", data)


async def delete_channel(uid: int) -> None:
    await db.delete(f"channels/{uid}")


# ───────────── wallet ─────────────
async def get_balance(uid: int) -> float:
    return float(await db.get(f"wallets/{uid}", 0))


async def debit(uid: int, amount: float) -> float:
    amount = round(float(amount), 2)

    def _f(cur):
        cur = float(cur or 0)
        if cur + 1e-9 < amount:
            raise InsufficientFunds()
        return round(cur - amount, 2)

    return float(await db.txn(f"wallets/{uid}", _f))


async def credit(uid: int, amount: float) -> float:
    amount = round(float(amount), 2)
    return float(await db.txn(f"wallets/{uid}", lambda cur: round(float(cur or 0) + amount, 2)))


# ───────────── stats ─────────────
async def bump_stats(**kv) -> None:
    """Counters for the dashboard: totals + per-day (users, orders, spent, earning, deposits, orders_<svc>)."""
    day = day_key(cfg.timezone)
    jobs = []
    for k, v in kv.items():
        jobs.append(db.incr(f"stats/totals/{k}", v))
        jobs.append(db.incr(f"stats/daily/{day}/{k}", v))
    await asyncio.gather(*jobs, return_exceptions=True)


# ───────────── orders ─────────────
async def create_order(uid: int, order: dict) -> str:
    key = f"{await db.next_id('orders'):09d}"
    order = {**order, "id": key}
    day = day_key(cfg.timezone, order["created_at"])
    await db.multi_update({
        f"orders/{key}": order,
        f"idx/user_orders/{uid}/{key}": order["created_at"],
        f"idx/active_orders/{key}": True,
        f"idx/orders_by_day/{day}/{key}": True,
        f"idx/plan_orders/{order['service']}/{order.get('plan_id') or 'custom'}/{key}": uid,
    })
    return key


async def user_order_ids(uid: int) -> list[str]:
    idx = await db.get(f"idx/user_orders/{uid}", {})
    return sorted(idx.keys(), reverse=True)


async def get_orders(ids: list[str]) -> list[dict]:
    res = await asyncio.gather(*(db.get(f"orders/{i}") for i in ids))
    return [r for r in res if r]


async def get_order(oid: str) -> dict | None:
    return await db.get(f"orders/{_safe(oid)}")


# ───────────── deposits ─────────────
async def create_deposit(tg, amount: int, proof_file_id: str) -> str:
    key = f"{await db.next_id('deposits'):09d}"
    dep = {"id": key, "user_id": tg.id, "name": tg.full_name, "username": tg.username or "", "amount": amount,
           "status": "pending", "proof": proof_file_id, "created_at": now()}
    day = day_key(cfg.timezone, dep["created_at"])
    await db.multi_update({f"deposits/{key}": dep, f"idx/user_deposits/{tg.id}/{key}": dep["created_at"],
                           f"idx/deposits_by_day/{day}/{key}": True})
    await db.incr(f"idx/pending/{tg.id}")
    return key


async def get_deposit(did: str) -> dict | None:
    return await db.get(f"deposits/{_safe(did)}")


async def user_deposit_ids(uid: int) -> list[str]:
    idx = await db.get(f"idx/user_deposits/{uid}", {})
    return sorted(idx.keys(), reverse=True)


async def get_deposits(ids: list[str]) -> list[dict]:
    res = await asyncio.gather(*(db.get(f"deposits/{i}") for i in ids))
    return [r for r in res if r]


async def pending_count(uid: int) -> int:
    return int(await db.get(f"idx/pending/{uid}", 0))


async def resolve_deposit(did: str, approve: bool, admin_id: int, reason: str | None = None):
    """Atomically approve/reject a pending deposit. Raises AlreadyProcessed if not pending.
    Returns (deposit, new_balance)."""
    did = _safe(did)
    new = "approved" if approve else "rejected"

    def _flip(cur):
        if cur != "pending":
            raise AlreadyProcessed(cur)
        return new

    await db.txn(f"deposits/{did}/status", _flip)  # only ONE admin click can win
    dep = await db.get(f"deposits/{did}")
    uid = dep["user_id"]
    try:
        bal = await credit(uid, dep["amount"]) if approve else await get_balance(uid)
    except Exception:
        await db.put(f"deposits/{did}/status", "pending")  # roll back so it can be retried
        raise
    patch = {"reviewed_by": admin_id, "reviewed_at": now()}
    if reason:
        patch["reason"] = reason
    await db.update(f"deposits/{did}", patch)
    await db.txn(f"idx/pending/{uid}", lambda c: max(0, int(c or 0) - 1))
    if approve:
        spawn(bump_stats(earning=dep["amount"], deposits=1))
    dep.update(patch)
    dep["status"] = new
    return dep, bal


# ───────────── logs ─────────────
async def log_event(source: str, uid: int | None, action: str, detail: str = "") -> None:
    await db.push(f"logs/{source}", {"ts": now(), "uid": uid, "action": action, "detail": detail[:300]})


def log_bg(source: str, uid: int | None, action: str, detail: str = "") -> None:
    spawn(log_event(source, uid, action, detail))


# ───────────── engine settings ─────────────
async def get_safety() -> dict:
    from ..constants import DEFAULT_SAFETY
    return {**DEFAULT_SAFETY, **((await get_settings()).get("safety") or {})}


async def get_speed() -> dict:
    from ..constants import DEFAULT_SPEED
    return {**DEFAULT_SPEED, **((await get_settings()).get("speed") or {})}


async def get_apis() -> list[dict]:
    raw = (await get_settings()).get("apis") or {}
    return [{"id": int(k), "hash": v["hash"]} for k, v in raw.items() if isinstance(v, dict) and v.get("hash")]


# ───────────── one-time migration (Phase 1 -> 2 indexes) ─────────────
async def migrate() -> None:
    if await db.get("meta/migrated_v2"):
        return
    up: dict = {}
    for uid, u in (await db.get("users") or {}).items():
        up[f"idx/users/{uid}"] = u.get("joined_at", 0)
    for oid, o in (await db.get("orders") or {}).items():
        day = day_key(cfg.timezone, o.get("created_at"))
        up[f"idx/orders_by_day/{day}/{oid}"] = True
        up[f"idx/plan_orders/{o['service']}/{o.get('plan_id') or 'custom'}/{oid}"] = o["user_id"]
    for did, d in (await db.get("deposits") or {}).items():
        up[f"idx/deposits_by_day/{day_key(cfg.timezone, d.get('created_at'))}/{did}"] = True
    up["meta/migrated_v2"] = True
    await db.multi_update(up)
    log.info("migration v2 done (%d writes)", len(up))
